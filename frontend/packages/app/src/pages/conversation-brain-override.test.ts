import { describe, expect, it } from "vitest";
import { resolveBrainOverride } from "./conversation-brain-override";

// The composer state as it looks mid-handoff: the picks still hold the
// project's last-used channel / the global default, because the member roster
// that feeds ``selectedAgentBrain`` has not landed yet.
const STALE_PICKS = {
  providerId: "claude-subscription-row",
  modelId: "claude-fable-5",
  runtimeId: "claude_agent" as const,
  effort: "medium" as const,
};

describe("resolveBrainOverride", () => {
  it("sends nothing while an agent is bound and the user has not picked", () => {
    // The regression this exists for: an agent pinned to MiniMax was minted on
    // the project's previously-used Claude subscription, whose OAuth token had
    // been revoked — so every send 401'd, while the same agent worked fine in
    // a standalone conversation.
    expect(
      resolveBrainOverride({
        agentSlug: "valurion",
        composerTouched: false,
        ...STALE_PICKS,
      }),
    ).toEqual({});
  });

  it("sends the user's pick once they actually change the composer", () => {
    expect(
      resolveBrainOverride({
        agentSlug: "valurion",
        composerTouched: true,
        ...STALE_PICKS,
      }),
    ).toEqual({
      provider_id: "claude-subscription-row",
      model_id: "claude-fable-5",
      runtime_id: "claude_agent",
      effort: "medium",
    });
  });

  it("always sends for an agentless chat — there is no brain to inherit", () => {
    expect(
      resolveBrainOverride({
        agentSlug: null,
        composerTouched: false,
        ...STALE_PICKS,
      }),
    ).toEqual({
      provider_id: "claude-subscription-row",
      model_id: "claude-fable-5",
      runtime_id: "claude_agent",
      effort: "medium",
    });
  });

  it("withholds the runtime unless a full (provider, model) pick came with it", () => {
    // Runtime alone would re-point the session at a runtime the resolved
    // channel may not serve.
    expect(
      resolveBrainOverride({
        agentSlug: null,
        composerTouched: false,
        ...STALE_PICKS,
        modelId: null,
      }).runtime_id,
    ).toBeUndefined();
  });

  it("maps absent picks to undefined so they drop out of the request body", () => {
    expect(
      resolveBrainOverride({
        agentSlug: null,
        composerTouched: true,
        providerId: null,
        modelId: null,
        runtimeId: null,
        effort: null,
      }),
    ).toEqual({
      provider_id: undefined,
      model_id: undefined,
      runtime_id: undefined,
      effort: null,
    });
  });
});
