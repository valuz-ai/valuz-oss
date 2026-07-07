import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Clock3, ListChecks, MessageSquare } from "lucide-react";
import {
  DeleteConfirmDialog,
  Badge,
  PageHeader,
  Tabs,
  TabsList,
  TabsTrigger,
} from "@valuz/ui";
import {
  buildTurns,
  sessionsApi,
  useDecisionPending,
  useRunningRuns,
  useSessionEvents,
  useSessionStore,
  useTranslation,
  useProjectStore,
  useActivityFeed,
  type RunSummary,
} from "@valuz/core";
import {
  buildSegments,
  summarizeSegmentPhrase,
  type SessionEventDTO,
} from "@valuz/shared";
import { ActivityFeedList } from "@valuz/app/components";
import { AttentionGroup } from "../components/DecisionInbox";
import { useProjectOutlet } from "@valuz/app/layout";

type SourceFilter = "all" | "chat" | "task" | "automation";

const tk = (key: string) =>
  key as Parameters<ReturnType<typeof useTranslation>["t"]>[0];

// Label key per run status; colors/style come from the shared Badge.
const STATUS_LABEL_KEY: Record<string, string> = {
  running: "activity.statusRunning",
  paused: "activity.statusPaused",
  idle: "activity.statusIdle",
  completed: "activity.statusCompleted",
  failed: "activity.statusFailed",
  stopped: "activity.statusStopped",
  blocked: "activity.statusBlocked",
};

const activityRunStatusVariant = (
  status: string,
): "brand" | "success" | "warning" | "error" | "outline" => {
  if (status === "running") return "brand";
  if (status === "completed" || status === "idle") return "success";
  if (status === "failed") return "error";
  if (status === "blocked" || status === "paused") return "warning";
  return "outline";
};

// ──────────────────────────────────────────────────────────────────────────
// Live running card — subscribes to its session's SSE event stream and
// renders the most recent milestones as a mini event log.
// ──────────────────────────────────────────────────────────────────────────

type Translator = ReturnType<typeof useTranslation>["t"];

interface DashboardLine {
  key: string;
  text: string;
}

/** Return the LAST portion of ``text`` prefixed with ``…`` so the visible
 * (truncated-by-CSS) row shows the most recent tokens, not the frozen
 * first prefix. Used for the actively-streaming line of a running card.
 *
 * CSS-only approaches (``direction: rtl`` + ``text-overflow: ellipsis``)
 * either don't compose with ``line-clamp-1`` (which uses
 * ``-webkit-box``) or get overridden by ``unicode-bidi``. JS slicing is
 * uglier but actually works across mixed CJK / Latin / emoji content.
 *
 * Limit picked per script — CJK glyphs are ~2× the width of ASCII at the
 * dashboard's ``text-xs`` size, so a flat char limit either wastes space
 * or overshoots the row. */
const tailTruncate = (text: string): string => {
  if (!text) return text;
  const hasCjk = /[\u3000-\u9fff\uff00-\uffef]/.test(text);
  const limit = hasCjk ? 28 : 70;
  if (text.length <= limit) return text;
  return `…${text.slice(-limit)}`;
};

/** Format an elapsed duration (ms) as ``Xs`` / ``Xm Ys`` / ``Xh Ym`` using
 * the same ``task.duration*`` i18n templates as the project-home task
 * cards, so the running-time string reads identically across surfaces. */
const formatElapsedMs = (ms: number, t: Translator): string => {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return t(tk("task.durationSec"), { sec: String(total) });
  if (total < 3600) {
    return t(tk("task.durationMinSec"), {
      min: String(Math.floor(total / 60)),
      sec: String(total % 60),
    });
  }
  return t(tk("task.durationHourMin"), {
    hour: String(Math.floor(total / 3600)),
    min: String(Math.floor((total % 3600) / 60)),
  });
};

/** Transform a stream of SSE events into the chat-style narrative the
 * conversation page shows. We deliberately reuse the conversation page's
 * canonical pipeline:
 *
 *   ``buildTurns(events)`` → ``ConversationTurn[]``
 *     (same store the chat view's ``useChatSession`` feeds off)
 *   ``buildSegments(turn)`` → ``Segment[]``
 *     (header = assistant text, items = thinking+tool that follow)
 *   ``summarizeSegmentPhrase(items)``
 *     → ``"Called harness 5 times，Ran 6 commands"``
 *
 * Without this we'd have two separate aggregation paths drifting in
 * different directions — the chat view would say "Called harness 5
 * times" while the dashboard said "Called harness 2 times" for the
 * same session, because the dashboard's hand-rolled batcher caught a
 * different slice of the stream than ``buildTurns`` does. */
const aggregateEvents = (events: SessionEventDTO[]): DashboardLine[] => {
  const lines: DashboardLine[] = [];
  const turns = buildTurns(events);
  for (const turn of turns) {
    // Skip ``turn.userText`` — the dashboard card title already shows the
    // user prompt verbatim; quoting it again as ``> …`` would just
    // duplicate the header.
    const segments = buildSegments(turn);
    for (let i = 0; i < segments.length; i += 1) {
      const seg = segments[i]!;
      if (seg.header !== null && seg.header.length > 0) {
        lines.push({ key: `h-${turn.id}-${i}`, text: seg.header });
      }
      if (seg.items.length > 0) {
        const { phrase } = summarizeSegmentPhrase(seg.items);
        lines.push({ key: `s-${turn.id}-${i}`, text: phrase });
      }
    }
  }
  return lines;
};

interface RunningCardProps {
  run: RunSummary;
  sourceLabel: string;
  /** ``true`` when the run is a task; drives the leading icon. */
  isTask: boolean;
  statusChip: ReactNode;
  onOpen: () => void;
  t: Translator;
}

const RUNNING_VISIBLE_LINES = 5;

const RunningCard = ({
  run,
  sourceLabel,
  isTask,
  statusChip,
  onOpen,
  t,
}: RunningCardProps) => {
  const ScopeIcon =
    run.origin === "automation" ? Clock3 : isTask ? ListChecks : MessageSquare;
  // No ``max`` override — rely on the hook default. The visible-line cap
  // below handles display trimming; the buffer needs to be large enough that
  // batch counts (``Called harness 10 times``, ``Ran 6 commands``) survive
  // aggregation without getting truncated by FIFO eviction.
  const events = useSessionEvents(run.session_id);
  const lines = useMemo(() => aggregateEvents(events), [events]);
  const visible = lines.slice(-RUNNING_VISIBLE_LINES);

  // Live "running time" ticking next to the scope label — same 1s cadence /
  // duration format as the project-home task cards. ``run.updated_at`` is
  // the session's created_at (epoch ms), i.e. the run's start. Only tick
  // while actually running: a ``paused`` run shows here too (it's in-flight),
  // and its clock must freeze rather than keep counting.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (run.status !== "running") return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [run.status]);
  const elapsed = run.updated_at
    ? formatElapsedMs(now - run.updated_at, t)
    : "";

  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex h-[226px] flex-col overflow-hidden rounded-xl bg-card p-4 pt-5 text-left shadow-[var(--shadow-1)] transition-colors hover:bg-surface-soft"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex min-w-0 items-center gap-1 text-[11px] text-ink-meta">
          <ScopeIcon className="h-3 w-3 shrink-0" strokeWidth={2} />
          <span className="truncate">{sourceLabel}</span>
          {elapsed && (
            <span className="shrink-0 tabular-nums">· {elapsed}</span>
          )}
        </span>
        {statusChip}
      </div>
      <div className="mt-2 truncate text-sm font-medium text-ink-heading">
        {run.title}
      </div>
      <div className="mt-3 flex-1 space-y-1 overflow-hidden border-t border-surface-border pt-2">
        {visible.length === 0 ? (
          <div className="text-xs text-ink-meta">
            {t(tk("activity.waitingForEvents"))}
          </div>
        ) : (
          visible.map((line, idx) => {
            // The last visible line is the freshest — when the run is
            // actively streaming, that line is usually the assistant
            // header still growing token-by-token. Tail-truncate it so
            // the user sees the LATEST tokens (… on the left) rather
            // than a frozen first-sentence prefix that no longer
            // reflects what the agent is doing right now. Earlier lines
            // are sealed history; head-truncation reads fine for them.
            const isLiveTail =
              idx === visible.length - 1 && run.status === "running";
            return (
              <div
                key={line.key}
                className="flex items-start gap-1.5 text-xs leading-5 text-ink-meta"
              >
                <span className="mt-2 inline-block h-1 w-1 shrink-0 rounded-full bg-ink-meta/60" />
                <span className="line-clamp-1 min-w-0 flex-1">
                  {isLiveTail ? tailTruncate(line.text) : line.text}
                </span>
              </div>
            );
          })
        )}
      </div>
    </button>
  );
};

export const ActivityPage = () => {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { setHeader, setHeaderClassName, setContentInnerClassName } =
    useProjectOutlet();
  const { runs: running } = useRunningRuns();
  const projects = useProjectStore((s) => s.projects);
  const renameSession = useSessionStore((s) => s.renameSession);
  // Sessions/tasks with a pending question — drives the 等你确认 status
  // override on running cards (question-attention). Both keys are needed:
  // a task's RunSummary carries the LEAD session_id, while the pending's
  // session_id is usually a MEMBER session — those only meet via task_id.
  const decisionPending = useDecisionPending();
  const attention = useMemo(() => {
    const sessions = new Set<string>();
    const tasks = new Set<string>();
    for (const e of decisionPending) {
      sessions.add(e.session_id);
      if (e.task_id) tasks.add(e.task_id);
    }
    return { sessions, tasks };
  }, [decisionPending]);

  const [filter, setFilter] = useState<SourceFilter>("all");
  // The chat row up for delete-confirmation ({id,title}); null when closed.
  // Only chat rows populate this — tasks have no DELETE endpoint (openapi.yaml).
  const [deletingChat, setDeletingChat] = useState<{
    id: string;
    title: string;
  } | null>(null);
  const [deleteInFlight, setDeleteInFlight] = useState(false);

  // Global activity history (chats + tasks), cursor-paginated — the same feed
  // the project-home tabs use, minus the project scope. The live "running" cards
  // above stay on ``useRunningRuns``; this is the quiet scannable history below.
  const historyFeed = useActivityFeed({ tab: filter, pollMs: 4000 });

  useEffect(() => {
    setHeader(<PageHeader title={t(tk("nav.activity"))} />);
    setHeaderClassName("h-auto px-5 py-5");
    // Drop the AppShell's default vertical padding for this page —
    // the page already self-manages top/bottom space (``pt-4`` on the
    // tab strip, ``pb-12`` at the bottom) and the outer ``py-7`` was
    // adding double breathing room that stranded the history list
    // mid-screen.
    setContentInnerClassName("px-6 sm:px-7");
    return () => {
      setHeader(null);
      setHeaderClassName(undefined);
      setContentInnerClassName(undefined);
    };
  }, [setHeader, setHeaderClassName, setContentInnerClassName, t]);

  // Label: ``<project> · <kind>`` for project-scoped runs, bare ``<kind>``
  // for the default project. Prefixing the default chats with the
  // project name ("New chat") just reads as "New chat · Chat" — redundant
  // — so we drop the scope there and only keep it when it carries real
  // information (the project name). Chats carry their scope in
  // ``source_kind`` directly; tasks don't, so look the project kind up by id.
  const projectKindById = useMemo(
    () => new Map(projects.map((w) => [w.id, w.kind])),
    [projects],
  );

  const sourceLabel = (r: RunSummary): string => {
    const isProject =
      r.source_kind === "project_chat" ||
      (r.source_kind === "task" &&
        projectKindById.get(r.project_id) === "project");
    // Automation-triggered runs read as 自动化 regardless of chat/task — in the
    // 全部 tab the kind chip should mark provenance, not surface type.
    const kind =
      r.origin === "automation"
        ? t(tk("activity.automationTag"))
        : r.source_kind === "task"
          ? t(tk("activity.taskTag"))
          : t(tk("activity.chatTag"));
    if (!isProject) return kind;
    const scope = r.project_name ?? "Project";
    return `${scope} · ${kind}`;
  };

  const matchesFilter = (r: RunSummary): boolean => {
    const isAuto = r.origin === "automation";
    if (filter === "automation") return isAuto;
    if (filter === "all") return true;
    if (filter === "task") return r.source_kind === "task" && !isAuto;
    return r.source_kind !== "task" && !isAuto; // chat
  };

  const filteredRunning = useMemo(
    () => running.filter(matchesFilter),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [running, filter],
  );
  const displayRunning = filteredRunning;

  const openRun = (r: RunSummary): void => {
    if (r.source_kind === "task" && r.task_id) {
      navigate(`/tasks/${encodeURIComponent(r.task_id)}`);
    } else {
      navigate(`/conversation/${encodeURIComponent(r.session_id)}`);
    }
  };

  // Activity-feed row handlers (history list below).
  const openSession = (id: string): void => {
    navigate(`/conversation/${encodeURIComponent(id)}`);
  };
  const openTask = (id: string): void => {
    navigate(`/tasks/${encodeURIComponent(id)}`);
  };
  const handleRenameConfirm = (id: string, name: string): void => {
    void (async () => {
      await renameSession(id, name);
      historyFeed.refresh();
    })();
  };

  const renderStatusChip = (run: RunSummary) => {
    // 等你确认 overrides the raw kernel status (question-attention): a run
    // blocked on the user is the one state that must never read as a plain
    // 运行中. Joined from the decision store — same source as the badge and
    // the attention group, so the three can never disagree.
    if (
      attention.sessions.has(run.session_id) ||
      (run.task_id != null && attention.tasks.has(run.task_id))
    ) {
      return (
        <Badge variant="warning" className="shrink-0">
          {t(tk("decisionInbox.statusAttention"))}
        </Badge>
      );
    }
    const key = STATUS_LABEL_KEY[run.status];
    if (!key) return null;
    return (
      <Badge
        variant={activityRunStatusVariant(run.status)}
        className="shrink-0"
      >
        {t(tk(key))}
      </Badge>
    );
  };

  // History rows: title + relative created time + status pill on the side,
  // matching the project-home conversation rows. Always list-shaped — the
  // dashboard above already has the heavy card visualisation, so the history
  // rail stays a quiet scannable index. Outer wrapper is a ``div`` (not a
  // ``button``) because chat rows nest a ``RowActionsMenu`` trigger
  // ``button``, and nested buttons are invalid HTML. Keyboard accessibility:
  // ``role="button"`` + ``tabIndex`` + Enter / Space.
  const handleDeleteChat = async () => {
    const target = deletingChat;
    if (!target) return;
    setDeleteInFlight(true);
    try {
      await sessionsApi.delete(target.id);
      setDeletingChat(null);
      historyFeed.refresh();
    } catch {
      // Leave the dialog open on failure so the user can retry / read the
      // error from the underlying API call's console log.
    } finally {
      setDeleteInFlight(false);
    }
  };

  // Running: always card-shaped (regardless of view toggle); each card
  // subscribes to its own session's SSE stream so the dashboard auto-updates.
  // Wider cards (max 2 columns) so the streaming event log has room to breathe.
  const renderRunning = (runs: RunSummary[]) => (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {runs.map((r) => (
        <RunningCard
          key={r.session_id}
          run={r}
          sourceLabel={sourceLabel(r)}
          isTask={r.source_kind === "task"}
          statusChip={renderStatusChip(r)}
          onOpen={() => openRun(r)}
          t={t}
        />
      ))}
    </div>
  );

  // ──────────────────────────────────────────────────────────────
  // Toolbar pieces
  // ──────────────────────────────────────────────────────────────

  const FILTERS: { value: SourceFilter; labelKey: string }[] = [
    { value: "all", labelKey: "activity.filterAll" },
    { value: "chat", labelKey: "activity.chatTag" },
    { value: "task", labelKey: "activity.taskTag" },
    { value: "automation", labelKey: "activity.automationTag" },
  ];

  // ──────────────────────────────────────────────────────────────
  // Render
  // ──────────────────────────────────────────────────────────────


  return (
    <div className="mx-auto max-w-[760px] pb-12 pt-4">
      {/* Toolbar — line-tab filter shared with project home / conversation
          right panel for visual consistency. */}
      <Tabs value={filter} onValueChange={(v) => setFilter(v as SourceFilter)}>
        <div className="border-b border-surface-border">
          <TabsList
            variant="line"
            className="h-9 justify-start gap-4 border-0 p-0"
          >
            {FILTERS.map((f) => (
              <TabsTrigger key={f.value} value={f.value}>
                {t(tk(f.labelKey))}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>

      {/* 等你处理 — pending questions pinned above everything else
          (question-attention). Self-hides when empty. */}
      <AttentionGroup />

      {/* Running section — visible whenever the display list has anything
          (real running from polling + the two demo style-case runs pinned
          from today). Sits at the top so the user lands on the live
          dashboard. Followed by a divider before the always-visible
          history. */}
      {displayRunning.length > 0 && (
        <section className="mt-5">
          <div className="mb-2 flex items-center gap-2 px-3">
            <span className="text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
              {t(tk("activity.running"))}
            </span>
            <span className="text-[11.5px] font-medium text-ink-meta">
              · {displayRunning.length}
            </span>
          </div>
          {renderRunning(displayRunning)}
          <div className="my-6 border-t border-surface-border" />
        </section>
      )}

      {/* History — the unified activity feed (chats + tasks), cursor-paginated,
          global scope. The live "running" cards sit above. */}
      <section className={displayRunning.length > 0 ? "" : "mt-5"}>
        <ActivityFeedList
          feed={historyFeed}
          showProjectName
          onOpenSession={openSession}
          onOpenTask={openTask}
          onRenameConfirm={handleRenameConfirm}
          onDeleteSession={(id, title) => setDeletingChat({ id, title })}
          emptyLabel={t(tk("activity.noHistory"))}
        />
      </section>
      <DeleteConfirmDialog
        open={deletingChat !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingChat(null);
        }}
        itemName={deletingChat?.title ?? ""}
        loading={deleteInFlight}
        onConfirm={() => void handleDeleteChat()}
      />
    </div>
  );
};
