import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { MemberWithAgent, SkillView } from "@valuz/core";

/** The composer config hook reaches for the whole runtime (targets, provider
 *  channels, the agent library). None of that decides which skills ``/``
 *  offers, so stub it down to inert values and let the roster + catalog — the
 *  two inputs under test — do the talking. */
vi.mock("@valuz/core", () => ({
  getDefaultExecutionTarget: () => ({ id: "local", name: "local" }),
  useDefaultRuntimeLocation: () => "local",
  useEntityOrigin: () => undefined,
  useComposerProviderChannelState: () => ({ providers: [], pending: false }),
  useComposerAgentLibrary: () => ({
    agents: [],
    loaded: true,
    failed: false,
    settling: false,
    refresh: () => undefined,
  }),
  useComposerProviders: () => [],
  useTranslation: () => ({ t: (key: string) => key }),
  RUNTIME_DISPLAY_NAME: {},
}));

const { useComposerConfig } = await import("./useComposerConfig");

const skill = (over: Partial<SkillView> & { slug: string }): SkillView =>
  ({
    id: over.slug,
    name: over.slug,
    description: "",
    scope: "user",
    source: "user",
    path: `/skills/${over.slug}`,
    enabled: true,
    tags: [],
    deletable: true,
    ...over,
  }) as SkillView;

const member = (
  slug: string,
  agent: Partial<MemberWithAgent["agent"]>,
): MemberWithAgent =>
  ({
    member: {
      id: slug,
      project_id: "p1",
      agent_slug: slug,
      source_agent_slug: slug,
    },
    agent: {
      id: `agent:${slug}`,
      name: slug,
      model: "claude-sonnet-4-6",
      runtime_provider: "claude_agent",
      instructions: "",
      skills: [],
      connectors: [],
      provider_id: null,
      effort: null,
      ...agent,
    },
  }) as MemberWithAgent;

/** A project conversation on ``agentSlug``, with ``catalog`` as the project's
 *  skill catalog. Everything else is the inert minimum the hook needs. */
function renderForProject(params: {
  projectAgents: MemberWithAgent[];
  agentSlug: string;
  catalog: SkillView[];
}) {
  /* eslint-disable @typescript-eslint/no-explicit-any */
  return renderHook(() =>
    useComposerConfig({
      id: "s1",
      isNewSession: false,
      projects: [{ id: "p1", name: "P", kind: "project" } as any],
      selectedProjectId: "p1",
      selectedSession: null,
      activeProject: { id: "p1", name: "P", kind: "project" } as any,
      executionTargets: [],
      execTargetId: "local",
      pendingUserMessage: null,
      selectedRuntimeId: null,
      runtimeList: [],
      managedRuntimeSetup: false,
      channelsPending: false,
      projectAgents: params.projectAgents,
      agentParam: null,
      agentLibraryRevision: 0,
      selectedAgentSlug: params.agentSlug,
      effectiveAgentSlug: params.agentSlug,
      availableSkills: params.catalog,
      projectSkills: [],
    } as any),
  );
  /* eslint-enable @typescript-eslint/no-explicit-any */
}

describe("project conversation ``/`` skill list", () => {
  const CATALOG = [
    skill({ slug: "stock-analysis", library_enabled: true }),
    skill({ slug: "dcf", library_enabled: true }),
    skill({ slug: "switched-off", library_enabled: false }),
  ];

  it("offers the library to an all_available agent that binds nothing", () => {
    // The regression: Valurion reports ``skills: []`` by design (its real set
    // is resolved from the owner's library when the session is created), and
    // the picker used to read that array literally — so every project chat on
    // the built-in agent got an empty ``/`` while the switched-on skills were
    // loaded and usable.
    const { result } = renderForProject({
      projectAgents: [
        member("valurion", { skills: [], resource_policy: "all_available" }),
      ],
      agentSlug: "valurion",
      catalog: CATALOG,
    });

    expect(result.current.selectedAgentSkillItems.map((i) => i.slug)).toEqual([
      "stock-analysis",
      "dcf",
    ]);
  });

  it("still shows only what an explicit agent bound", () => {
    const { result } = renderForProject({
      projectAgents: [
        member("analyst", { skills: ["dcf"], resource_policy: "explicit" }),
      ],
      agentSlug: "analyst",
      catalog: CATALOG,
    });

    expect(result.current.selectedAgentSkillItems.map((i) => i.slug)).toEqual([
      "dcf",
    ]);
  });

  it("leaves an explicit agent that bound nothing with an empty picker", () => {
    // "Bound to nothing" and "bound to everything" must stay distinguishable;
    // only the policy separates them.
    const { result } = renderForProject({
      projectAgents: [
        member("bare", { skills: [], resource_policy: "explicit" }),
      ],
      agentSlug: "bare",
      catalog: CATALOG,
    });

    expect(result.current.selectedAgentSkillItems).toEqual([]);
  });

  it("reads a member from a backend that predates the field as explicit", () => {
    const { result } = renderForProject({
      projectAgents: [member("legacy", { skills: ["dcf"] })],
      agentSlug: "legacy",
      catalog: CATALOG,
    });

    expect(result.current.selectedAgentSkillItems.map((i) => i.slug)).toEqual([
      "dcf",
    ]);
  });
});
