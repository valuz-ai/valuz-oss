import { useCallback, useMemo } from "react";
import {
  getDefaultExecutionTarget,
  useEntityOrigin,
  useComposerProviderChannelState,
  useComposerAgentLibrary,
  useComposerProviders,
  type ExecutionTarget,
  type MemberWithAgent,
  type ProjectDetail,
  type ProjectListItem,
  type RuntimeId,
  type RuntimeListItem,
  type SessionListItem,
  type SkillView,
} from "@valuz/core";
import {
  type ComposerAgentItem,
  type RuntimeStartLocation,
} from "@valuz/ui";
import { modelLabel } from "@valuz/shared";
import {
  resolveAgentSkillItems,
  type AgentSkillItem,
} from "../../lib/agent-skill-items";
import { NEW_SESSION_ID } from "./session-events";

type ComposerConfigParams = {
  /** Route param (``/conversation/{id}``), defaulted to ``NEW_SESSION_ID``. */
  id: string;
  isNewSession: boolean;
  projects: ProjectListItem[];
  selectedProjectId: string | null;
  selectedSession: SessionListItem | null;
  activeProject: ProjectDetail | null;
  executionTargets: ExecutionTarget[];
  execTargetId: string | null;
  /** Read for truthiness only — the runtime-startup phase flag (set from
   *  Send until the kernel echoes ``message.user``). */
  pendingUserMessage: {
    text: string;
    attachments: Array<{ name: string; size: number }>;
    fromSeq: number;
    sentAt: number; // Unix epoch ms (UTC)
  } | null;
  selectedRuntimeId: RuntimeId | null;
  runtimeList: RuntimeListItem[];
  managedRuntimeSetup: boolean;
  channelsPending: boolean;
  projectAgents: MemberWithAgent[];
  /** ``?agent=`` search param — pre-selected agent hand-off. */
  agentParam: string | null;
  agentLibraryRevision: number;
  selectedAgentSlug: string | null;
  effectiveAgentSlug: string | null;
  availableSkills: SkillView[];
  projectSkills: SkillView[];
};

/**
 * ── Composer configuration ───────────────────────────────────────────
 *
 * Owns the composer/provider/agent/skill derivation cluster of the
 * conversation page: the execution-target resolution
 * (``providerTargetId`` / ``providerTarget`` / ``startingRuntime``),
 * the provider-channel and agent-library queries, the composer option
 * lists (providers / runtimes / agents / projects), the setup-pending
 * flags, the bound agent's brain, and the ``/`` skill picker
 * derivations. Bodies and dependency arrays are moved verbatim from
 * ``ConversationPage``. The two effects that write the page's composer
 * override state (seed-from-brain and provider auto-pick) stay in the
 * page — they touch foreign setters.
 */
export function useComposerConfig({
  id,
  isNewSession,
  projects,
  selectedProjectId,
  selectedSession,
  activeProject,
  executionTargets,
  execTargetId,
  pendingUserMessage,
  selectedRuntimeId,
  runtimeList,
  managedRuntimeSetup,
  channelsPending,
  projectAgents,
  agentParam,
  agentLibraryRevision,
  selectedAgentSlug,
  effectiveAgentSlug,
  availableSkills,
  projectSkills,
}: ComposerConfigParams) {
  // Existing sessions follow their observed origin. New project conversations
  // follow the selected project's origin; new temp chats follow the explicit
  // location chip (or the registered default). The catalog adapter owns
  // target resolution and routing.
  // The route id is authoritative during navigation. ``selectedSessionId``
  // intentionally lags until session detail resolves, so preferring it here
  // would briefly query the previous conversation's execution target.
  const providerSessionId = id !== NEW_SESSION_ID ? id : null;
  const sessionExecOrigin = useEntityOrigin(providerSessionId, "session");
  const selectedProviderProject = projects.find(
    (project) => project.id === selectedProjectId,
  );
  const selectedProjectOrigin = selectedProviderProject
    ? (selectedProviderProject.exec_origin ?? "local")
    : undefined;
  const providerTargetId =
    id !== NEW_SESSION_ID
      ? sessionExecOrigin
      : (selectedProjectOrigin ?? execTargetId);
  const providerTarget =
    executionTargets.find((target) => target.id === providerTargetId) ??
    getDefaultExecutionTarget();
  // Runtime-startup phase for the turn header: set from Send until the kernel
  // echoes ``message.user`` (which it writes at run() entry, i.e. once the
  // runtime is actually up). ``pendingUserMessage`` tracks exactly that
  // window, so it doubles as the phase flag. The location only picks the
  // wording — OSS registers no execution targets, so ``providerTargetId`` is
  // undefined there and this always reads "local".
  const startingRuntime: RuntimeStartLocation | null = pendingUserMessage
    ? providerTargetId === "cloud"
      ? "cloud"
      : "local"
    : null;
  const providerChannelState =
    useComposerProviderChannelState(providerTargetId);
  const providers = providerChannelState.providers;
  const {
    agents: myAgents,
    loaded: myAgentsLoaded,
    failed: myAgentsFailed,
    settling: myAgentsSettling,
    refresh: refreshAgents,
  } = useComposerAgentLibrary(
    providerTargetId,
    `${agentParam ?? ""}:${agentLibraryRevision}`,
  );

  const composerProviders = useComposerProviders(
    providers,
    selectedRuntimeId ?? undefined,
  );

  // Adapter: shrink ``RuntimeListItem`` from @valuz/core into the
  // narrower ``RuntimeSelectorItem`` shape @valuz/ui consumes — keeps
  // the UI package free of cross-package runtime imports.
  const composerRuntimes = useMemo(
    () =>
      runtimeList.map((rt) => ({
        id: rt.id,
        displayName: rt.display_name,
        available: rt.available,
        unavailableReason: rt.unavailable_reason,
      })),
    [runtimeList],
  );

  // 09-assistant: the 📁 chip's dropdown options — every project project.
  // ``ProjectListItem`` carries no member count, so the count is left
  // undefined for now (chip renders fine without it).
  // 09-assistant: whether the conversation currently targets 临时对话
  // (chat-default / non-project). The page stores the ``"chat-default"``
  // sentinel for 临时, so derive temp-ness from the resolved project kind
  // rather than a literal null.
  const isTempConversation = activeProject?.kind !== "project";

  // Settled, trusted and still empty — see useComposerAgentLibrary for why the
  // first empty answer is not enough to say this.
  const rosterEmpty =
    isTempConversation &&
    myAgentsLoaded &&
    !myAgentsFailed &&
    !myAgentsSettling &&
    myAgents.length === 0;
  const agentPending = managedRuntimeSetup && rosterEmpty;
  const setupPending = channelsPending || agentPending;

  // The attached strip under the composer owns the 📁 project choice for a
  // NEW conversation (replacing the composer's old toolbar chip) and keeps
  // showing the bound context on existing ones. All editions render it; the
  // location chip inside it only appears on multi-target builds.
  const execBarLocked = !(selectedSession == null && isNewSession);
  const execBarProjects = useMemo(
    () =>
      projects
        .filter((w) => w.kind === "project")
        .map((w) => ({ id: w.id, name: w.name, execOrigin: w.exec_origin })),
    [projects],
  );

  // Agent options for the composer's 🤖 chip. Candidates depend on the 📁
  // chip: 临时对话 → the "我的" library (``myAgents``); a project → its
  // 派驻 member roster (``projectAgents``). Runtime ids are mapped to their
  // display names so the dropdown reads "Claude Agent · mimo-v2.5-pro".
  const composerAgents = useMemo<ComposerAgentItem[]>(() => {
    if (isTempConversation) {
      // Pin the built-in Valurion to the top of
      // the dropdown; keep the rest of the library in its existing order.
      const ordered = [
        ...myAgents.filter((a) => a.slug === "valurion"),
        ...myAgents.filter((a) => a.slug !== "valurion"),
      ];
      return ordered.map((a) => ({
        slug: a.slug,
        name: a.name,
        runtimeLabel:
          runtimeList.find((r) => r.id === a.runtime)?.display_name ??
          a.runtime,
        modelLabel: modelLabel(a.model),
      }));
    }
    return projectAgents.map((m) => ({
      slug: m.member.agent_slug,
      name: m.agent?.name ?? m.member.agent_slug,
      runtimeLabel:
        runtimeList.find((r) => r.id === m.agent?.runtime_provider)
          ?.display_name ??
        m.agent?.runtime_provider ??
        "",
      modelLabel: modelLabel(m.agent?.model ?? ""),
    }));
  }, [isTempConversation, myAgents, projectAgents, runtimeList]);

  // The brain (runtime / model / provider / effort) of the currently bound
  // agent. It seeds the override controls' defaults; an untouched override
  // therefore equals the agent's own config, which the backend treats as a
  // no-op (it only diverges from the agent when the user actually changes a
  // value). Temp conversations bind a library agent; projects bind a member.
  const selectedAgentBrain = useMemo<{
    runtime: RuntimeId | null;
    model: string;
    providerId: string | null;
    effort: "low" | "medium" | "high" | "xhigh" | "max" | null;
  } | null>(() => {
    if (!selectedAgentSlug) return null;
    if (isTempConversation) {
      const a = myAgents.find((x) => x.slug === selectedAgentSlug);
      return a
        ? {
            runtime: (a.runtime as RuntimeId) || null,
            model: a.model,
            providerId: a.provider_id,
            effort: a.effort,
          }
        : null;
    }
    const a = projectAgents.find(
      (m) => m.member.agent_slug === selectedAgentSlug,
    )?.agent;
    return a
      ? {
          runtime: (a.runtime_provider as RuntimeId) || null,
          model: a.model,
          providerId: a.provider_id,
          effort: a.effort,
        }
      : null;
  }, [selectedAgentSlug, isTempConversation, myAgents, projectAgents]);

  // Whether this conversation's model diverges from the bound agent's default
  // (i.e. the user actually overrode it). Drives the muted model hint in the
  // agent button — hidden until an override happens, so a default chat is clean.
  // Slug → display name, so the header chip shows the agent's full name
  // ("研究分析师") rather than the raw kernel slug.
  const agentNameBySlug = useMemo(
    () => new Map(composerAgents.map((a) => [a.slug, a.name])),
    [composerAgents],
  );

  // For project project "/" mention, only show enabled/bound skills
  // Resolve an agent's stored skill entries to ``/`` picker items via the loaded
  // catalogs (shared with ProjectDetailPage to avoid drift).
  const resolveSkillItems = useCallback(
    (entries: string[] | null | undefined) =>
      resolveAgentSkillItems(entries, [availableSkills, projectSkills]),
    [availableSkills, projectSkills],
  );

  // The bound skills of the currently selected member agent — the ``/`` picker
  // list for a PROJECT conversation. Project chats can't attach skills ad-hoc
  // (skills are the agent's equipment), so ``/`` surfaces exactly that agent's
  // skills.
  const selectedAgentSkillItems = useMemo(() => {
    if (!effectiveAgentSlug) return [];
    const agent = projectAgents.find(
      (m) => m.member.agent_slug === effectiveAgentSlug,
    )?.agent;
    return resolveSkillItems(agent?.skills);
  }, [effectiveAgentSlug, projectAgents, resolveSkillItems]);

  // The ``/`` picker list for a NEW (non-project) conversation: the union of
  // the library-ENABLED skills and the selected agent's bound skills, deduped
  // by slug. A new conversation may have no agent (library skills only); the
  // global library switch (``library_enabled``) is what the Skills page toggles.
  const composerMentionSkills = useMemo<AgentSkillItem[]>(() => {
    const libraryItems: AgentSkillItem[] = availableSkills
      .filter((s) => s.library_enabled !== false)
      .map((s) => ({
        id: s.id,
        name: s.name,
        slug: s.slug,
        description: s.description,
      }));
    const agentEntries = isTempConversation
      ? myAgents.find((a) => a.slug === effectiveAgentSlug)?.skills
      : undefined;
    const seen = new Set(
      libraryItems.map((i) => i.slug).filter((s): s is string => !!s),
    );
    const merged: AgentSkillItem[] = [...libraryItems];
    for (const it of resolveSkillItems(agentEntries)) {
      if (it.slug && seen.has(it.slug)) continue;
      merged.push(it);
      if (it.slug) seen.add(it.slug);
    }
    return merged;
  }, [
    availableSkills,
    isTempConversation,
    myAgents,
    effectiveAgentSlug,
    resolveSkillItems,
  ]);

  // Slug → display-name map for rendering inline ``/skill-slug`` chips
  // in past user messages. We blend availableSkills (the global picker
  // catalog) and projectSkills (project-bound skills) so chips render
  // even for project-only skills that wouldn't appear in the global
  // catalog.
  const skillsBySlug = useMemo(() => {
    const map: Record<string, { name: string }> = {};
    for (const s of availableSkills) {
      if (s.slug) map[s.slug] = { name: s.name };
    }
    for (const s of projectSkills) {
      if (s.slug) map[s.slug] = { name: s.name };
    }
    return map;
  }, [availableSkills, projectSkills]);

  return {
    sessionExecOrigin,
    selectedProjectOrigin,
    providerTarget,
    startingRuntime,
    providerChannelState,
    providers,
    myAgents,
    myAgentsLoaded,
    refreshAgents,
    composerProviders,
    composerRuntimes,
    rosterEmpty,
    agentPending,
    setupPending,
    execBarLocked,
    execBarProjects,
    composerAgents,
    selectedAgentBrain,
    agentNameBySlug,
    selectedAgentSkillItems,
    composerMentionSkills,
    skillsBySlug,
  };
}
