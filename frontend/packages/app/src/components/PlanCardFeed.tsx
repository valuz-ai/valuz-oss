/**
 * PlanCardFeed — wires PlanCard rendering into a chat conversation
 * (VALUZ-CHATPLAN S3 / exec-plan §3.8).
 *
 * Lifecycle:
 *  1. The conversation page hands us the chat session id; whenever the
 *     agent calls ``draft_task`` the originator id matches, and the
 *     resulting task lives in the same project.
 *  2. We poll the task event log via ``useTaskEvents`` and accumulate
 *     ``task_plan_update`` snapshots as a chronological list of cards.
 *  3. ``recordPlanVersion`` in the task store gates which card is the
 *     "latest" — older cards render greyed-out with disabled actions.
 *  4. Action callbacks (Execute / Abandon / Open Task Detail) invoke
 *     the matching ``tasksApi`` methods so the chat remains the control
 *     surface for the task.
 *
 * Tasks are tracked by id. The component is intentionally
 * self-contained so the host page only needs to render
 * ``<PlanCardFeed sessionId={...} taskIds={[...]} />`` somewhere in
 * its conversation flow.
 */

import { useCallback, useEffect, useState, type ReactElement } from "react";
import {
  tasksApi,
  useTaskEvents,
  useTaskStore,
  type PlanResponse,
  type PlanSubtask,
  type TaskEvent,
} from "@valuz/core";
import { PlanCard } from "./PlanCard";

interface PlanCardEntry {
  taskId: string;
  taskTitle: string;
  status: string;
  planVersion: number;
  subtasks: PlanSubtask[];
  receivedAt: number;
}

export interface PlanCardFeedProps {
  /** The originating chat session — passed verbatim to ``commit_task``
   * / ``abandon_task`` as ``caller_session_id``. */
  sessionId: string;
  /** Tasks drafted from this conversation. The host page collects these
   * ids (e.g. by watching its own session-event SSE for
   * ``draft_task`` tool results) and passes them in. */
  taskIds: string[];
  /** Project navigator — typically ``router.navigate``. Called with
   * the task detail path when the user clicks Open Task Detail / View
   * Final State. */
  onNavigate?: (path: string) => void;
}

export function PlanCardFeed(props: PlanCardFeedProps): ReactElement | null {
  const { sessionId, taskIds, onNavigate } = props;
  const [cards, setCards] = useState<PlanCardEntry[]>([]);
  const recordPlanVersion = useTaskStore((s) => s.recordPlanVersion);
  const latestPlanIdByTaskId = useTaskStore((s) => s.latestPlanIdByTaskId);

  // Bootstrap one card per known task — useful when the user reloads
  // the chat and the events have already been written.
  useEffect(() => {
    if (taskIds.length === 0) return;
    let cancelled = false;
    void (async () => {
      for (const tid of taskIds) {
        try {
          const detail = await tasksApi.getTask(tid);
          const plan: PlanResponse = await tasksApi.getPlan(tid);
          if (cancelled) return;
          setCards((prev) => {
            if (
              prev.some(
                (c) =>
                  c.taskId === tid && c.planVersion === plan.current_version,
              )
            )
              return prev;
            return [
              ...prev,
              {
                taskId: tid,
                taskTitle: detail.task.title,
                status: detail.task.status,
                planVersion: plan.current_version,
                subtasks: plan.subtasks,
                receivedAt: detail.task.updated_at,
              },
            ];
          });
          recordPlanVersion(tid, plan.current_version);
        } catch {
          // Task not visible yet — useTaskEvents will catch later writes.
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskIds, recordPlanVersion]);

  return (
    <div className="space-y-2">
      {cards.map((card) => (
        <SinglePlanCardWatcher
          key={`${card.taskId}-${card.planVersion}-${card.receivedAt}`}
          card={card}
          sessionId={sessionId}
          isLatest={
            (latestPlanIdByTaskId[card.taskId] ?? 0) === card.planVersion
          }
          onNavigate={onNavigate}
          onNewVersion={(entry) => {
            setCards((prev) => [...prev, entry]);
            recordPlanVersion(entry.taskId, entry.planVersion);
          }}
        />
      ))}
      {sessionId.length === 0 && null}
    </div>
  );
}

interface SingleWatcherProps {
  card: PlanCardEntry;
  sessionId: string;
  isLatest: boolean;
  onNavigate?: (path: string) => void;
  onNewVersion: (entry: PlanCardEntry) => void;
}

function SinglePlanCardWatcher(props: SingleWatcherProps): ReactElement {
  const { card, sessionId, isLatest, onNavigate, onNewVersion } = props;

  // Subscribe to events for THIS task. New task_plan_update events
  // spawn fresh cards (immutable history); status-only events update
  // the existing card's status badge.
  const handleEvent = useCallback(
    (ev: TaskEvent) => {
      if (ev.type === "task_plan_update") {
        const payload = ev.payload as {
          plan_version?: number;
          subtasks?: PlanSubtask[];
          title?: string;
          status?: string;
        };
        const version = payload.plan_version ?? 0;
        if (version <= card.planVersion) return;
        onNewVersion({
          taskId: card.taskId,
          taskTitle: payload.title ?? card.taskTitle,
          status: payload.status ?? card.status,
          planVersion: version,
          subtasks: payload.subtasks ?? card.subtasks,
          receivedAt: ev.created_at,
        });
      }
    },
    [card, onNewVersion],
  );

  // Only the latest card for a task polls — older cards are frozen.
  useTaskEvents(isLatest ? card.taskId : null, handleEvent);

  const handleExecute = useCallback(async () => {
    try {
      await tasksApi.commit(card.taskId, { caller_session_id: sessionId });
    } catch {
      // Surface failure via console; the page-level toast wiring is
      // owned by the conversation page.
      // eslint-disable-next-line no-console
      console.warn("commit_task failed");
    }
  }, [card.taskId, sessionId]);

  const handleAbandon = useCallback(async () => {
    try {
      await tasksApi.abandon(card.taskId, { caller_session_id: sessionId });
    } catch {
      // eslint-disable-next-line no-console
      console.warn("abandon_task failed");
    }
  }, [card.taskId, sessionId]);

  const handleOpenDetail = useCallback(() => {
    onNavigate?.(`/tasks/${card.taskId}`);
  }, [card.taskId, onNavigate]);

  return (
    <PlanCard
      taskId={card.taskId}
      taskTitle={card.taskTitle}
      status={card.status}
      planVersion={card.planVersion}
      subtasks={card.subtasks}
      isLatest={isLatest}
      onExecute={handleExecute}
      onAbandon={handleAbandon}
      onOpenDetail={handleOpenDetail}
    />
  );
}
