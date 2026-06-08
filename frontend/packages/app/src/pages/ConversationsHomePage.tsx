import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Sparkles, MessageSquarePlus, FolderOpen } from "lucide-react";
import {
  ActionCardGrid,
  Composer,
  DataSourcePicker,
  SuggestionList,
  type DataSourceOption,
  type SkillSearchItem,
} from "@valuz/ui";
import {
  providersApi,
  mcpProvidersApi,
  sessionsApi,
  skillsApi,
  useComposerProviders,
  useModelDefaults,
  useRuntimes,
  usePanelStore,
  useSessionAttachments,
  workspacesApi,
  type ProviderDetail,
  type RuntimeId,
  type SessionListItem,
  type SkillView,
} from "@valuz/core";
import { homeSuggestions } from "@valuz/app/lib/prototype-data";
import { useTranslation } from "@valuz/core";
import { AttachmentParsingDialog } from "../components/AttachmentParsingDialog";

export const ConversationsHomePage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);
  const [input, setInput] = useState("");
  const [recentSessions, setRecentSessions] = useState<SessionListItem[]>([]);
  const [dataSources, setDataSources] = useState<DataSourceOption[]>([]);
  const [enabledSlugs, setEnabledSlugs] = useState<string[]>([]);
  const [providers, setProviders] = useState<ProviderDetail[]>([]);
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(
    null,
  );
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  // ``null`` until the global Default-config arrives — the effect below
  // seeds runtime/provider/model from Settings → Default in one shot, so
  // the composer reflects the user's chosen default without needing them
  // to click the picker.
  const [selectedRuntimeId, setSelectedRuntimeId] = useState<RuntimeId | null>(
    null,
  );
  // ``true`` once the user touches any composer picker (runtime / model
  // / provider). Locks out Settings-default reseeds so explicit choices
  // survive re-renders. Covers both runtime and (provider, model)
  // pickers because the global Default config is one tuple — once the
  // user starts overriding, none of the three should snap back.
  const [composerTouched, setComposerTouched] = useState(false);
  // ADR-013/014 permission mode for the chat-default new session.
  // Frozen at create time per ADR-006. Default ``full_access`` matches
  // the kernel's fallback so quick chats don't park.
  const [selectedPermissionMode, setSelectedPermissionMode] = useState<
    "default" | "auto_review" | "full_access"
  >("full_access");
  const { defaults: modelDefaults, loading: defaultsLoading } =
    useModelDefaults();

  useEffect(() => {
    if (!modelDefaults) return;
    if (composerTouched) return;
    // Force-assign — must beat the runtime-fallback effect below, which
    // would otherwise race in and pin ``selectedRuntimeId`` to the
    // ``firstAvailable`` runtime before this effect lands. ``prev ??
    // default`` lost that race because ``useRuntimes`` is cached at the
    // module level (warm) while ``useModelDefaults`` always hits the
    // network on first mount.
    if (modelDefaults.default_runtime) {
      setSelectedRuntimeId(modelDefaults.default_runtime);
    }
    if (modelDefaults.default_provider_id) {
      setSelectedProviderId(modelDefaults.default_provider_id);
    }
    if (modelDefaults.default_model) {
      setSelectedModelId(modelDefaults.default_model);
    }
  }, [modelDefaults, composerTouched]);
  const [globalSkills, setGlobalSkills] = useState<SkillView[]>([]);
  const [sending, setSending] = useState(false);
  // Attachments upload-on-attach, which needs a session up front. The first
  // attach eager-creates the quick-chat session (freezing model/runtime per
  // ADR-006 — the composer pickers lock once ``sessionId`` is set). Parsing
  // runs async on the backend; the hook polls the status for the progress UI.
  const [sessionId, setSessionId] = useState<string | null>(null);
  const {
    attachments: stagedAttachments,
    hasParsing,
    attachLocalFiles,
    remove: removeAttachment,
    markPendingConsumed,
  } = useSessionAttachments(sessionId);
  const [parsingConfirmOpen, setParsingConfirmOpen] = useState(false);

  const { runtimes: runtimeList } = useRuntimes();
  useEffect(() => {
    // Wait for the Settings-default fetch to land before falling back.
    // Without this guard the runtime picker would snap to
    // ``firstAvailable`` on the very first render (runtimes are
    // module-cached, defaults are network-fetched) and we'd lose the
    // race to apply the user's configured default below.
    if (defaultsLoading) return;
    if (runtimeList.length === 0) return;
    const current = runtimeList.find((rt) => rt.id === selectedRuntimeId);
    if (current && current.available) return;
    const firstAvailable = runtimeList.find((rt) => rt.available);
    if (firstAvailable) {
      setSelectedRuntimeId(firstAvailable.id as RuntimeId);
    }
  }, [runtimeList, selectedRuntimeId, defaultsLoading]);

  const composerProviders = useComposerProviders(
    providers,
    selectedRuntimeId ?? undefined,
  );

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

  // Auto-pick the first usable (provider, model) so Send never falls
  // through to a backend default that would 422. Same race-guard as
  // the runtime fallback above: wait for the Settings-default fetch
  // before deciding the current pick is "invalid" — otherwise the
  // composer briefly flashes to ``isDefault`` provider before the
  // configured global default lands.
  useEffect(() => {
    if (defaultsLoading) return;
    if (composerProviders.length === 0) return;
    const stillValid =
      selectedProviderId &&
      selectedModelId &&
      composerProviders.some(
        (m) =>
          m.providerId === selectedProviderId && m.modelId === selectedModelId,
      );
    if (stillValid) return;
    const preferred =
      composerProviders.find((m) => m.isDefault) ?? composerProviders[0];
    setSelectedProviderId(preferred.providerId);
    setSelectedModelId(preferred.modelId);
  }, [composerProviders, selectedProviderId, selectedModelId, defaultsLoading]);

  const availableSkills: SkillSearchItem[] = useMemo(
    () =>
      globalSkills
        .filter((s) => s.enabled)
        .map((s) => ({
          id: s.id,
          name: s.name,
          slug: s.slug,
          description: s.description,
        })),
    [globalSkills],
  );

  const bootstrap = useCallback(async () => {
    try {
      // Each quick-chat is now its own ephemeral chat workspace, so the
      // "recent chats" list aggregates sessions across every chat-kind
      // workspace rather than reading from a single singleton id.
      const [wsResponse, sessResponse] = await Promise.all([
        workspacesApi.list(),
        sessionsApi.list(),
      ]);
      const chatWsIds = new Set(
        wsResponse.workspaces.filter((w) => w.kind === "chat").map((w) => w.id),
      );
      const chatSessions = sessResponse.sessions.filter((s) =>
        chatWsIds.has(s.workspace_id),
      );
      setRecentSessions(chatSessions.slice(0, 5));
    } catch {
      // Silently fail — home page still works with empty state
    }
    try {
      const mcp = await mcpProvidersApi.list();
      setDataSources(mcp.providers);
      // Auto-enable already-connected providers; the user can still toggle them off.
      setEnabledSlugs(
        mcp.providers.filter((p) => p.connected).map((p) => p.slug),
      );
    } catch {
      // Silently fail — picker just shows empty state.
    }
    try {
      const chListRes = await providersApi.list();
      const details = await Promise.all(
        chListRes.providers
          .filter((c) => c.enabled)
          .map((c) => providersApi.get(c.id).catch(() => null)),
      );
      setProviders(details.filter((d): d is ProviderDetail => d !== null));
    } catch {
      // Silently fail — Composer model picker just shows empty state.
    }
    try {
      const skillRes = await skillsApi.list();
      setGlobalSkills(skillRes.skills);
    } catch {
      // Silently fail — Composer '/' menu just shows nothing.
    }
  }, []);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const handleConnectDataSource = (slug: string) => {
    // Hand off to settings; the OAuth completion flips `connected` and a re-bootstrap
    // will pick it up next time the user lands here.
    navigate(`/settings?focus=data-source&slug=${encodeURIComponent(slug)}`);
  };

  // ``"chat-default"`` is a server-side sentinel: each ``POST /v1/sessions``
  // with this id allocates a brand-new ephemeral chat workspace (and a
  // fresh kernel project + cwd), so quick-chats never share working
  // directories with one another. ADR-006: model_provider is locked at
  // session creation, so the composer's pick rides on the create call —
  // sendMessage ignores it.
  const sessionPayload = () => ({
    workspace_id: "chat-default",
    mcp_provider_slugs: enabledSlugs.length > 0 ? enabledSlugs : undefined,
    provider_id: selectedProviderId ?? undefined,
    model_id: selectedModelId ?? undefined,
    runtime_id: selectedRuntimeId ?? undefined,
    permission_mode: selectedPermissionMode,
  });

  const handleNewChat = async () => {
    try {
      const session = await sessionsApi.create(sessionPayload());
      navigate(`/conversation/${session.id}`);
    } catch {
      navigate("/conversation/new");
    }
  };

  // Mint (once) the quick-chat session attachments upload into. Cached in
  // ``sessionId`` so subsequent attaches + the send reuse the same session.
  const ensureSession = async (): Promise<{ id: string }> => {
    if (sessionId) return { id: sessionId };
    const session = await sessionsApi.create(sessionPayload());
    setSessionId(session.id);
    return { id: session.id };
  };

  // The actual send. Attachments are already uploaded (on attach), so this
  // just reuses / mints the session and posts the message.
  const performSend = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const session = await ensureSession();
      markPendingConsumed();
      await sessionsApi.sendMessage(session.id, text);
      setInput("");
      panelSetCollapsed(false);
      navigate(`/conversation/${session.id}`, {
        state: { revealSessionContext: true },
      });
    } catch {
      navigate("/conversation/new");
    } finally {
      setSending(false);
    }
  };

  const handleSend = () => {
    const text = input.trim();
    if (!text || sending) return;
    // Block on unfinished parsing — let the user wait or submit raw.
    if (hasParsing) {
      setParsingConfirmOpen(true);
      return;
    }
    void performSend();
  };

  return (
    <div className="flex h-full flex-col">
      {/* Top-right mascot — ``position: fixed`` anchors to the
          viewport (not the page or scroll container), so it sits in
          the window's top-right regardless of any scroll context.
          ``top-[60px]`` clears the workspace TopBar (h-12 = 48 px);
          ``right-6`` keeps a 24 px breathing margin from the window
          edge. Only mounted on the home page so it doesn't bleed
          onto other routes.

          Entrance animation: when the homepage mounts the mascot
          performs a single fly-around — swoops in from off-screen
          lower-left, traces a clockwise loop overhead, then settles
          into its resting position. Pure CSS keyframes; one-shot
          via ``animation-fill-mode: both`` so it doesn't replay on
          every re-render. ``prefers-reduced-motion`` skips the loop
          and snaps directly to rest. */}
      <style>{`
        @keyframes mascot-fly-in {
          0%   { transform: translate(-110vw,  18vh) rotate(  45deg) scale(0.5); opacity: 0;   }
          18%  { transform: translate( -45vw, -10vh) rotate( -40deg) scale(0.85); opacity: 0.95; }
          38%  { transform: translate( -10vw, -22vh) rotate(-180deg) scale(0.95); opacity: 1;   }
          58%  { transform: translate(  18vw, -10vh) rotate(-300deg) scale(1.0); opacity: 1;   }
          75%  { transform: translate(   8vw,   8vh) rotate(-380deg) scale(1.05); opacity: 0.9; }
          90%  { transform: translate(  -2vw,  -2vh) rotate(-345deg) scale(1.0); opacity: 0.75; }
          100% { transform: translate(   0,    0  ) rotate(-360deg) scale(1);   opacity: 0.6; }
        }
        .mascot-fly-in {
          animation: mascot-fly-in 1.9s cubic-bezier(0.22, 1, 0.36, 1) both;
          will-change: transform, opacity;
        }
        @media (prefers-reduced-motion: reduce) {
          .mascot-fly-in { animation: none; opacity: 0.6; }
        }
      `}</style>
      <img
        src="./mascot-wave.png"
        alt=""
        aria-hidden="true"
        className="mascot-fly-in pointer-events-none fixed right-6 top-[60px] z-30 h-[180px] w-auto select-none opacity-60"
      />
      <div className="flex flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[680px] px-8 pt-4 pb-16">
            <div className="mb-10">
              <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-brand-light text-brand">
                <Sparkles className="h-6 w-6" />
              </div>
              <h1 className="mb-2 text-2xl font-heading font-semibold text-ink-heading">
                {t("conversation.newChat" as Parameters<typeof t>[0])}
              </h1>
              <p className="max-w-[500px] text-base leading-relaxed text-ink-body">
                {t("conversation.startHere" as Parameters<typeof t>[0])}
              </p>
            </div>

            <div className="mb-10">
              <ActionCardGrid
                actions={[
                  {
                    icon: MessageSquarePlus,
                    title: t("conversation.newChat" as Parameters<typeof t>[0]),
                    desc: t(
                      "conversation.startHere" as Parameters<typeof t>[0],
                    ),
                    onClick: () => {
                      void handleNewChat();
                    },
                  },
                  {
                    icon: FolderOpen,
                    title: t("common.create" as Parameters<typeof t>[0]),
                    desc: t("project.instruction" as Parameters<typeof t>[0]),
                    onClick: () => navigate("/projects"),
                  },
                ]}
              />
            </div>

            {recentSessions.length > 0 ? (
              <div className="mb-10">
                <div className="label-mono mb-3">
                  {t("nav.chat" as Parameters<typeof t>[0])}
                </div>
                <div className="space-y-1.5">
                  {recentSessions.map((session) => (
                    <button
                      key={session.id}
                      type="button"
                      className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-surface-muted"
                      onClick={() => navigate(`/conversation/${session.id}`)}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm text-ink-heading">
                          {session.name ||
                            t("cron.untitled" as Parameters<typeof t>[0])}
                        </div>
                        {session.last_user_message_text ? (
                          <div className="mt-0.5 truncate text-xs text-ink-meta">
                            {session.last_user_message_text}
                          </div>
                        ) : null}
                      </div>
                      <span className="shrink-0 text-xs text-ink-meta">
                        {session.status}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            <div>
              <div className="label-mono mb-3">
                {t("conversation.startHere" as Parameters<typeof t>[0])}
              </div>
              <SuggestionList
                suggestions={homeSuggestions}
                onClick={(text) => {
                  if (text) setInput(text);
                }}
              />
            </div>

            <div className="mt-12 rounded-lg border border-surface-border bg-surface-soft p-5">
              <div className="mb-1 text-xs font-medium text-ink-label">
                {t("settings.title" as Parameters<typeof t>[0])}
              </div>
              <p className="text-sm leading-relaxed text-ink-body">
                {t("commandPalette.placeholder" as Parameters<typeof t>[0])}{" "}
                <kbd className="tabular rounded border border-surface-border bg-surface px-2 py-0.5 font-mono text-2xs">
                  ⌘K
                </kbd>{" "}
                {t("commandPalette.placeholder" as Parameters<typeof t>[0])}{" "}
                <kbd className="tabular rounded border border-surface-border bg-surface px-2 py-0.5 font-mono text-2xs">
                  @Skill
                </kbd>{" "}
                {t("skill.noAvailable" as Parameters<typeof t>[0])}
              </p>
            </div>
          </div>
        </div>

        <div className="border-t border-surface-border bg-surface px-8 pb-5 pt-4">
          <div className="mx-auto max-w-[780px] space-y-3">
            {dataSources.length > 0 ? (
              <DataSourcePicker
                options={dataSources}
                selectedSlugs={enabledSlugs}
                onChange={setEnabledSlugs}
                onConnect={handleConnectDataSource}
              />
            ) : null}
            <Composer
              value={input}
              onChange={setInput}
              onSend={() => {
                void handleSend();
              }}
              skills={availableSkills}
              providers={composerProviders}
              selectedProviderId={selectedProviderId}
              selectedModelId={selectedModelId}
              onModelChange={(chId, mId) => {
                setSelectedProviderId(chId);
                setSelectedModelId(mId);
                setComposerTouched(true);
              }}
              runtimes={composerRuntimes}
              selectedRuntimeId={selectedRuntimeId}
              onRuntimeChange={(rt) => {
                setSelectedRuntimeId((rt as RuntimeId | null) ?? null);
                setComposerTouched(true);
              }}
              permissionMode={selectedPermissionMode}
              onPermissionModeChange={(mode) => {
                setSelectedPermissionMode(mode);
                setComposerTouched(true);
              }}
              // ADR-006: the first attach mints the session, freezing
              // model/runtime — lock the pickers once that happens.
              modelLocked={sessionId != null}
              uploadOnAttach
              existingAttachmentCount={
                stagedAttachments.filter((a) => !a.consumed_at).length
              }
              pinnedAttachments={stagedAttachments
                .filter((a) => !a.consumed_at)
                .map((a) => ({
                  id: a.id,
                  name: a.filename,
                  parseStatus: a.parse_status as
                    | "parsing"
                    | "ready"
                    | "failed"
                    | undefined,
                  sourceKind: a.source_kind,
                }))}
              onRemovePinnedAttachment={(attId) => void removeAttachment(attId)}
              onLocalUpload={(files) => void attachLocalFiles(files, ensureSession)}
              onFileDrop={(files) => void attachLocalFiles(files, ensureSession)}
              sending={sending}
              autoFocus
            />
            <AttachmentParsingDialog
              open={parsingConfirmOpen}
              onConfirm={() => {
                setParsingConfirmOpen(false);
                void performSend();
              }}
              onCancel={() => setParsingConfirmOpen(false)}
            />
          </div>
        </div>
      </div>
    </div>
  );
};
