import {
  getDefaultExecutionTarget,
  getExecutionTargets,
  type Agent,
} from "@valuz/core";

interface AgentSyncInfo {
  status?: string;
}

/** Cloud-only organization resources are catalog entries, not local targets. */
export function isCloudOnlyResource(resource: unknown): boolean {
  if (!resource || typeof resource !== "object") return false;
  const outer = resource as Record<string, unknown>;
  const target =
    outer.kind === "installed" && outer.item && typeof outer.item === "object"
      ? (outer.item as Record<string, unknown>)
      : outer;
  const sync = (target._org_sync ?? target._sync) as AgentSyncInfo | undefined;
  return sync?.status === "cloud_only";
}

/** Cloud-only organization Agents are catalog entries, not local edit targets. */
export function isCloudOnlyAgent(agent: Agent): boolean {
  return isCloudOnlyResource(agent);
}

/**
 * The agent lives on another machine — a desktop this account controls, or a
 * colleague's host reached through a share.
 *
 * The library lists it so "what can I run" is the union across every target
 * (picking it in the composer moves the conversation there), but it is not a
 * local edit target: its instructions, skills and connectors live on that
 * machine, and the local backend has never heard of its slug.
 */
export function runsOnAnotherTarget(agent: Agent): boolean {
  const target = agent.exec_target_id;
  if (!target) return false;
  return target !== (getDefaultExecutionTarget()?.id ?? "local");
}

/**
 * Where an agent row came from, which is what the library groups by:
 *
 * - ``local``  — this machine's own library (or a catalog entry with no home).
 * - ``remote`` — a machine you may run on freely: another of your desktops.
 * - ``shared`` — a host that lent you this ONE agent. Its target is
 *   registered (the location chip has to name it) but not selectable.
 *
 * Falls back to the cloud-only marker when the target is not registered yet:
 * during a cold start a share would otherwise be filed as one of your own
 * machines for a moment.
 */
export function agentTargetKind(agent: Agent): "local" | "remote" | "shared" {
  if (!runsOnAnotherTarget(agent)) return "local";
  const target = getExecutionTargets().find(
    (t) => t.id === agent.exec_target_id,
  );
  if (target) return target.selectable === false ? "shared" : "remote";
  return isCloudOnlyAgent(agent) ? "shared" : "remote";
}

/**
 * Identity of a LIST ROW, which a slug alone is not: two machines may each
 * have an "sde" and both are listed. Anything that dedupes, keys or selects
 * rows has to use this — dedupe by slug silently drops the second machine's
 * copy.
 */
export function agentRowId(agent: Agent): string {
  return `${agent.exec_target_id ?? "local"}:${agent.slug}`;
}

/** Neither editable nor openable here: a catalog entry, or another machine's. */
export function isRemoteAgentRow(agent: Agent): boolean {
  return isCloudOnlyAgent(agent) || runsOnAnotherTarget(agent);
}

/** Built-in runtime agent whose managed identity and resources are immutable. */
export function isSystemAgent(agent: Agent): boolean {
  return agent.kind === "system";
}

/** Keep the canonical system Agent ahead of every portable Agent. */
export function compareAgentsWithValurionFirst(a: Agent, b: Agent): number {
  const valurionPriority =
    Number(b.slug === "valurion") - Number(a.slug === "valurion");
  return valurionPriority || a.name.localeCompare(b.name);
}
