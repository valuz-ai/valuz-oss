import { describe, expect, it } from "vitest";
import type { Agent } from "@valuz/core";
import { getAgentCopyDefaults } from "./agent-copy-defaults";

function agent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agent-1",
    slug: "researcher",
    name: "Researcher",
    description: "Research deeply",
    instructions: "Verify every source",
    runtime: "codex",
    model: "gpt-5",
    skills: ["/skills/research"],
    connector_types: ["github"],
    knowledge_scope: ["kb-1"],
    provider_id: "provider-1",
    effort: "xhigh",
    kind: "standard",
    resource_policy: "explicit",
    inherit_global_instructions: false,
    permission_mode: "full_access",
    source: "user",
    readonly: false,
    deletable: true,
    avatar: "sparkles",
    ...overrides,
  };
}

describe("getAgentCopyDefaults", () => {
  it("deep-copies a portable standard agent", () => {
    const source = agent();
    const copy = getAgentCopyDefaults(source);

    expect(copy).toMatchObject({
      tagline: source.description,
      avatar: source.avatar,
      model: {
        runtime: source.runtime,
        providerId: source.provider_id,
        model: source.model,
      },
      effort: source.effort,
      instructions: source.instructions,
      skills: source.skills,
      connectors: source.connector_types,
      knowledgeScope: source.knowledge_scope,
      inheritValurionInstructions: false,
    });
    expect(copy.skills).not.toBe(source.skills);
  });

  it("copies only Valurion brain settings into an inheriting empty agent", () => {
    const copy = getAgentCopyDefaults(
      agent({
        slug: "valurion",
        kind: "system",
        resource_policy: "all_available",
      }),
    );

    expect(copy).toMatchObject({
      tagline: "",
      avatar: null,
      model: {
        runtime: "codex",
        providerId: null,
        model: "gpt-5",
      },
      effort: "xhigh",
      instructions: "",
      skills: [],
      connectors: [],
      knowledgeScope: [],
      inheritValurionInstructions: true,
    });
  });
});
