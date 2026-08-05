import type { Dispatch, SetStateAction } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowDown } from "lucide-react";
import {
  getDefaultExecutionTarget,
  sessionsApi,
  useTranslation,
  type ProjectListItem,
  type RuntimeId,
  type SessionAttachmentItem,
  type SessionListItem,
  type SkillView,
} from "@valuz/core";
import { cn, Composer, type ComposerConnector } from "@valuz/ui";
import type { I18nKey } from "@valuz/shared";
import { QueuedInputsBar } from "../../components/QueuedInputsBar";
import { AttachmentParsingDialog } from "../../components/AttachmentParsingDialog";
import { CreateAgentDialog } from "../../components/CreateAgentDialog";
import { ExecutionLocationBar } from "../../components/ExecutionLocationBar";
import type { useComposerConfig } from "./useComposerConfig";
import type { useConversationScroll } from "./useConversationScroll";
import type { useConversationSend } from "./useConversationSend";
import type { useInputQueue } from "./useInputQueue";

type ComposerConfig = ReturnType<typeof useComposerConfig>;
type ConversationScroll = ReturnType<typeof useConversationScroll>;
type ConversationSend = ReturnType<typeof useConversationSend>;
type InputQueue = ReturnType<typeof useInputQueue>;

type ComposerPaneProps = {
  showScrollBottom: boolean;
  handleScrollToBottom: ConversationScroll["handleScrollToBottom"];
  displayBusy: boolean;
  selectedSession: SessionListItem | null;
  rosterEmpty: ComposerConfig["rosterEmpty"];
  channelLoaded: boolean;
  hasChannel: boolean;
  channelsPending: boolean;
  agentPending: ComposerConfig["agentPending"];
  setupPending: ComposerConfig["setupPending"];
  refreshChannels: () => void;
  refreshAgents: ComposerConfig["refreshAgents"];
  createAgentOpen: boolean;
  setCreateAgentOpen: Dispatch<SetStateAction<boolean>>;
  setAgentLibraryRevision: Dispatch<SetStateAction<number>>;
  setSelectedAgentSlug: Dispatch<SetStateAction<string | null>>;
  setComposerTouched: Dispatch<SetStateAction<boolean>>;
  selectedSessionId: string | null;
  queue: InputQueue["queue"];
  isBusy: boolean;
  queueDispatching: InputQueue["queueDispatching"];
  queuePaused: InputQueue["queuePaused"];
  handleEditQueued: InputQueue["handleEditQueued"];
  handleDeleteQueued: InputQueue["handleDeleteQueued"];
  handleResumeQueue: InputQueue["handleResumeQueue"];
  handleSteerQueued: InputQueue["handleSteerQueued"];
  conversationInstanceKey: string;
  draft: string;
  setDraft: Dispatch<SetStateAction<string>>;
  isProjectProject: boolean;
  effectiveAgentSlug: string | null;
  handleSend: () => void;
  interruptRef: { current: () => void };
  sessionAttachments: SessionAttachmentItem[];
  handleRemoveSessionAttachment: ConversationSend["handleRemoveSessionAttachment"];
  composerAgents: ComposerConfig["composerAgents"];
  sessionAgentSlug: string | null;
  selectedAgentSlug: string | null;
  execBarLocked: ComposerConfig["execBarLocked"];
  sessionExecOrigin: ComposerConfig["sessionExecOrigin"];
  execTargetId: string | null;
  setExecTargetId: Dispatch<SetStateAction<string | null>>;
  setSelectedProviderId: Dispatch<SetStateAction<string | null>>;
  setSelectedModelId: Dispatch<SetStateAction<string | null>>;
  projects: ProjectListItem[];
  selectedProjectId: string | null;
  setSelectedProjectId: Dispatch<SetStateAction<string | null>>;
  setSelectedComposerSkill: Dispatch<SetStateAction<SkillView | null>>;
  execBarProjects: ComposerConfig["execBarProjects"];
  providerTarget: ComposerConfig["providerTarget"];
  panelSetCollapsed: (collapsed: boolean) => void;
  composerProviders: ComposerConfig["composerProviders"];
  selectedProviderId: string | null;
  selectedModelId: string | null;
  composerRuntimes: ComposerConfig["composerRuntimes"];
  selectedRuntimeId: RuntimeId | null;
  setSelectedRuntimeId: Dispatch<SetStateAction<RuntimeId | null>>;
  selectedPermissionMode: "default" | "auto_review" | "full_access";
  setSelectedPermissionMode: Dispatch<
    SetStateAction<"default" | "auto_review" | "full_access">
  >;
  isNewSession: boolean;
  /** Route param (``/conversation/{id}``), defaulted to ``NEW_SESSION_ID``. */
  id: string;
  selectedEffort: "low" | "medium" | "high" | "xhigh" | "max" | null;
  setSelectedEffort: Dispatch<
    SetStateAction<"low" | "medium" | "high" | "xhigh" | "max" | null>
  >;
  modelSelectorUnlocked: boolean;
  selectedAgentSkillItems: ComposerConfig["selectedAgentSkillItems"];
  composerMentionSkills: ComposerConfig["composerMentionSkills"];
  availableSkills: SkillView[];
  handleOpenKbPicker: ConversationSend["handleOpenKbPicker"];
  handleLocalFilesAttach: ConversationSend["handleLocalFilesAttach"];
  connectorOptions: ComposerConnector[];
  selectedMcpSlugs: string[];
  toggleConnector: (slug: string, enabled: boolean) => void;
  parsingConfirmOpen: boolean;
  setParsingConfirmOpen: Dispatch<SetStateAction<boolean>>;
  performSend: ConversationSend["performSend"];
};

/**
 * ── Composer region ──────────────────────────────────────────────────
 *
 * Everything below the transcript: the scroll-to-bottom affordance, the
 * setup banner, the create-agent dialog, the queued-inputs bar, the
 * Composer itself (with the execution-location footer bar) and the
 * attachment-parsing confirm dialog. Extracted verbatim from
 * ConversationPage's return JSX — behavior and markup unchanged; every
 * referenced page value arrives as a same-named prop, so the inline
 * handlers close over props instead of page locals.
 */
export function ComposerPane({
  showScrollBottom,
  handleScrollToBottom,
  displayBusy,
  selectedSession,
  rosterEmpty,
  channelLoaded,
  hasChannel,
  channelsPending,
  agentPending,
  setupPending,
  refreshChannels,
  refreshAgents,
  createAgentOpen,
  setCreateAgentOpen,
  setAgentLibraryRevision,
  setSelectedAgentSlug,
  setComposerTouched,
  selectedSessionId,
  queue,
  isBusy,
  queueDispatching,
  queuePaused,
  handleEditQueued,
  handleDeleteQueued,
  handleResumeQueue,
  handleSteerQueued,
  conversationInstanceKey,
  draft,
  setDraft,
  isProjectProject,
  effectiveAgentSlug,
  handleSend,
  interruptRef,
  sessionAttachments,
  handleRemoveSessionAttachment,
  composerAgents,
  sessionAgentSlug,
  selectedAgentSlug,
  execBarLocked,
  sessionExecOrigin,
  execTargetId,
  setExecTargetId,
  setSelectedProviderId,
  setSelectedModelId,
  projects,
  selectedProjectId,
  setSelectedProjectId,
  setSelectedComposerSkill,
  execBarProjects,
  providerTarget,
  panelSetCollapsed,
  composerProviders,
  selectedProviderId,
  selectedModelId,
  composerRuntimes,
  selectedRuntimeId,
  setSelectedRuntimeId,
  selectedPermissionMode,
  setSelectedPermissionMode,
  isNewSession,
  id,
  selectedEffort,
  setSelectedEffort,
  modelSelectorUnlocked,
  selectedAgentSkillItems,
  composerMentionSkills,
  availableSkills,
  handleOpenKbPicker,
  handleLocalFilesAttach,
  connectorOptions,
  selectedMcpSlugs,
  toggleConnector,
  parsingConfirmOpen,
  setParsingConfirmOpen,
  performSend,
}: ComposerPaneProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <>
      {/* Scroll-to-bottom button + Composer share a relative wrapper so the
            button anchors to the Composer's top edge (``bottom-full``) instead
            of a magic ``bottom: 150px``. The Composer's height varies a lot
            (skill chip, attachments, multi-line draft, model picker), so the
            old magic number sometimes left the button overlapping the Composer
            top border. Pulses while a turn is still streaming so the user
            knows the run hasn't stalled. */}
      <div className="relative">
        {showScrollBottom && (
          <button
            type="button"
            onClick={handleScrollToBottom}
            className={cn(
              "absolute bottom-full left-1/2 z-20 mb-3 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-surface-border bg-surface shadow-md transition-opacity hover:bg-surface-soft",
              displayBusy &&
                "animate-[border-breathe_1.8s_ease-in-out_infinite] border-brand/60",
            )}
          >
            <ArrowDown className="h-4 w-4 text-ink-body" />
          </button>
        )}

        {!selectedSession &&
          (rosterEmpty || (channelLoaded && !hasChannel)) && (
            <div className="mx-auto mb-2 flex w-full max-w-[760px] items-center justify-between gap-3 rounded-lg border border-info-border bg-info-light px-3 py-2 text-xs text-info-text">
              <span>
                {channelsPending
                  ? t("conversation.channelsPendingBanner" as I18nKey)
                  : agentPending
                    ? t("conversation.agentPendingBanner" as I18nKey)
                    : channelLoaded && !hasChannel
                      ? rosterEmpty
                        ? t("conversation.noChannelAndAgentBanner" as I18nKey)
                        : t("conversation.noChannelBanner" as I18nKey)
                      : t("conversation.noAgentBanner" as I18nKey)}
              </span>
              <button
                type="button"
                onClick={() => {
                  if (!setupPending) {
                    navigate("/welcome");
                    return;
                  }
                  if (channelsPending) refreshChannels();
                  if (rosterEmpty) refreshAgents();
                }}
                className="shrink-0 rounded-md bg-brand px-2.5 py-1 font-medium text-white transition-colors hover:bg-brand-hover"
              >
                {setupPending
                  ? t("conversation.pendingBannerCta" as I18nKey)
                  : t("conversation.noAgentBannerCta" as I18nKey)}
              </button>
            </div>
          )}
        <CreateAgentDialog
          open={createAgentOpen}
          onOpenChange={setCreateAgentOpen}
          onCreated={(slug) => {
            setAgentLibraryRevision((revision) => revision + 1);
            setSelectedAgentSlug(slug);
            setComposerTouched(true);
          }}
        />
        {selectedSessionId ? (
          // Mirror the Composer root's horizontal inset (``px-5``) so the
          // queue lines up with the input box, which is its own
          // ``mx-auto max-w-[760px]`` inside that same px-5.
          <div className="px-5">
            <QueuedInputsBar
              queue={queue}
              // The dispatched head bridges the gap between "left the queue"
              // and "visible in the transcript": show it only while no turn
              // is active — once the drained turn's ``message.user`` streams
              // in (``isBusy`` via the running status), the transcript
              // renders it and the bubble would just duplicate it.
              dispatching={isBusy ? null : queueDispatching}
              paused={queuePaused}
              onEdit={handleEditQueued}
              onDelete={handleDeleteQueued}
              onResume={handleResumeQueue}
              onSteer={handleSteerQueued}
            />
          </div>
        ) : null}
        <Composer
          // Remount on true conversation switches so native autoFocus refires.
          // Keep the key stable during /conversation/new → real-id promotion
          // so first-send does not rebuild the composer.
          key={conversationInstanceKey}
          value={draft}
          onChange={setDraft}
          // Keep the composer usable while a turn runs — submitting queues a
          // follow-up (session-input-queue) instead of being blocked.
          queueWhileSending
          // Project conversations can't attach skills ad-hoc (skills are the
          // agent's equipment), so the toolbar "add skill" button stays hidden
          // there. The ``/`` picker, however, is enabled once a member agent
          // is selected so the user can invoke that agent's bound skills; the
          // assistant (non-project) chat keeps both for global ``/`` skills.
          showSkillButton={!isProjectProject}
          showSkillSlash={
            isProjectProject ? effectiveAgentSlug != null : undefined
          }
          autoFocus
          onSend={() => {
            void handleSend();
          }}
          sending={displayBusy}
          onStop={() => interruptRef.current()}
          // Upload cap counts only the *pending* server rows — the
          // ones staged for the next turn. Consumed rows live on in
          // the panel as history but don't eat the staging budget.
          // The composer adds its own not-yet-uploaded local queue on
          // top and greys the attachment menu once the total hits
          // ``MAX_SESSION_ATTACHMENTS``.
          existingAttachmentCount={
            sessionAttachments.filter((a) => !a.consumed_at).length
          }
          // Both local uploads and KB picks surface as chips in the
          // composer's attachment row, each with its async parse status
          // (spinner while ``parsing``). Only *pending* ones show: once a
          // turn consumes them they drop from the staging row (but stay in
          // the side panel's history).
          uploadOnAttach
          pinnedAttachments={sessionAttachments
            .filter((a) => !a.consumed_at)
            .map((a) => ({
              id: a.id,
              name: a.filename,
              parseStatus: a.parse_status as
                "parsing" | "ready" | "failed" | "native" | undefined,
              sourceKind: a.source_kind,
            }))}
          onRemovePinnedAttachment={handleRemoveSessionAttachment}
          // 09-assistant §2.1/§2.2: every conversation — 临时 or project —
          // binds to an agent, so the 🤖 chip is always in agent mode. The
          // candidate roster comes from ``composerAgents`` (临时 → "我的"
          // library; project → 派驻 members). The session inherits
          // runtime/model/provider/effort/skills/connectors from the chosen
          // agent.
          agents={composerAgents}
          selectedAgentSlug={
            selectedSession ? sessionAgentSlug : selectedAgentSlug
          }
          // Surface the bound agent's runtime / model / effort in the agent
          // dropdown — temp / quick chats only. Project conversations are
          // driven by the deployed agent team, so they neither show the
          // model hint nor offer a per-conversation override. For a NEW temp
          // conversation the controls are an editable override (applied at
          // session creation; the agent itself is never modified); for an
          // EXISTING temp session runtime/model are read-only (frozen,
          // ADR-006) but visible, and effort stays editable (live-reconcile).
          allowAgentBrainOverride={!isProjectProject}
          // ADR-006: once a session exists both chips freeze (the locked
          // 🤖 chip shows the bound ``sessionAgentSlug``).
          agentLocked={selectedSession != null}
          onAgentChange={(slug) => {
            setSelectedAgentSlug(slug);
            // Switching to an agent re-seeds runtime/model/effort from that
            // agent's brain. Picking "Default" (slug = null) keeps whatever
            // you already chose in the rows below — don't reset it.
            if (slug) setComposerTouched(false);
          }}
          // 09-assistant 📁 project chip: switches the draft between 临时对话
          // (chat-default) and a project project. The page stores the
          // ``"chat-default"`` sentinel for 临时, so the chip sees ``null``
          // when the active project isn't a project, and a change to
          // ``null`` maps back to the sentinel. Frozen once a session exists.
          footerBar={
            <ExecutionLocationBar
              locked={execBarLocked}
              lockedOriginId={sessionExecOrigin}
              targetId={execTargetId}
              onTargetChange={(tid) => {
                setExecTargetId(tid);
                // Provider ids are backend-local. Clear the old pick while
                // the newly selected service's list is loading.
                setSelectedProviderId(null);
                setSelectedModelId(null);
                // A project belongs to ONE backend — switching location
                // resets the pick back to 临时对话.
                const current = projects.find(
                  (w) => w.id === selectedProjectId,
                );
                if (current && (current.exec_origin ?? "local") !== tid) {
                  setSelectedProjectId("chat-default");
                  setSelectedComposerSkill(null);
                }
                setComposerTouched(true);
              }}
              projects={execBarProjects}
              selectedProjectId={isProjectProject ? selectedProjectId : null}
              onProjectChange={(idOrNull) => {
                const nextTargetId = idOrNull
                  ? (projects.find((w) => w.id === idOrNull)?.exec_origin ??
                    "local")
                  : (execTargetId ?? getDefaultExecutionTarget()?.id);
                if (nextTargetId !== providerTarget?.id) {
                  setSelectedProviderId(null);
                  setSelectedModelId(null);
                }
                setSelectedProjectId(idOrNull ?? "chat-default");
                // Same scope rule as the old toolbar chip: skills don't
                // survive a project-scope change.
                setSelectedComposerSkill(null);
                setComposerTouched(true);
                // A project always has meaningful panel content (file
                // tree / KB / members) — reveal the right panel on pick.
                if (idOrNull) panelSetCollapsed(false);
              }}
            />
          }
          onAddAgent={
            isProjectProject && selectedProjectId
              ? () =>
                  navigate(`/projects/${encodeURIComponent(selectedProjectId)}`)
              : () => setCreateAgentOpen(true)
          }
          // Only the project path greys the send button (a project with no
          // deployed members / no pick). 临时 conversations stay clickable
          // even with an empty library — handleSend then nudges the user to
          // pick/create an agent (10-new-conversation-guidance).
          sendDisabled={
            isProjectProject &&
            !selectedSession &&
            (composerAgents.length === 0 || !selectedAgentSlug)
          }
          providers={composerProviders}
          selectedProviderId={selectedProviderId}
          selectedModelId={selectedModelId}
          runtimes={composerRuntimes}
          selectedRuntimeId={selectedRuntimeId}
          onRuntimeChange={(rt) => {
            setSelectedRuntimeId((rt as RuntimeId | null) ?? null);
            setComposerTouched(true);
          }}
          permissionMode={selectedPermissionMode}
          // Kernel V5+bba3014 live-reconciles ``permission_mode`` on
          // the next Send (Claude live ``set_permission_mode`` mutator
          // + fork-on-rebuild for the bypass tier; Codex per-turn
          // approval/sandbox kwargs; DeepAgents graph rebuild). The
          // pre-bba3014 lock-on-live-session has been dropped — the
          // picker is now interactive for both new and live sessions.
          permissionModeLocked={false}
          onPermissionModeChange={(mode) => {
            setSelectedPermissionMode(mode);
            // For a live session, persist via PATCH so the next Send
            // picks up the new mode. For new-session entry the value
            // is forwarded into ``sessionsApi.create`` from
            // ``handleSend`` instead.
            if (!isNewSession && id) {
              void sessionsApi.updatePermissionMode(id, mode).catch(() => {
                /* non-fatal — surfaced by error toast pipeline */
              });
            }
          }}
          // Effort budget: seeded from the bound agent's brain for a new
          // agent conversation (overridable here — see
          // ``allowAgentBrainOverride`` below), or from Settings for quick
          // chats. For a live session it live-reconciles via PATCH.
          effort={selectedEffort}
          onEffortChange={(level) => {
            setSelectedEffort(level);
            setComposerTouched(true);
            if (!isNewSession && id) {
              void sessionsApi.updateEffort(id, level).catch(() => {
                /* non-fatal — surfaced by error toast pipeline */
              });
            }
          }}
          // Session model is frozen at creation (V5 / ADR-006). Lock the
          // picker the moment a session exists — including freshly-created
          // sessions (e.g. Skill Creator opens a session before the user
          // can type), where the previous ``turns.length > 0`` guard let
          // the picker pretend it was effective. ``modelSelectorUnlocked``
          // is the manual escape hatch the retry-with-different-model flow
          // toggles via ``handleSwitchModel``. The same lock applies to
          // ``runtime`` per ADR-006 + REP-107 — no mid-session swaps.
          modelLocked={selectedSession != null && !modelSelectorUnlocked}
          onModelChange={(chId, mId) => {
            setSelectedProviderId(chId);
            setSelectedModelId(mId);
            setComposerTouched(true);
          }}
          skills={
            isProjectProject ? selectedAgentSkillItems : composerMentionSkills
          }
          onSkillSelect={(s) => {
            const skill = availableSkills.find((sk) => sk.id === s.id) ?? null;
            setSelectedComposerSkill(skill);
          }}
          onKBPick={() => {
            void handleOpenKbPicker();
          }}
          onLocalUpload={handleLocalFilesAttach}
          onFileDrop={handleLocalFilesAttach}
          connectors={connectorOptions}
          selectedConnectorSlugs={selectedMcpSlugs}
          onToggleConnector={toggleConnector}
          connectorsReadOnly={!isNewSession}
          onManageSkills={() => navigate("/skills")}
          onManageConnectors={() => navigate("/connectors")}
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
    </>
  );
}
