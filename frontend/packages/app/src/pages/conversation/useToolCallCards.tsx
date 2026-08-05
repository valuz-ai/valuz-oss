import { useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Sparkles } from "lucide-react";
import {
  SESSION_ACTION_RESOLVED_EVENT,
  parseActionResolved,
  useTranslation,
  type SessionEventDTO,
  type WorkflowState,
  type useIncrementalTurns,
} from "@valuz/core";
import {
  AgentProposalCard,
  AskUserQuestionCard,
  AutomationProposalCard,
  AutomationToolCard,
  GenerativeUICard,
  SkillSubmissionCard,
  UserAnswerSummaryCard,
  WorkflowProgressCard,
  parseAskUserQuestionInput,
  parseAutomationToolOutput,
} from "@valuz/ui";
import { usePlatform } from "@valuz/app/platform";
import { LiveTaskCard } from "../../components/LiveTaskCard";
import type { computePlanAnchors } from "../conversation-plan-anchors";
import {
  automationTriggerSummary,
  isToolNamed,
  parseAutomationCreateInput,
  renderChatplanStatusPill,
} from "./tool-card-helpers";
import { useToolCallCardActions } from "./useToolCallCardActions";

type ToolCallCardsParams = {
  events: SessionEventDTO[];
  turns: ReturnType<typeof useIncrementalTurns>;
  isBusy: boolean;
  selectedSessionId: string | null;
  selectedSessionIdRef: { current: string | null };
  /** ``selectedSession?.name`` — labels the "bound to project" chip on
   *  confirmed submission/proposal cards. */
  selectedSessionName: string | null;
  planAnchors: ReturnType<typeof computePlanAnchors>;
  workflowStates: Map<string, WorkflowState>;
  askUserQuestionLocalAnswers: Record<
    string,
    Record<string, string | string[]>
  >;
  askUserQuestionSubmitRef: {
    current: (toolId: string, answers: Record<string, string>) => void;
  };
};

/**
 * ── ``submit_skill`` tool_use → submission card wiring ──────────────
 *
 * Owns every special-cased tool-call card in the conversation timeline:
 * skill submissions, agent proposals, automation proposals, chatplan
 * pills, AskUserQuestion cards, generative UI, and workflow progress.
 * The page renders through the returned ``renderToolCall`` /
 * ``isToolCardFoldable``; the per-card confirm/dismiss state lives here.
 */
export function useToolCallCards({
  events,
  turns,
  isBusy,
  selectedSessionId,
  selectedSessionIdRef,
  selectedSessionName,
  planAnchors,
  workflowStates,
  askUserQuestionLocalAnswers,
  askUserQuestionSubmitRef,
}: ToolCallCardsParams) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { revealInFinder } = usePlatform();

  const {
    submissionStates,
    proposalStates,
    automationProposalStates,
    handleConfirmSubmission,
    handleDismissSubmission,
    handleConfirmProposal,
    handleDismissProposal,
    handleConfirmAutomation,
    handleDismissAutomation,
  } = useToolCallCardActions({
    turns,
    isBusy,
    selectedSessionId,
    selectedSessionIdRef,
    selectedSessionName,
  });

  // Once ``action_resolved (decision="answer")`` lands for a parked
  // AskUserQuestion, we swap the interactive ``AskUserQuestionCard``
  // for a ``UserAnswerSummaryCard`` that shows each question → answer
  // pair. Derived from the event stream so live submits AND replay-
  // after-reload both flow through the same matching logic.
  //
  // We match by ``pending_id`` (carried on both
  // ``session.requires_action`` and ``session.action_resolved``) plus
  // a temporal pairing of the immediately-preceding AskUserQuestion
  // ``tool.call.started`` — kernel parks one clarifying pending at a
  // time per session (orchestrator.submit_action raises
  // ``PendingActionConflictError`` otherwise), so the most recent
  // AskUserQuestion tool_use before a ``clarifying_questions``
  // requires_action is unambiguously its source. This avoids relying
  // on ``message_id`` reaching ``tool.call.started`` over live SSE,
  // which has historically been fragile across the kernel
  // ``_MessageIdStampSink`` → bus → broadcast → SSE chain.
  const askUserQuestionAnswersByToolId = useMemo(() => {
    const out: Record<string, Record<string, string | string[]>> = {};
    const pendingIdToToolId = new Map<string, string>();
    let lastAskToolId: string | null = null;
    for (const ev of events) {
      const type = ev.event.event_type;
      const payload = ev.event.payload ?? {};
      if (type === "tool.call.started") {
        const name = payload.name;
        const isAsk = isToolNamed(name, "AskUserQuestion");
        if (isAsk) {
          const toolUseId = payload.tool_use_id || payload.id;
          if (toolUseId) lastAskToolId = toolUseId;
        }
      } else if (type === "session.requires_action") {
        if (
          payload.subject === "clarifying_questions" &&
          payload.pending_id &&
          lastAskToolId
        ) {
          pendingIdToToolId.set(payload.pending_id, lastAskToolId);
        }
      }
    }
    for (const ev of events) {
      if (ev.event.event_type !== SESSION_ACTION_RESOLVED_EVENT) continue;
      const ar = parseActionResolved(ev);
      if (!ar || ar.decision !== "answer") continue;
      const toolId = pendingIdToToolId.get(ar.pending_id);
      if (toolId) {
        out[toolId] = ar.answers;
      }
    }
    return out;
  }, [events]);

  // Mark an AskUserQuestion card as *foldable* so it collapses away with the
  // process trail once the turn ends — but ONLY after it's been answered. An
  // unanswered, still-pending question stays pinned/visible (the run parks and
  // ``sending`` flips to false while awaiting the answer, which would otherwise
  // auto-fold and hide the card the user needs to act on). Other overridden
  // cards (proposals, skill submission, workflow/task) are never foldable.
  const isToolCardFoldable = useCallback(
    (tool: { id: string; title?: string }): boolean => {
      const name = tool.title ?? "";
      const isAsk = isToolNamed(name, "AskUserQuestion");
      if (!isAsk) return false;
      return Boolean(
        askUserQuestionAnswersByToolId[tool.id] ??
        askUserQuestionLocalAnswers[tool.id],
      );
    },
    [askUserQuestionAnswersByToolId, askUserQuestionLocalAnswers],
  );

  const renderToolCall = useCallback(
    (tool: {
      id: string;
      title: string;
      input?: string;
      output?: string;
      status?: string;
      thinking?: string;
    }) => {
      const name = tool.title || "";

      // generate_ui — generative UI. The MCP tool returns OpenUI Lang as
      // ``tool.output`` (growing token-by-token while running, as the host
      // forwards ephemeral text_deltas as tool_output_delta) plus a live
      // reasoning stream on ``tool.thinking`` (tool.call.thinking_delta).
      // Render with OpenUI's <Renderer> via GenerativeUICard, including while
      // running so the UI paints progressively and the thinking phase shows
      // as dimmed progress; only error falls through (return null) to the
      // generic ToolCallCard so the failure text stays visible.
      if (isToolNamed(name, "generate_ui")) {
        if (tool.status === "error") return null;
        return (
          <GenerativeUICard
            openui={tool.output}
            status={tool.status === "running" ? "running" : "success"}
            thinking={tool.thinking}
          />
        );
      }

      // Claude dynamic-workflow launch → WorkflowProgressCard. The kernel
      // streams ``session.workflow_progress`` snapshots keyed by this tool's
      // tool_use_id while the background runtime executes; we render the live
      // overview (status + agents-done/total + per-agent list) in place of the
      // opaque generic tool card. When no snapshot exists yet (history replay /
      // reconnect — the progress is live-only and never persisted), fall through
      // to the generic ToolCallCard so the launch still shows.
      if (name === "Workflow") {
        const wfState = workflowStates.get(tool.id);
        if (wfState) {
          return (
            <WorkflowProgressCard
              state={wfState}
              fallbackTitle={name}
              onOpenStateFile={revealInFinder}
            />
          );
        }
      }

      // ADR-021: automation tool result → AutomationToolCard. The MCP
      // server returns a structured JSON blob as ``tool.output``; we
      // parse it and hand off to the card. If the output is missing
      // (still running) or unparseable, we fall through to the generic
      // tool renderer. ``isToolNamed`` covers every runtime's MCP
      // namespacing (bare / Claude ``mcp__server__tool`` / codex
      // ``server/tool``).
      const isAutomation = isToolNamed(name, "automation");
      if (isAutomation) {
        const result = parseAutomationToolOutput(tool.output);
        const openInAutomation = (automationId: string) => {
          // The automation page is at ``/automations`` and reads
          // ``?automation=<id>`` for direct linking. Soft navigation keeps the
          // conversation mounted in the project sidebar.
          navigate(
            `/automations?automation=${encodeURIComponent(automationId)}`,
          );
        };

        // ``create`` PROPOSES — render a propose→confirm card (mirrors
        // propose_agent). Every other action keeps the read-only tool card.
        // We render primarily from the INPUT (always clean) and enrich from the
        // OUTPUT proposal when it's parseable — the Valuz/DeepAgents runtime
        // wraps the output so ``result`` is null there, but the card must still
        // render and be confirmable.
        const inputSpec = parseAutomationCreateInput(tool.input);
        const proposal = result?.proposal ?? null;
        const isCreate = result?.action === "create" || inputSpec != null;
        if (isCreate) {
          // The create tool rejected the proposal (bad cron / task-in-chat).
          const validationError = result && !result.ok ? result.message : null;
          // Nothing to show yet (no parsed input, no proposal, no error) —
          // generic renderer until something lands.
          if (!inputSpec && !proposal && !validationError) return null;
          const cardName = proposal?.name ?? inputSpec?.name ?? "";
          const cardPrompt =
            proposal?.prompt_template ?? inputSpec?.prompt_template;
          const confirmTrigger =
            proposal?.trigger ?? inputSpec?.trigger ?? null;
          const cardTriggerHuman =
            proposal?.trigger_human_readable ??
            automationTriggerSummary(confirmTrigger, t);
          const cardActionKind =
            proposal?.action_kind ?? inputSpec?.action_kind ?? "chat";
          const cardWorktree =
            proposal?.worktree ?? inputSpec?.worktree ?? false;
          const cardAgentName =
            proposal?.agent_name ?? inputSpec?.agent_slug ?? null;
          const entry = automationProposalStates[tool.id] || {
            state: "pending" as const,
          };
          return (
            <AutomationProposalCard
              name={cardName}
              promptTemplate={cardPrompt}
              triggerHuman={cardTriggerHuman}
              agentName={cardAgentName}
              actionKind={cardActionKind}
              worktree={cardWorktree}
              state={entry.state}
              errorMessage={entry.errorMessage}
              validationError={validationError}
              onConfirm={() => {
                if (!confirmTrigger || !cardName) return;
                void handleConfirmAutomation(tool.id, {
                  name: cardName,
                  prompt_template: cardPrompt ?? "",
                  trigger: confirmTrigger,
                  agent_slug: proposal?.agent_slug ?? inputSpec?.agent_slug,
                  action_kind: cardActionKind,
                  worktree: cardWorktree,
                });
              }}
              onDismiss={() => handleDismissAutomation(tool.id)}
            />
          );
        }

        if (result) {
          return (
            <AutomationToolCard
              result={result}
              onOpenInAutomation={openInAutomation}
            />
          );
        }
        return null;
      }

      // VALUZ-CHATPLAN — the LATEST plan_task / modify_plan tool for a
      // given task renders the rich, SSE-subscribed LiveTaskCard. Every
      // other chatplan tool result (draft, earlier plan writes, commit,
      // abandon, inject) renders a compact polished pill. This lands the
      // "current state" surface at the most recent plan write so the
      // user sees subtask progress + execute/abandon controls without
      // scrolling.
      const richPlanTaskId = planAnchors.taskByRichTool.get(tool.id);
      if (richPlanTaskId) {
        return (
          <LiveTaskCard
            taskId={richPlanTaskId}
            callerSessionId={selectedSessionId ?? ""}
            onNavigate={navigate}
          />
        );
      }
      const chatplanPill = renderChatplanStatusPill(name, tool, t, navigate);
      if (chatplanPill) return chatplanPill;

      // v3 (M10 附录 E): create_task launcher result → a compact card with
      // the task title + a link into the task detail page. The handler
      // returns ``{task_id, title, status}`` but the kernel wraps tool output
      // as a content-block repr (``[{'type': 'text', 'text': '{...}'}]``), so
      // extract the fields by regex rather than JSON.parse-ing the whole blob.
      const isCreateTask = isToolNamed(name, "create_task");
      if (isCreateTask && tool.output) {
        const idMatch = tool.output.match(/"task_id"\s*:\s*"([^"]+)"/);
        const taskId = idMatch?.[1];
        if (taskId) {
          const titleMatch = tool.output.match(
            /"title"\s*:\s*"((?:[^"\\]|\\.)*)"/,
          );
          let taskTitle = "";
          if (titleMatch?.[1]) {
            try {
              taskTitle = JSON.parse(`"${titleMatch[1]}"`);
            } catch {
              taskTitle = titleMatch[1];
            }
          }
          return (
            <div className="flex items-center gap-3 rounded-lg border border-surface-border bg-surface-soft px-3 py-2.5 text-sm">
              <Sparkles className="h-4 w-4 shrink-0 text-brand" />
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="truncate font-medium text-ink-heading">
                  {t("conversation.taskCreated" as Parameters<typeof t>[0])}
                </span>
                {taskTitle && (
                  <span className="truncate text-xs text-ink-body">
                    {taskTitle}
                  </span>
                )}
              </div>
              <button
                type="button"
                className="shrink-0 rounded-md border border-surface-border px-2 py-1 text-xs text-ink-body transition-colors hover:bg-surface-muted hover:text-ink-heading"
                onClick={() => navigate(`/tasks/${encodeURIComponent(taskId)}`)}
              >
                {t("conversation.openTask" as Parameters<typeof t>[0])}
              </button>
            </div>
          );
        }
      }

      // AskUserQuestion tool. Two render modes:
      //   - Pre-answer: interactive ``AskUserQuestionCard`` with the
      //     option chooser.
      //   - Post-answer: compact ``UserAnswerSummaryCard`` showing
      //     each question → answer pair. The interactive card is
      //     dropped entirely so the turn transcript stays clean.
      //
      // Answers source priority: (1) kernel-confirmed
      // ``askUserQuestionAnswersByToolId`` (authoritative, populated
      // from ``session.action_resolved`` SSE — works for live + replay
      // uniformly via the pending_id bridge). (2)
      // ``askUserQuestionLocalAnswers`` (optimistic, populated on
      // submit click). The local mirror keeps the card swap latency
      // at zero — the user never sees the read-only fill-content card
      // between submit and the kernel ack.
      const isAskUserQuestion = isToolNamed(name, "AskUserQuestion");
      if (isAskUserQuestion) {
        const parsed = parseAskUserQuestionInput(tool.input);
        if (parsed && parsed.questions.length > 0) {
          const answers =
            askUserQuestionAnswersByToolId[tool.id] ??
            askUserQuestionLocalAnswers[tool.id];
          if (answers) {
            return (
              <UserAnswerSummaryCard
                questions={parsed.questions}
                answers={answers}
              />
            );
          }
          return (
            <AskUserQuestionCard
              questions={parsed.questions}
              onSubmit={(submitted) =>
                askUserQuestionSubmitRef.current(tool.id, submitted)
              }
            />
          );
        }
        return null;
      }

      // ``propose_agent`` — natural-language agent creation. Renders a card
      // letting the user create + deploy the proposed agent. Tool name comes
      // through plain or MCP-bridged (``mcp__harness__propose_agent``).
      const isProposeAgent = isToolNamed(name, "propose_agent");
      if (isProposeAgent) {
        let spec: {
          name?: string;
          instructions?: string;
          description?: string;
          runtime?: string;
          model?: string;
          effort?: string;
          skills?: string[];
          connectors?: string[];
        } = {};
        if (tool.input) {
          try {
            spec =
              typeof tool.input === "string"
                ? JSON.parse(tool.input)
                : tool.input;
          } catch {
            // Partial/malformed args (still streaming) — render with blanks;
            // the confirm button stays disabled until a name is present.
          }
        }
        const entry = proposalStates[tool.id] || { state: "pending" as const };
        const confirmSpec = {
          name: spec.name || "",
          instructions: spec.instructions || "",
          description: spec.description,
          runtime: spec.runtime,
          model: spec.model,
          skills: Array.isArray(spec.skills) ? spec.skills : [],
          connectors: Array.isArray(spec.connectors) ? spec.connectors : [],
        };
        return (
          <AgentProposalCard
            name={confirmSpec.name}
            description={spec.description}
            instructions={confirmSpec.instructions}
            runtime={spec.runtime || "claude_agent"}
            model={spec.model || "claude-sonnet-4-6"}
            skills={confirmSpec.skills}
            connectors={confirmSpec.connectors}
            state={entry.state}
            errorMessage={entry.errorMessage}
            deployedProjectLabel={entry.deployedProjectLabel}
            onConfirm={() => void handleConfirmProposal(tool.id, confirmSpec)}
            onDismiss={() => handleDismissProposal(tool.id)}
          />
        );
      }

      const isSubmit = isToolNamed(name, "submit_skill");
      if (!isSubmit) return null;
      let parsed: {
        slug?: string;
        summary?: string;
        change_kind?: "create" | "update";
        files_touched?: string[];
      } = {};
      if (tool.input) {
        try {
          parsed =
            typeof tool.input === "string"
              ? JSON.parse(tool.input)
              : tool.input;
        } catch {
          // Malformed args — fall through to defaults below; the user
          // will still see the slug/summary fields blank.
        }
      }
      const slug = parsed.slug || "(unknown-slug)";
      const summary = parsed.summary;
      const changeKind: "create" | "update" =
        parsed.change_kind === "update" ? "update" : "create";
      const filesTouched = Array.isArray(parsed.files_touched)
        ? parsed.files_touched
        : [];
      // Initial state on first render is "awaiting_files" — the scan
      // effect above flips us to "pending" once SKILL.md actually
      // exists in the staging dir. Pre-existing state (after user
      // interactions) takes precedence.
      const entry = submissionStates[tool.id] || {
        state: "awaiting_files" as const,
      };
      return (
        <SkillSubmissionCard
          slug={slug}
          summary={summary}
          changeKind={changeKind}
          filesTouched={filesTouched}
          state={entry.state}
          errorMessage={entry.errorMessage}
          boundToProjectLabel={entry.boundToProjectLabel}
          stagedFiles={entry.stagedFiles}
          stagingPath={entry.stagingPath}
          onConfirm={() =>
            void handleConfirmSubmission(
              tool.id,
              slug,
              summary,
              changeKind,
              filesTouched,
            )
          }
          onDismiss={() => void handleDismissSubmission(tool.id, slug)}
        />
      );
    },
    [
      submissionStates,
      handleConfirmSubmission,
      handleDismissSubmission,
      proposalStates,
      handleConfirmProposal,
      handleDismissProposal,
      automationProposalStates,
      handleConfirmAutomation,
      handleDismissAutomation,
      askUserQuestionAnswersByToolId,
      askUserQuestionLocalAnswers,
      askUserQuestionSubmitRef,
      planAnchors,
      workflowStates,
      revealInFinder,
      selectedSessionId,
      navigate,
      t,
    ],
  );

  return { isToolCardFoldable, renderToolCall };
}
