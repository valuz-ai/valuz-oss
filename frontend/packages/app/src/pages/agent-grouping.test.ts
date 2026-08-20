/**
 * What the agent library actually shows, per group.
 *
 * Pinned with the real pieces — buildAgentCategories' filters and the list's
 * bucketing — against the shape a controlled desktop produces: every built-in
 * exists on BOTH machines under the same slug.
 */
import { describe, expect, it } from "vitest";
import { bucketByCategory } from "@valuz/ui";
import { setExecutionTargets } from "@valuz/core";
import type { Agent } from "@valuz/core";

import { buildAgentCategories } from "./AgentsPage";
import { agentRowId } from "./agent-list-state";

const t = ((key: string) => key) as never;

function agent(partial: Partial<Agent> & { slug: string }): Agent {
  return {
    name: partial.slug,
    description: "",
    instructions: "",
    runtime: "claude_agent",
    model: "",
    skills: [],
    connector_types: [],
    knowledge_scope: [],
    provider_id: null,
    effort: null,
    kind: "standard",
    resource_policy: "explicit",
    inherit_global_instructions: false,
    permission_mode: "",
    source: "valuz",
    readonly: false,
    deletable: true,
    avatar: null,
    ...partial,
  } as Agent;
}

const LOCAL_TARGET = {
  id: "local",
  labelKey: "local",
  baseUrl: "",
  isDefault: true,
};
const HOST_TARGET = {
  id: "device:d1",
  labelKey: "d1",
  baseUrl: "https://relay/proxy",
};
const SHARE_TARGET = {
  id: "device:d2",
  labelKey: "d2",
  baseUrl: "https://relay/proxy2",
  selectable: false,
};

describe("agent library grouping", () => {
  it("keeps a machine's namesake instead of dropping it as a duplicate", () => {
    // Every machine ships the same built-ins. Dedupe on slug — which the list
    // did — made the remote copies vanish and the 远程 group come up empty.
    setExecutionTargets([LOCAL_TARGET, HOST_TARGET, SHARE_TARGET]);
    const items = [
      agent({ slug: "valurion", kind: "system" }),
      agent({ slug: "sde" }),
      agent({ slug: "valurion", kind: "system", exec_target_id: "device:d1" }),
      agent({ slug: "sde", exec_target_id: "device:d1" }),
      agent({ slug: "shared:abc", exec_target_id: "device:d2" }),
    ];

    const buckets = bucketByCategory(items, buildAgentCategories(t), agentRowId);

    expect(
      buckets.map(({ category, items: rows }) => [
        category.id,
        rows.map((r) => r.slug),
      ]),
    ).toEqual([
      ["system", ["valurion"]],
      ["custom", ["sde"]],
      ["remote", ["valurion", "sde"]],
      ["shared", ["shared:abc"]],
    ]);
  });

  it("files an opened agent under 开放, not 远程", () => {
    // Told apart by the grant on the target: a share's target is registered so
    // the location chip can name it, but it is never selectable.
    setExecutionTargets([LOCAL_TARGET, SHARE_TARGET]);
    const buckets = bucketByCategory(
      [agent({ slug: "shared:abc", exec_target_id: "device:d2" })],
      buildAgentCategories(t),
      agentRowId,
    );
    expect(buckets.map((b) => b.category.id)).toEqual(["shared"]);
  });

  it("puts nothing in a remote group when only this machine answers", () => {
    setExecutionTargets([LOCAL_TARGET]);
    const buckets = bucketByCategory(
      [agent({ slug: "sde" }), agent({ slug: "valurion", kind: "system" })],
      buildAgentCategories(t),
      agentRowId,
    );
    expect(buckets.map((b) => b.category.id)).toEqual(["system", "custom"]);
  });
});
