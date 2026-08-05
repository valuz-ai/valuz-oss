import { useCallback, useEffect, useState } from "react";
import {
  connectorsApi,
  useModelDefaults,
  useRuntimes,
  type RuntimeId,
  type SessionListItem,
  type SkillView,
} from "@valuz/core";
import type { ComposerConnector } from "@valuz/ui";

/** ADR-013 approval-mode union (the composer's permission picker). */
export type PermissionMode = "default" | "auto_review" | "full_access";

type ComposerSelectionParams = {
  selectedSessionId: string | null;
  /** The user's agent pick for the next (new) session — gates the
   *  Settings-default seed (agent-bound chats seed from the brain instead). */
  selectedAgentSlug: string | null;
  selectedSession: SessionListItem | null;
};

/**
 * ── Composer override selection spine ────────────────────────────────
 *
 * Owns the composer's override state cluster of the conversation page:
 * the runtime / provider / model / effort / permission / connector /
 * skill selections, the Settings-default seed effect, the
 * runtime-availability repair effect, the connector fetch +
 * ``toggleConnector``, the locked-session mirror effect, and
 * ``handleSwitchModel`` (+ the retry-count / model-unlock state it
 * writes). Bodies, comments and dependency arrays are moved verbatim
 * from ``ConversationPage``. The two override effects that read
 * ``useComposerConfig`` outputs (seed-from-brain and provider
 * auto-pick) stay in the page — moving them here would create a
 * call-order cycle between the two hooks.
 */
export function useComposerSelection({
  selectedSessionId,
  selectedAgentSlug,
  selectedSession,
}: ComposerSelectionParams) {
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(
    null,
  );
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  // Runtime / provider / model start as ``null`` — the Settings →
  // Default tuple seeds them via ``useModelDefaults`` below for new
  // sessions. For an existing session the locked_* sync effect later
  // in this file overrides these from session metadata, so the order
  // is: defaults → session-locked → user picker.
  const [selectedRuntimeId, setSelectedRuntimeId] = useState<RuntimeId | null>(
    null,
  );
  // ``true`` once the user touches any composer picker. Locks out
  // Settings-default reseeds so explicit choices survive re-renders.
  const [composerTouched, setComposerTouched] = useState(false);
  const { defaults: modelDefaults, loading: defaultsLoading } =
    useModelDefaults();
  // Seed the composer pickers from Settings → Default ONLY for new
  // sessions. The session-locked sync effect later in this file owns
  // the existing-session path; if we let defaults run there too, the
  // composer would briefly flash to the global default before snapping
  // back to whatever the session was created with.
  useEffect(() => {
    if (!modelDefaults) return;
    if (selectedSessionId) return;
    if (composerTouched) return;
    // Agent-bound conversations seed runtime / model / effort from the
    // agent's brain (the agent-brain effect later in this file), not from
    // Settings → Default. Only quick chats (no agent) use the global default.
    if (selectedAgentSlug) return;
    // Force-assign — must beat the runtime-fallback effect below, which
    // otherwise races in first because useRuntimes is module-cached.
    if (modelDefaults.default_runtime) {
      setSelectedRuntimeId(modelDefaults.default_runtime);
    }
    if (modelDefaults.default_provider_id) {
      setSelectedProviderId(modelDefaults.default_provider_id);
    }
    if (modelDefaults.default_model) {
      setSelectedModelId(modelDefaults.default_model);
    }
    // Effort is non-nullable on ``ModelDefaults`` (the backend coerces
    // unset / cleared rows to ``EFFORT_FALLBACK`` server-side), so the
    // composer always opens on the user's actual Settings pick — Max
    // means Max, not the prompt-cache-friendly fallback "high".
    setSelectedEffort(modelDefaults.default_effort);
  }, [modelDefaults, composerTouched, selectedSessionId]);
  const { runtimes: runtimeList } = useRuntimes();
  // Repair the default if claude_agent ever reports unavailable.
  // Waits for ``useModelDefaults`` so we don't race-overwrite the
  // user's configured default before it lands.
  useEffect(() => {
    if (defaultsLoading) return;
    if (runtimeList.length === 0) return;
    const current = runtimeList.find((rt) => rt.id === selectedRuntimeId);
    if (current && current.available) return;
    const firstAvailable = runtimeList.find((rt) => rt.available);
    if (firstAvailable) {
      setSelectedRuntimeId(firstAvailable.id as RuntimeId);
    }
  }, [runtimeList, selectedRuntimeId, defaultsLoading]);
  const [retryCounts, setRetryCounts] = useState<Record<string, number>>({});
  const [modelSelectorUnlocked, setModelSelectorUnlocked] = useState(false);

  // ADR-013 approval mode. ``full_access`` matches the host's backend
  // default — preserves prior behaviour for users who don't touch the
  // picker. For an active session this is reconciled from
  // ``selectedSession.permission_mode`` by the effect below. For a new
  // session, this value is forwarded into ``sessionsApi.create``.
  // Mid-session changes go through PATCH /permission-mode (effective on
  // the runtime's next cold load, per the ADR).
  const [selectedPermissionMode, setSelectedPermissionMode] = useState<
    "default" | "auto_review" | "full_access"
  >("full_access");

  // Reasoning-effort budget for the session (kernel V5+bba3014
  // ``ModelSettings.effort``). ``null`` = let the runtime fall through
  // to its SDK default. For a new session, this value is forwarded into
  // ``sessionsApi.create``; for an existing session, mid-session
  // changes PATCH ``/v1/sessions/{id}/effort`` (live-reconcile applies
  // on next Send).
  const [selectedEffort, setSelectedEffort] = useState<
    "low" | "medium" | "high" | "xhigh" | "max" | null
  >(null);

  // Connector selection — only meaningful for new sessions (locked at creation
  // per ADR-006). The picker UI was removed from the composer; we still
  // pre-select every connected connector at session-creation time so the
  // new session inherits the user's globally-enabled data sources.
  const [selectedMcpSlugs, setSelectedMcpSlugs] = useState<string[]>([]);
  // Connected connectors shown in the composer "+" menu. On a new conversation
  // they're toggleable (the selection is handed to the session at creation); on
  // an existing one they're read-only (connectors lock at creation). Fetched +
  // defaulted to all-on once on mount, so a new conversation keeps the user's
  // pick across the new→existing URL transition (which reuses this component).
  const [connectorOptions, setConnectorOptions] = useState<ComposerConnector[]>(
    [],
  );
  useEffect(() => {
    connectorsApi
      .list()
      .then(({ connectors: list }) => {
        const connected = list.filter((c) => c.status === "connected");
        setConnectorOptions(
          connected.map((c) => ({
            slug: c.slug,
            label: c.display_name,
            description: c.description ?? undefined,
          })),
        );
        setSelectedMcpSlugs(connected.map((c) => c.slug));
      })
      .catch(() => {
        /* non-fatal */
      });
    // Mount-only: re-running on the new→existing id change would reset the
    // user's connector selection right after they created the session.
  }, []);
  const toggleConnector = useCallback((slug: string, enabled: boolean) => {
    setSelectedMcpSlugs((prev) =>
      enabled
        ? prev.includes(slug)
          ? prev
          : [...prev, slug]
        : prev.filter((s) => s !== slug),
    );
  }, []);

  const [selectedComposerSkill, setSelectedComposerSkill] =
    useState<SkillView | null>(null);

  const handleSwitchModel = useCallback((turnId: string) => {
    setRetryCounts((prev) => {
      const next = { ...prev, [turnId]: (prev[turnId] ?? 0) + 1 };
      return next;
    });
    setModelSelectorUnlocked(true);
  }, []);

  // Mirror the kernel session's locked model/provider into the composer's
  // selector state so the UI shows what the session is actually using
  // (V5 freezes the model at session creation — picking a different one
  // mid-conversation would be a no-op anyway). Without this sync the
  // picker stays at whatever the page initialised to, which is typically
  // NOT the model the session was created with from the project page.
  useEffect(() => {
    if (!selectedSession) return;
    // Sync the composer selector to whatever the kernel locked at session
    // creation. Both ids must come from the session (not just locked_model_id)
    // — the composer matches on (providerId, modelId) pairs, so a missing
    // provider id makes the selector silently fall back to the project
    // default's display label even when the session is wired to a different
    // model end-to-end.
    if (selectedSession.locked_provider_id) {
      setSelectedProviderId(selectedSession.locked_provider_id);
    }
    if (selectedSession.locked_model_id) {
      setSelectedModelId(selectedSession.locked_model_id);
    }
    // REP-107: also sync the runtime selector to the session's frozen
    // runtime_provider. Without this the page-level ``selectedRuntimeId``
    // state leaks across session switches — switching from a Claude
    // Agent session to a Valuz Agent one would keep showing "Claude
    // Agent" until the user manually clicked the picker.
    if (selectedSession.runtime_provider) {
      setSelectedRuntimeId(selectedSession.runtime_provider as RuntimeId);
    }
    // ADR-013: reconcile the permission selector to the live session.
    if (selectedSession.permission_mode) {
      setSelectedPermissionMode(
        selectedSession.permission_mode as
          "default" | "auto_review" | "full_access",
      );
    }
    // Kernel V5+bba3014: reconcile the effort selector to the live
    // session so live-reconcile PATCHes start from the persisted value.
    setSelectedEffort(
      (selectedSession.effort as
        "low" | "medium" | "high" | "xhigh" | "max" | null | undefined) ?? null,
    );
  }, [
    selectedSession?.id,
    selectedSession?.locked_model_id,
    selectedSession?.locked_provider_id,
    selectedSession?.runtime_provider,
    selectedSession?.permission_mode,
    selectedSession?.effort,
  ]);

  return {
    selectedProviderId,
    setSelectedProviderId,
    selectedModelId,
    setSelectedModelId,
    selectedRuntimeId,
    setSelectedRuntimeId,
    composerTouched,
    setComposerTouched,
    defaultsLoading,
    runtimeList,
    retryCounts,
    setRetryCounts,
    modelSelectorUnlocked,
    selectedPermissionMode,
    setSelectedPermissionMode,
    selectedEffort,
    setSelectedEffort,
    selectedMcpSlugs,
    connectorOptions,
    toggleConnector,
    selectedComposerSkill,
    setSelectedComposerSkill,
    handleSwitchModel,
  };
}
