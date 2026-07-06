/**
 * Renders the ``AskUserQuestion`` tool as the interactive question card (or a
 * post-answer summary), matching the main conversation view — for surfaces that
 * don't own ConversationPage's full tool-card machinery (the task-detail Lead
 * follow-up chat).
 *
 * All state is derived from the session event stream, using the SAME matching
 * logic as ConversationPage: the kernel parks one clarifying pending at a time,
 * so the most recent ``AskUserQuestion`` ``tool.call.started`` before a
 * ``clarifying_questions`` ``session.requires_action`` is unambiguously that
 * pending's source. Live submits and replay-after-reload both flow through it.
 */
import { useCallback, useMemo, useState, type ReactNode } from "react";

import {
  SESSION_ACTION_RESOLVED_EVENT,
  parseActionResolved,
  sessionsApi,
  useTranslation,
} from "@valuz/core";
import type { SessionEventDTO } from "@valuz/shared";
import {
  AskUserQuestionCard,
  UserAnswerSummaryCard,
  parseAskUserQuestionInput,
} from "@valuz/ui";
import { toast } from "sonner";

type ToolLike = {
  id: string;
  title: string;
  input?: string;
  output?: string;
};

const REQUIRES_ACTION = "session.requires_action";
const CLARIFYING = "clarifying_questions";

function isAskName(name: string | undefined): boolean {
  // Match every MCP namespacing the runtimes produce: bare
  // ("AskUserQuestion"), Claude-style ("mcp__harness__AskUserQuestion"),
  // or slash-style ("harness/AskUserQuestion" — the codex runtime).
  return (
    name === "AskUserQuestion" ||
    (typeof name === "string" &&
      (name.endsWith("__AskUserQuestion") || name.endsWith("/AskUserQuestion")))
  );
}

export function useAskUserQuestionCards({
  events,
  sessionId,
}: {
  events: SessionEventDTO[];
  sessionId: string | null;
}): { renderToolCall: (tool: ToolLike) => ReactNode | null } {
  const { t } = useTranslation();
  // Optimistic answers: stashed on submit so the card flips to the summary on
  // the same tick; ``answersByToolId`` (event-derived, kernel-authoritative)
  // takes precedence once the paired ``action_resolved`` frame lands.
  const [localAnswers, setLocalAnswers] = useState<
    Record<string, Record<string, string | string[]>>
  >({});

  // tool_use id -> resolved answers, matched via pending_id.
  const answersByToolId = useMemo(() => {
    const out: Record<string, Record<string, string | string[]>> = {};
    const pendingIdToToolId = new Map<string, string>();
    let lastAskToolId: string | null = null;
    for (const ev of events) {
      const type = ev.event.event_type;
      const payload = ev.event.payload ?? {};
      if (type === "tool.call.started") {
        if (isAskName(payload.name)) {
          const toolUseId = payload.tool_use_id || payload.id;
          if (toolUseId) lastAskToolId = toolUseId;
        }
      } else if (type === REQUIRES_ACTION) {
        if (
          payload.subject === CLARIFYING &&
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
      if (toolId) out[toolId] = ar.answers;
    }
    return out;
  }, [events]);

  // The one still-open clarifying pending — what a submit resolves.
  const currentPendingId = useMemo(() => {
    const resolved = new Set<string>();
    for (const ev of events) {
      if (ev.event.event_type !== SESSION_ACTION_RESOLVED_EVENT) continue;
      const ar = parseActionResolved(ev);
      if (ar) resolved.add(ar.pending_id);
    }
    let pending: string | null = null;
    for (const ev of events) {
      if (ev.event.event_type !== REQUIRES_ACTION) continue;
      const payload = ev.event.payload ?? {};
      if (
        payload.subject === CLARIFYING &&
        payload.pending_id &&
        !resolved.has(payload.pending_id)
      ) {
        pending = payload.pending_id;
      }
    }
    return pending;
  }, [events]);

  const submit = useCallback(
    (toolId: string, answers: Record<string, string>) => {
      if (!sessionId || !currentPendingId) {
        toast.error(t("common.error" as Parameters<typeof t>[0]));
        return;
      }
      setLocalAnswers((prev) => ({ ...prev, [toolId]: answers }));
      sessionsApi
        .submitAction(sessionId, {
          pending_id: currentPendingId,
          decision: "answer",
          answers,
        })
        .catch((err: unknown) => {
          setLocalAnswers((prev) => {
            const next = { ...prev };
            delete next[toolId];
            return next;
          });
          toast.error(
            err instanceof Error
              ? err.message
              : t("common.saveFailed" as Parameters<typeof t>[0]),
          );
        });
    },
    [sessionId, currentPendingId, t],
  );

  const renderToolCall = useCallback(
    (tool: ToolLike): ReactNode | null => {
      if (!isAskName(tool.title)) return null;
      const parsed = parseAskUserQuestionInput(tool.input);
      if (!parsed || parsed.questions.length === 0) return null;
      const answers = answersByToolId[tool.id] ?? localAnswers[tool.id];
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
          onSubmit={(submitted) => submit(tool.id, submitted)}
        />
      );
    },
    [answersByToolId, localAnswers, submit],
  );

  return { renderToolCall };
}
