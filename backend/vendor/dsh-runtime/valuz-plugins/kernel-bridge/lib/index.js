/**
 * valuz-dsh-kernel-bridge — the Valuz kernel's in-process seam inside the
 * dsh subprocess.
 *
 * The dsh SDK JSON-RPC wire has no plan-mode or user-questions channel
 * (verified at 0.1.1-rc.2: the protocol is still exactly session/prompt +
 * session/created + session/event + agent/status + subagent/end), and both
 * `ctx.planMode.set(...)` and `ctx.userQuestions.registerProvider(...)` are
 * in-process plugin APIs — the dsh Web host wires them the same way. This
 * plugin is the Valuz counterpart:
 *
 * 1. Plan-state convergence. The kernel's `Session.mode` is authoritative;
 *    the composition bakes the desired state into `config.planActive` and
 *    this plugin converges the dsh-side logged state ONCE per session on the
 *    first `agent/pre-step` (the plan-mode controller applies the pending
 *    selection in that same pre-step, so the very first request already
 *    carries the plan section). Converging once — not per step — is
 *    load-bearing: an approved `exit_plan_mode` flips dsh-side state
 *    mid-turn, and a per-step converge would fight it and re-enter plan.
 *
 * 2. User-questions provider. Forwards `ask()` (the `exit_plan_mode`
 *    plan-review question and `ask_user_question` clarifying batches) to the
 *    kernel's user-questions endpoint, where it parks as a standard
 *    `requires_action` approval. Long-poll loop: POST /ask registers the
 *    park, GET /ask/{id}?wait_seconds=N blocks server-side until decided
 *    (bounded so no single HTTP request outlives client timeouts).
 *
 * Config (validated fail-loud, dsh doctrine):
 *   { planActive?: boolean, userQuestionsEndpoint?: string }
 * Omitting a key disables that half of the bridge.
 */

export const name = "valuz-kernel-bridge";
export const inject = [];

const KNOWN_KEYS = ["planActive", "userQuestionsEndpoint"];
const POLL_WAIT_SECONDS = 25;

export function apply(ctx, config = {}) {
  const unknown = Object.keys(config).filter((key) => !KNOWN_KEYS.includes(key));
  if (unknown.length > 0) {
    throw new Error(
      `valuz-kernel-bridge config has unknown key(s) ${unknown.join(", ")} — ` +
        "config is { planActive?, userQuestionsEndpoint? }",
    );
  }
  const planActive = config.planActive;
  if (planActive !== undefined && typeof planActive !== "boolean") {
    throw new Error("valuz-kernel-bridge `planActive` must be a boolean when present");
  }
  const endpoint = config.userQuestionsEndpoint;
  if (endpoint !== undefined && (typeof endpoint !== "string" || endpoint.trim() === "")) {
    throw new Error(
      "valuz-kernel-bridge `userQuestionsEndpoint` must be a non-empty string when present",
    );
  }

  if (typeof planActive === "boolean") {
    ctx.inject(["planMode"], (planCtx) => {
      const converged = new WeakSet();
      planCtx.on("agent/pre-step", ({ agent }, next) => {
        const session = agent.session;
        if (!converged.has(session)) {
          converged.add(session);
          try {
            planCtx.planMode.set(agent, planActive);
          } catch (error) {
            planCtx.logger.warn(
              "valuz-kernel-bridge: plan-state converge failed: %o",
              error,
            );
          }
        }
        return next();
      });
    });
  }

  if (typeof endpoint === "string") {
    const base = endpoint.replace(/\/+$/, "");
    ctx.inject(["userQuestions"], (qCtx) => {
      qCtx.userQuestions.registerProvider({
        ask: (request) => askKernel(base, request),
      });
    });
  }
}

/** Forward one ask() to the kernel and long-poll until it is decided. */
async function askKernel(base, request) {
  const signal = request.signal;
  const body = {
    questions: request.questions.map((q) => ({
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
      throw new Error("the user-questions request was aborted before the user answered");
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
    throw new Error("the user-questions request was aborted before the user answered");
  }
}
