/**
 * valuz-session-tool-stubs — keeps the shipped subagent rows usable in
 * session children.
 *
 * The upstream presets give `subagent` / `subagent_fork` a `toolFilter.deny`
 * of the reminder tools (`schedule_*`). Those tools register only where the
 * Host `schedule` service resolves, and it hangs off the web connection chain
 * (connection → file-upload → session-controller → schedule) that the session
 * role switches off. dsh's `tools.restrict()` rejects names it does not know,
 * so every subagent spawn in a Valuz session failed with "names unknown
 * global tools".
 *
 * Restrictable names are every global and ancestor registration, whether or
 * not the viewing agent may see it (dsh-tools `view().restrictableNames`). So
 * this registers inert global tools under the denied names and denies them on
 * every agent as it is created (children join the preset beside their root,
 * not under it, so each needs its own deny; restrictions intersect, so a
 * child's own `toolFilter` still applies): no model ever sees them, and the
 * shipped filters resolve to known names.
 *
 * Only rows of the session role mount this; the manager has the real tools.
 * tests/runtimes/test_dsh_upstream_compat.py pins that every name a shipped
 * preset filters is either a real session tool or listed here.
 */
export const name = "valuz-session-tool-stubs";
export const inject = ["tools"];

export const STUBBED_TOOLS = ["schedule_create", "schedule_delete", "schedule_list", "schedule_update"];

const UNAVAILABLE = "Reminders are not available in Valuz sessions.";

function stub(toolName) {
  return {
    name: toolName,
    description: UNAVAILABLE,
    parameters: { type: "object", properties: {} },
    output: { schema: { type: "string" }, render: (_args, value) => [{ type: "text", text: value }] },
    async execute() {
      return UNAVAILABLE;
    },
  };
}

export function apply(ctx) {
  for (const toolName of STUBBED_TOOLS) ctx.tools.register(stub(toolName));
  ctx.on("agent/created", ({ agent }) => {
    agent.ctx.tools.restrict({ deny: STUBBED_TOOLS });
  });
}
