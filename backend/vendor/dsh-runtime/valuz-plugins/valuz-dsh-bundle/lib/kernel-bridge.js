/**
 * valuz-kernel-bridge — the Valuz kernel's in-process seam inside a dsh
 * session child.
 *
 * The dsh SDK JSON-RPC wire carries prompts and session events only; plan
 * state, user questions and tool approvals are in-process plugin seams (the
 * dsh web host answers them over its own Remote API). This plugin is the
 * Valuz counterpart, inserted by the per-session patch the kernel writes:
 *
 * 1. Valuz instructions. The kernel session's instructions (agent prompt +
 *    project context) become one system-prompt section right after the
 *    deployment persona — the preset's persona and tools stay dsh's own.
 *
 * 2. Plan-state convergence. The kernel's `Session.mode` is authoritative;
 *    `planActive` is converged ONCE per session on the first `agent/pre-step`
 *    (an approved `exit_plan_mode` flips dsh state mid-turn — a per-step
 *    converge would fight it). Plan mode lives inside the agent's preset group
 *    since dsh 0.2, so the service is read per agent through `agentPresets`.
 *
 * 3. User questions. Answers the `user-questions/request` waterfall
 *    (`exit_plan_mode` plan reviews, `ask_user_question` batches) by parking
 *    the batch on the kernel's user-questions endpoint as a standard
 *    `requires_action` card. Long-poll: POST /ask registers, GET
 *    /ask/{id}?wait_seconds=N blocks server-side until decided.
 *
 * 4. Tool approvals. Answers the `approval/request` waterfall the same way —
 *    one single-select question "Allow once" / "Deny" — so a session running
 *    under dsh's workspace-write preset asks the Valuz user instead of failing
 *    closed. Any bridge failure answers `rejected` (dsh doctrine: fail closed).
 *
 * Config (validated fail-loud): { instructions?, planActive?,
 * userQuestionsEndpoint? }. Omitting a key disables that half.
 */

export const name = "valuz-kernel-bridge";

const KNOWN_KEYS = ["instructions", "planActive", "userQuestionsEndpoint"];
const POLL_WAIT_SECONDS = 25;
const ALLOW_LABEL = "Allow once";
const DENY_LABEL = "Deny";

export function apply(ctx, config = {}) {
  const unknown = Object.keys(config).filter((key) => !KNOWN_KEYS.includes(key));
  if (unknown.length > 0) {
    throw new Error(
      `valuz-kernel-bridge config has unknown key(s) ${unknown.join(", ")} — ` +
        "config is { instructions?, planActive?, userQuestionsEndpoint? }",
    );
  }
  const { instructions, planActive, userQuestionsEndpoint: endpoint } = config;
  if (instructions !== undefined && typeof instructions !== "string") {
    throw new Error("valuz-kernel-bridge `instructions` must be a string when present");
  }
  if (planActive !== undefined && typeof planActive !== "boolean") {
    throw new Error("valuz-kernel-bridge `planActive` must be a boolean when present");
  }
  if (endpoint !== undefined && (typeof endpoint !== "string" || endpoint.trim() === "")) {
    throw new Error(
      "valuz-kernel-bridge `userQuestionsEndpoint` must be a non-empty string when present",
    );
  }

  if (typeof instructions === "string" && instructions.trim() !== "") {
    ctx.inject(["systemPrompt"], (promptCtx) => {
      const prompt = promptCtx.systemPrompt;
      promptCtx.effect(() =>
        prompt.section({
          name: "valuz:instructions",
          order: prompt.getSectionOrder("DEPLOYMENT_PERSONA_PREFIX") + 1,
          text: instructions,
        }),
      );
    });
  }

  if (typeof planActive === "boolean") {
    const converged = new WeakSet();
    ctx.on("agent/pre-step", ({ agent }, next) => {
      const session = agent.session;
      if (!converged.has(session)) {
        converged.add(session);
        const planMode =
          ctx.get("agentPresets")?.serviceFor(agent, "planMode") ?? ctx.get("planMode");
        try {
          planMode?.set(agent, planActive);
        } catch (error) {
          ctx.logger.warn("valuz-kernel-bridge: plan-state converge failed: %o", error);
        }
      }
      return next();
    });
  }

  if (typeof endpoint === "string") {
    const base = endpoint.replace(/\/+$/, "");
    ctx.on("user-questions/request", (request) => askKernel(base, request.questions, request.signal));
    ctx.on("approval/request", async (request) => {
      const detail =
        request.displayReason?.en ?? request.reason ?? `The agent wants to run ${request.toolName}.`;
      try {
        const answer = await askKernel(
          base,
          [
            {
              id: "approval",
              header: request.toolName,
              question: `Allow ${request.toolName}?`,
              detail,
              options: [{ label: ALLOW_LABEL }, { label: DENY_LABEL }],
              multiSelect: false,
            },
          ],
          request.signal,
        );
        const selected = answer.answers?.[0]?.selected ?? [];
        return selected.includes(ALLOW_LABEL) ? "allowed-once" : "rejected";
      } catch (error) {
        if (request.signal?.aborted) return "cancelled";
        ctx.logger.warn("valuz-kernel-bridge: approval forward failed: %o", error);
        return "rejected";
      }
    });
  }
}

/** Park one question batch on the kernel and long-poll until it is decided. */
async function askKernel(base, questions, signal) {
  const body = {
    questions: questions.map((q) => ({
      id: q.id,
      question: q.question,
      ...(q.header !== undefined ? { header: q.header } : {}),
      ...(q.detail !== undefined ? { detail: q.detail } : {}),
      ...(q.options !== undefined ? { options: q.options } : {}),
      ...(q.multiSelect !== undefined ? { multiSelect: q.multiSelect } : {}),
      ...(q.intent !== undefined ? { intent: q.intent } : {}),
    })),
  };
  const started = await fetchJson(`${base}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  const askId = started.ask_id;
  if (typeof askId !== "string" || askId === "") {
    throw new Error("the kernel user-questions bridge returned no ask_id");
  }
  for (;;) {
    throwIfAborted(signal);
    const state = await fetchJson(
      `${base}/ask/${encodeURIComponent(askId)}?wait_seconds=${POLL_WAIT_SECONDS}`,
      { method: "GET", signal },
    );
    if (state.status === "pending") continue;
    if (state.status === "answered" && state.answer && Array.isArray(state.answer.answers)) {
      return state.answer;
    }
    throw new Error(
      typeof state.message === "string" && state.message !== ""
        ? state.message
        : "the kernel user-questions bridge returned an unusable state",
    );
  }
}

async function fetchJson(url, init) {
  let response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    if (init.signal?.aborted) {
      throw new Error("the request was aborted before the user answered");
    }
    throw new Error(`the kernel user-questions bridge is unreachable: ${error?.message ?? error}`);
  }
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(
      `the kernel user-questions bridge answered HTTP ${response.status}` +
        (text ? `: ${text.slice(0, 300)}` : ""),
    );
  }
  return response.json();
}

function throwIfAborted(signal) {
  if (signal?.aborted) {
    throw new Error("the request was aborted before the user answered");
  }
}
