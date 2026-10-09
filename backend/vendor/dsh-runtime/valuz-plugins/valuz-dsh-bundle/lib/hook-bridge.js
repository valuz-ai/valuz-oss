/**
 * valuz-hook-bridge — dsh's built-in tools on the Valuz hook bus.
 *
 * The chain runs in the Valuz kernel (src/core/hooks/remote.py); this plugin
 * is the dsh side of that conversation. It is armed by the per-session patch
 * only while a Valuz handler listens, so a session without hooks runs exactly
 * as before.
 *
 * tool.call — dsh splits a call the same way Claude's SDK does, so the
 *   bridge does what the Claude adapter does:
 *   - `tools/pre-execute` starts the chain. When it reaches core ("run the
 *     tool") the call proceeds; when a handler answers instead, the call is
 *     denied with the handler's answer as the reason (the model reads it).
 *     dsh excludes input rewriting by design, so a rewritten input is not
 *     applied.
 *   - `tools/post-execute` feeds the result to the waiting chain and returns
 *     what the handlers made of it (accept / replace content / block).
 *   - `tools/result` finishes chains of calls that never ran (denied,
 *     cancelled).
 * tool.check — wraps dsh's own pre-execute decision (`next()`); a handler
 *   can deny, or (Valuz's own tier) allow without asking.
 *
 * MCP tools (`mcp__*`, which includes the kernel toolkit): their tool.call is
 * dispatched by the kernel's MCP proxy / toolkit endpoint, so it is skipped
 * here; their tool.check still wraps dsh's own decision (nothing else sees
 * the approval step for them).
 *
 * Any bridge failure lets dsh proceed as if the bridge were absent
 * (fail-open, the bus's default policy).
 *
 * Config: { endpoint, events: ["tool.call", "tool.check"] }.
 */

// valuz-required-guard-v1: a monotonic execution guard follows the waterfall.
export const name = "valuz-hook-bridge";
export const inject = ["tools"];

const KNOWN_KEYS = ["endpoint", "events", "required"];

export function apply(ctx, config = {}) {
  const unknown = Object.keys(config).filter((key) => !KNOWN_KEYS.includes(key));
  if (unknown.length > 0) {
    throw new Error(
      `valuz-hook-bridge config has unknown key(s) ${unknown.join(", ")} — ` +
        "config is { endpoint, events }",
    );
  }
  const { endpoint, events, required = false } = config;
  if (typeof required !== "boolean") throw new Error("required must be boolean");
  if (endpoint === undefined) return; // not armed for this session
  if (typeof endpoint !== "string" || endpoint.trim() === "") {
    throw new Error("valuz-hook-bridge `endpoint` must be a non-empty string");
  }
  if (!Array.isArray(events) || events.some((event) => typeof event !== "string")) {
    throw new Error("valuz-hook-bridge `events` must be an array of event names");
  }
  const base = endpoint.replace(/\/+$/, "");
  const wantsCall = events.includes("tool.call");
  const wantsCheck = events.includes("tool.check");
  if (!wantsCall && !wantsCheck) return;

  // execution key -> dispatch id of a tool.call chain waiting for the result
  const waiting = new Map();
  const keyOf = (exec) => String(exec.token ?? exec.callId ?? "");
  const isMcp = (exec) => exec.name.startsWith("mcp__");
  const checked = new Map();
  const signature = (exec) => JSON.stringify([exec.name, plain(exec.arguments)]);
  if (required) {
    if (typeof ctx.tools?.guard !== "function") throw new Error("required monotonic tools guard unavailable");
    ctx.tools.guard((exec) => {
      if (isMcp(exec)) return; // the mandatory MCP proxy owns the actual upstream boundary
      const key = keyOf(exec);
      const expected = checked.get(key);
      checked.delete(key);
      if (expected !== signature(exec)) return "required execution guard did not approve these final parameters";
    });
  }

  ctx.on("tools/pre-execute", async (exec, next) => {
    if (typeof exec.name !== "string") return required ? { kind: "deny", reason: "required tool identity unavailable" } : next();
    const input = plain(exec.arguments);
    if (wantsCall && !isMcp(exec)) {
      let step;
      try {
        step = await post(`${base}/dispatch`, {
          event: "tool.call",
          payload: { name: exec.name, input, tool_use_id: exec.callId ?? null },
        });
      } catch (error) {
        ctx.logger.warn("valuz-hook-bridge: tool.call start failed: %o", error);
        if (required) return { kind: "deny", reason: "required execution guard unavailable" };
        step = null;
      }
      if (step && step.op === "done" && step.result) {
        // A handler answered without running the tool.
        return { kind: "deny", reason: contentText(step.result.content) };
      }
      if (step && step.op === "core") {
        if (required && (typeof step.id !== "string" || !sameJson(step.data?.input, input))) return { kind: "deny", reason: "required execution guard cannot safely apply rewritten parameters" };
        waiting.set(keyOf(exec), step.id);
      }
      else if (required) return { kind: "deny", reason: "required execution guard returned an invalid decision" };
    }
    const result = wantsCheck ? await check(exec, input, next) : await next();
    if (required && result?.kind !== "deny") checked.set(keyOf(exec), signature(exec));
    return result;
  });

  async function check(exec, input, next) {
    let own;
    const ownDecision = async () => (own ??= await next());
    try {
      let step = await post(`${base}/dispatch`, {
        event: "tool.check",
        payload: { name: exec.name, input, tool_use_id: exec.callId ?? null },
      });
      while (step.op === "core") {
        step = await post(`${base}/dispatch/${step.id}`, {
          result: decisionToWire(await ownDecision()),
        });
      }
      if (step.op === "done" && step.result) return decisionFromWire(step.result, own);
    } catch (error) {
      ctx.logger.warn("valuz-hook-bridge: tool.check failed: %o", error);
    }
    return required ? { kind: "deny", reason: "required execution guard unavailable" } : ownDecision();
  }

  if (wantsCall) {
    ctx.on("tools/post-execute", async (exec, result, next) => {
      const id = waiting.get(keyOf(exec));
      if (id === undefined) return next();
      waiting.delete(keyOf(exec));
      const seen = { content: plain(result.content), is_error: Boolean(result.isError) };
      let step;
      try {
        step = await post(`${base}/dispatch/${id}`, { result: seen });
        while (step.op === "core") {
          // dsh runs a tool once per call — a handler's second next() gets
          // an error (its first result already reached it).
          step = await post(`${base}/dispatch/${step.id}`, {
            error: "dsh runs a built-in tool once per call",
          });
        }
      } catch (error) {
        ctx.logger.warn("valuz-hook-bridge: tool.call result failed: %o", error);
        return next();
      }
      if (step.op !== "done" || !step.result) return next();
      const out = step.result;
      if (out.is_error === seen.is_error && sameJson(out.content, seen.content)) return next();
      const blocks = toBlocks(out.content);
      if (out.is_error && !seen.is_error) return { kind: "block", feedback: blocks };
      return { kind: "accept", content: blocks };
    });

    ctx.on("tools/result", (exec, result) => {
      // Calls that never reached post-execute (denied, cancelled): finish
      // their chains so handlers see how they ended.
      const id = waiting.get(keyOf(exec));
      if (id === undefined) return;
      waiting.delete(keyOf(exec));
      post(`${base}/dispatch/${id}`, {
        result: {
          content: plain(result?.content ?? []),
          is_error: true,
          executed: false,
        },
      }).catch((error) => {
        ctx.logger.warn("valuz-hook-bridge: finishing an unexecuted call failed: %o", error);
      });
    });
  }
}

function decisionToWire(decision) {
  switch (decision?.kind) {
    case "allow":
      return { behavior: "allow" };
    case "ask":
      return { behavior: "ask", reason: decision.reason ?? null };
    default:
      return { behavior: "deny", reason: decision?.reason ?? null };
  }
}

function decisionFromWire(wire, own) {
  if (wire.behavior === "allow") return { kind: "allow" };
  if (wire.behavior === "ask") {
    return own?.kind === "ask" ? own : { kind: "ask", reason: wire.reason ?? undefined };
  }
  if (own?.kind === "deny" && (wire.reason ?? null) === (own.reason ?? null)) return own;
  return { kind: "deny", reason: wire.reason || "denied by a Valuz hook" };
}

function contentText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const texts = content
      .filter((block) => block && block.type === "text" && typeof block.text === "string")
      .map((block) => block.text);
    if (texts.length > 0) return texts.join("\n");
  }
  return JSON.stringify(content ?? "");
}

function toBlocks(content) {
  if (Array.isArray(content)) return content;
  return [{ type: "text", text: typeof content === "string" ? content : JSON.stringify(content) }];
}

function plain(value) {
  return value === undefined ? null : JSON.parse(JSON.stringify(value));
}

function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`hook bridge answered HTTP ${response.status}${text ? `: ${text.slice(0, 300)}` : ""}`);
  }
  return response.json();
}
