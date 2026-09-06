import { useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Sparkles } from "lucide-react";
import {
  SESSION_ACTION_RESOLVED_EVENT,
  SlotRenderer,
  parseOperationToolOutput,
  parseActionResolved,
  useTranslation,
  type SessionEventDTO,
  type SessionMessageHostRef,
  type WorkflowState,
  type useIncrementalTurns,
} from "@valuz/core";
import {
  AgentProposalCard,
  AskUserQuestionCard,
  AutomationProposalCard,
  AutomationToolCard,
  PlaybookOperationCard,
  GenerativeUICard,
  SkillSubmissionCard,
  UserAnswerSummaryCard,
  WorkflowProgressCard,
  extractUiArtifactReceipt,
  parseAskUserQuestionInput,
  parseAutomationToolOutput,
} from "@valuz/ui";
import { usePlatform } from "@valuz/app/platform";
import { LiveTaskCard } from "../../components/LiveTaskCard";
import type { computePlanAnchors } from "../conversation-plan-anchors";
import {
  automationProposalGate,
  automationTriggerSummary,
  hostDocumentFileName,
  isToolNamed,
  normalizeAutomationTrigger,
  parseAutomationCreateInput,
  renderChatplanStatusPill,
  resolveGenUiHost,
} from "./tool-card-helpers";
import { skillSubmissionView } from "./skill-submission-view";
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
  /** Host this conversation panel lives at, when it is a product surface's
   *  panel rather than the system conversation page — the same value the
   *  page threads onto ``sendMessage``'s ``host_ref``. See the
   *  ``generate_ui`` branch of ``renderToolCall`` for why the tool argument
   *  alone is not a sound answer. */
  hostRef?: SessionMessageHostRef | null;
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
  hostRef,
}: ToolCallCardsParams) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { revealInFinder } = usePlatform();

  const {
    submissionStates,
    proposalStates,
    automationProposalStates,
    operationStates,
    operationBusy,
    handleConfirmSubmission,
    handleDismissSubmission,
    handleConfirmProposal,
    handleDismissProposal,
    handleConfirmAutomation,
    handleDismissAutomation,
    handleConfirmOperation,
    handleCancelOperation,
    handleRequestChangesOperation,
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

      // generate_ui — generative UI. The MCP tool returns an A2UI stream as
      // ``tool.output`` (growing token-by-token while running, as the host
      // forwards ephemeral text_deltas as tool_output_delta) plus a live
      // reasoning stream on ``tool.thinking`` (tool.call.thinking_delta).
      // Render with A2UIRenderer via GenerativeUICard, including while
      // running so the UI paints progressively and the thinking phase shows
      // as dimmed progress; only error falls through (return null) to the
      // generic ToolCallCard so the failure text stays visible.
      if (isToolNamed(name, "generate_ui")) {
        if (tool.status === "error") return null;
        // The sink receipt trailer (persisted inside the tool result) must
        // never reach the renderer. The slot below is the edition seam for
        // host-targeted generation UX (live progress mirrored into a product
        // host, adopt/bind proposal on completion) — it renders during the
        // run too, with the streaming body; OSS registers nothing there, so
        // the card is unchanged for OSS.
        const { receipt, body } = extractUiArtifactReceipt(tool.output);
        // A generation that belongs to a product host renders in that host
        // (the edition slot mirrors it there); the conversation keeps only
        // the slot's compact status/adopt card — the full inline card would
        // duplicate the same painting at panel width. Plain in-conversation
        // visuals keep the inline card. OSS declares neither signal, so OSS
        // behavior is unchanged.
        //
        // ``resolveGenUiHost`` mirrors the server's own resolution order —
        // the tool argument is an override, the panel's ``hostRef`` is the
        // deterministic floor. See its docstring for why the argument alone
        // is not a sound signal.
        const resolvedHost = resolveGenUiHost(tool.input, hostRef);
        return (
          <>
            {resolvedHost ? null : (
              <GenerativeUICard
                a2ui={tool.output === undefined ? undefined : body}
                status={tool.status === "running" ? "running" : "success"}
                thinking={tool.thinking}
              />
            )}
            <SlotRenderer
              name="genui.artifact-binding"
              context={{
                receipt,
                toolUseId: tool.id,
                status: tool.status === "running" ? "running" : "success",
                output: body,
                input: tool.input,
                // The reasoning stream (``tool.call.thinking_delta``), which
                // rides its own channel precisely so it never contaminates the
                // document. A host-targeted run paints the page in the host, so
                // the conversation's job is to show the WORK — and the thinking
                // is the readable half of that; the document is machine text.
                thinking: tool.thinking,
                // The host this generation belongs to, already resolved the
                // same way the server resolves it — so the edition slot does
                // not have to re-derive it from the tool argument and reach
                // the wrong answer whenever the model omitted it.
                hostRef: resolvedHost,
              }}
            />
          </>
        );
      }

      // Any OTHER tool whose input touches the host page's DOCUMENT FILE is
      // page work too — an agent editing the a2ui.jsonl directly and
      // delivering it via ``deliver_artifacts`` makes a new page version
      // exactly like a generation, and without this branch the workbench
      // never hears about it (versions grew silently: no card, no live
      // state, no refresh). The edition slot gets the same contract as a
      // generate_ui run: ``running`` mirrors 正在修改 into the host, and the
      // receipt the deliver tool now appends drives the adopt card. Errors
      // fall through to the generic card so the failure text stays visible.
      if (
        hostRef &&
        typeof tool.input === "string" &&
        tool.status !== "error"
      ) {
        const docName = hostDocumentFileName(hostRef);
        if (docName && tool.input.includes(docName)) {
          const { receipt } = extractUiArtifactReceipt(tool.output);
          return (
            <SlotRenderer
              name="genui.artifact-binding"
              context={{
                receipt,
                toolUseId: tool.id,
                status: tool.status === "running" ? "running" : "success",
                // The document is not in this tool's result — the edition
                // slot fetches the recorded revision when it needs the body.
                output: "",
                input: tool.input,
                thinking: tool.thinking,
                hostRef,
              }}
            />
          );
        }
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

      // Generic edition-domain mutation seam. Industry editions expose an
      // always-on MCP tool named ``domain_operation`` and register the actual
      // persisted proposal card in this slot. The host owns only placement;
      // it neither knows Finance entities nor duplicates their confirm API.
      if (isToolNamed(name, "domain_operation")) {
        return (
          <SlotRenderer
            name="domain.operation-card"
            context={{ tool }}
          />
        );
      }

      const isPlaybook = isToolNamed(name, "playbook");
      if (isPlaybook) {
        const result = parseOperationToolOutput(tool.output);
        const snapshot = result?.operation;
        if (snapshot) {
          const operation = operationStates[snapshot.id] ?? snapshot;
          return (
            <PlaybookOperationCard
              operation={operation}
              busy={operationBusy[operation.id] ?? null}
              onConfirm={() => void handleConfirmOperation(operation)}
              onCancel={() => void handleCancelOperation(operation)}
              onRequestChanges={(comment) =>
                void handleRequestChangesOperation(operation, comment)
              }
              onOpenPlaybook={(definitionId) =>
                navigate(
                  `/playbooks?definition=${encodeURIComponent(definitionId)}`,
                )
              }
            />
          );
        }
        // Read-only queries and run lifecycle actions do not create an
        // OperationRecord. Let them reach the generic tool renderer so the
        // user can still inspect the Agent's Playbook call and result.
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
          // Only a SERVER-validated proposal is confirmable. ``result`` is
          // null while the tool is still running (output not delivered yet)
          // and when its output can't be parsed — in both cases there is
          // nothing the server has approved, so the card stays read-only.
          // (``parseAutomationToolOutput`` now unwraps the kernel's
          // content-block envelope, so a wrapped ok=false result is no
          // longer mistaken for "no result".)
          const gate = automationProposalGate(result, tool.status);
          const validationError = gate.rejected
            ? tool.status === "error"
              ? tool.output ||
                t("automation.proposalFailed" as Parameters<typeof t>[0])
              : result?.message ?? null
            : null;
          const submittable = gate.submittable;
          // Nothing to show yet (no parsed input, no proposal, no error) —
          // generic renderer until something lands.
          if (!inputSpec && !proposal && !validationError) return null;
          const cardName = proposal?.name ?? inputSpec?.name ?? "";
          const cardPrompt =
            proposal?.prompt_template ?? inputSpec?.prompt_template;
          const confirmTrigger = normalizeAutomationTrigger(
            proposal?.trigger ?? inputSpec?.trigger ?? null,
          );
          const cardTriggerHuman =
            proposal?.trigger_human_readable ??
            automationTriggerSummary(confirmTrigger, t);
          const cardActionKind =
            proposal?.action_kind ?? inputSpec?.action_kind ?? "chat";
          const cardWorktree =
            proposal?.worktree ?? inputSpec?.worktree ?? false;
          const cardAgentName =
            proposal?.agent_name ?? inputSpec?.agent_slug ?? null;
          const cardPlaybookDefinitionId =
            proposal?.playbook_definition_id ??
            inputSpec?.playbook_definition_id ??
            null;
          const cardPlaybookVersion =
            proposal?.playbook_version ?? inputSpec?.playbook_version ?? null;
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
              playbookVersion={cardPlaybookVersion}
              state={entry.state}
              errorMessage={entry.errorMessage}
              validationError={validationError}
              submittable={submittable}
              onConfirm={() => {
                if (!submittable || !confirmTrigger || !cardName) return;
                void handleConfirmAutomation(tool.id, {
                  name: cardName,
                  prompt_template: cardPrompt ?? "",
                  trigger: confirmTrigger,
                  agent_slug: proposal?.agent_slug ?? inputSpec?.agent_slug,
                  action_kind: cardActionKind,
                  worktree: cardWorktree,
                  playbook_definition_id: cardPlaybookDefinitionId,
                  playbook_version: cardPlaybookVersion,
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
      // Operation flow: the tool result carries a ``skill.submit`` record,
      // so the card's state is the server's — it survives a reload, and
      // both the staged file list and the collision the user has to
      // resolve come from the proposal the user is actually approving.
      const submitResult = parseOperationToolOutput(tool.output);
      const submitSnapshot = submitResult?.operation;
      if (submitSnapshot) {
        const operation = operationStates[submitSnapshot.id] ?? submitSnapshot;
        const view = skillSubmissionView(
          operation,
          operationBusy[operation.id] ?? null,
        );
        return (
          <SkillSubmissionCard
            slug={view.slug || slug}
            summary={view.summary ?? summary}
            changeKind={view.changeKind ?? changeKind}
            filesTouched={filesTouched}
            state={view.state}
            errorMessage={view.errorMessage}
            stagedFiles={view.stagedFiles}
            stagingPath={view.stagingPath}
            nextVersion={view.nextVersion}
            savedVersion={view.savedVersion}
            conflictKind={view.conflictKind}
            onConfirm={(decision) =>
              void handleConfirmOperation(
                operation,
                decision as Record<string, unknown> | undefined,
              )
            }
            onDismiss={() => void handleCancelOperation(operation)}
          />
        );
      }

      // Legacy flow (cards from sessions that predate the operation
      // record): state is inferred from the staging scan.
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
      operationStates,
      operationBusy,
      handleConfirmOperation,
      handleCancelOperation,
      handleRequestChangesOperation,
      askUserQuestionAnswersByToolId,
      askUserQuestionLocalAnswers,
      askUserQuestionSubmitRef,
      planAnchors,
      workflowStates,
      revealInFinder,
      selectedSessionId,
      hostRef,
      navigate,
      t,
    ],
  );

  return { isToolCardFoldable, renderToolCall };
}
