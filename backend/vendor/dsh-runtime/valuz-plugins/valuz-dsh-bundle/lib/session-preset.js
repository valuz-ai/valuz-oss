/**
 * valuz-session-preset — joins every SDK-created root agent to an agent preset.
 *
 * The dsh SDK server creates agents without preset composition (dsh
 * `sdk/server/src/server.ts::createSession`: "a deployment that configures a
 * roster has to join one here first"). Valuz sessions run on the same profile
 * as the dsh desktop/web host, whose model-facing rows (tools, skills, plan
 * mode…) live in agent presets, so without this join a session agent has no
 * tools. `agent/created` is a serial event awaited before `agents.create()`
 * returns, i.e. before the SDK server enqueues the first prompt — the only
 * window in which `agentPresets.select()` may still rebind the agent.
 *
 * Config: { preset?: string } — omitted = the registry default (`standard`
 * in the shipped web composition), so users' preset edits apply unchanged.
 */
export const name = "valuz-session-preset";
export const inject = ["agentPresets"];

export function apply(ctx, config = {}) {
  const requested = typeof config.preset === "string" && config.preset !== "" ? config.preset : undefined;
  ctx.on("agent/created", async ({ agent }) => {
    // Children compose from their parent's preset revision (subagent spawn).
    if (agent.session?.header?.parentSession !== undefined) return;
    if (ctx.agentPresets.composedPreset(agent.ctx) !== undefined) return;
    const preset = requested ?? (await ctx.agentPresets.resolve()).id;
    await ctx.agentPresets.select(agent, preset);
  });
}
