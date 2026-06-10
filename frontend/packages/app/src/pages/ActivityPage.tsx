import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, ListChecks, MessageSquare, Trash2 } from "lucide-react";
import {
  DeleteConfirmDialog,
  StatusPill,
  Tabs,
  TabsList,
  TabsTrigger,
} from "@valuz/ui";
import {
  buildTurns,
  runsApi,
  sessionsApi,
  useRunningRuns,
  useSessionEvents,
  useTranslation,
  useProjectStore,
  type RunSummary,
} from "@valuz/core";
import {
  buildSegments,
  summarizeSegmentPhrase,
  type SessionEventDTO,
} from "@valuz/shared";
import { useProjectOutlet } from "@valuz/app/layout";

type SourceFilter = "all" | "chat" | "task";
type TimeBucket = "today" | "yesterday" | "thisWeek" | "earlier";

const tk = (key: string) =>
  key as Parameters<ReturnType<typeof useTranslation>["t"]>[0];

const bucketOf = (ms: number, now: Date): TimeBucket => {
  const d = new Date(ms);
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startYesterday = new Date(startToday);
  startYesterday.setDate(startYesterday.getDate() - 1);
  const startWeek = new Date(startToday);
  startWeek.setDate(startWeek.getDate() - 7);
  if (d >= startToday) return "today";
  if (d >= startYesterday) return "yesterday";
  if (d >= startWeek) return "thisWeek";
  return "earlier";
};

const BUCKET_ORDER: TimeBucket[] = [
  "today",
  "yesterday",
  "thisWeek",
  "earlier",
];
const BUCKET_KEY: Record<TimeBucket, string> = {
  today: "activity.today",
  yesterday: "activity.yesterday",
  thisWeek: "activity.thisWeek",
  earlier: "activity.earlier",
};

// Label key per run status; colors/style come from the shared StatusPill.
const STATUS_LABEL_KEY: Record<string, string> = {
  running: "activity.statusRunning",
  paused: "activity.statusPaused",
  idle: "activity.statusIdle",
  completed: "activity.statusCompleted",
  failed: "activity.statusFailed",
  stopped: "activity.statusStopped",
  blocked: "activity.statusBlocked",
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
  const hasCjk = /[　-鿿＀-￯]/.test(text);
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
  const ScopeIcon = isTask ? ListChecks : MessageSquare;
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
      className="flex h-[226px] flex-col overflow-hidden rounded-xl border border-surface-border bg-card p-4 pt-5 text-left shadow-sm transition-colors hover:bg-surface-soft"
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
  const { setHeader, setContentInnerClassName } = useProjectOutlet();
  const { runs: running } = useRunningRuns();
  const projects = useProjectStore((s) => s.projects);

  const [filter, setFilter] = useState<SourceFilter>("all");
  const [finished, setFinished] = useState<RunSummary[]>([]);
  // The row currently up for delete-confirmation. ``null`` when no dialog
  // is open. Only chat rows can populate this — tasks have no DELETE
  // endpoint (see openapi.yaml) so we don't expose a trash affordance on
  // task rows in the first place.
  const [deletingChat, setDeletingChat] = useState<RunSummary | null>(null);
  const [deleteInFlight, setDeleteInFlight] = useState(false);

  useEffect(() => {
    setHeader(
      <span className="text-base font-medium text-ink-heading">
        {t(tk("nav.activity"))}
      </span>,
    );
    // Drop the AppShell's default vertical padding for this page —
    // the page already self-manages top/bottom space (``pt-4`` on the
    // tab strip, ``pb-12`` at the bottom) and the outer ``py-7`` was
    // adding double breathing room that stranded the history list
    // mid-screen.
    setContentInnerClassName("px-6 sm:px-7");
    return () => {
      setHeader(null);
      setContentInnerClassName(undefined);
    };
  }, [setHeader, setContentInnerClassName, t]);

  // Load finished runs on mount + refresh whenever a session leaves the
  // running set (i.e., a run just finished). Without this, a run that
  // completes in front of the user stays invisible until the page is
  // re-opened.
  const refreshFinished = useCallback(() => {
    void runsApi
      .list({ status: "finished" })
      .then((res) => setFinished(res.runs))
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    refreshFinished();
  }, [refreshFinished]);
  const prevRunningIdsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const currentIds = new Set(running.map((r) => r.session_id));
    // Any change to the running set should pull a fresh history — not just
    // "someone left". The narrower check missed two real cases:
    //   • Backend lag: a run leaves the running pool before its DB row's
    //     ``status`` actually flips to finished, so the refresh fires once
    //     against stale data and never retries.
    //   • Short turns: chats that complete inside the 2.5s ``useRunningRuns``
    //     polling window are never seen as running, so ``prevSet`` stays
    //     empty and the shrink check never fires — the finished row only
    //     surfaces on a manual reload.
    let changed = currentIds.size !== prevRunningIdsRef.current.size;
    if (!changed) {
      for (const id of currentIds) {
        if (!prevRunningIdsRef.current.has(id)) {
          changed = true;
          break;
        }
      }
    }
    prevRunningIdsRef.current = currentIds;
    if (changed) {
      refreshFinished();
      // Delayed retry to cover the case where the run leaves the running
      // pool a tick before its DB row commits the finished status. ~1.5s is
      // enough breathing room for the orchestrator's finalize path.
      const handle = window.setTimeout(refreshFinished, 1500);
      return () => window.clearTimeout(handle);
    }
  }, [running, refreshFinished]);

  // Background safety net — re-pull the finished list every 5s while the
  // page is mounted, in case both the change-detect and the delayed retry
  // miss a transition. Cheap (one HTTP request, response is just session
  // metadata) and means a stale history can never persist past one tick.
  useEffect(() => {
    const handle = window.setInterval(refreshFinished, 5000);
    return () => window.clearInterval(handle);
  }, [refreshFinished]);

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
    const kind =
      r.source_kind === "task"
        ? t(tk("activity.taskTag"))
        : t(tk("activity.chatTag"));
    if (!isProject) return kind;
    const scope = r.project_name ?? "Project";
    return `${scope} · ${kind}`;
  };

  const matchesFilter = (r: RunSummary): boolean => {
    if (filter === "all") return true;
    if (filter === "task") return r.source_kind === "task";
    return r.source_kind !== "task"; // chat
  };

  const filteredRunning = useMemo(
    () => running.filter(matchesFilter),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [running, filter],
  );
  const filteredFinished = useMemo(
    () => finished.filter(matchesFilter),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [finished, filter],
  );

  const displayRunning = filteredRunning;

  const openRun = (r: RunSummary): void => {
    if (r.source_kind === "task" && r.task_id) {
      navigate(`/tasks/${encodeURIComponent(r.task_id)}`);
    } else {
      navigate(`/conversation/${encodeURIComponent(r.session_id)}`);
    }
  };

  const renderStatusChip = (run: RunSummary) => {
    const key = STATUS_LABEL_KEY[run.status];
    if (!key) return null;
    return <StatusPill status={run.status} label={t(tk(key))} />;
  };

  // History rows: title only (small scope label + status pill on the side).
  // Always list-shaped — the dashboard above already has the heavy card
  // visualisation, so the history rail stays a quiet scannable index.
  // Outer wrapper is a ``div`` (not a ``button``) because we nest a real
  // trash ``button`` inside for chat rows, and nested buttons are invalid
  // HTML — the trash click would also trip the row's navigation. Keyboard
  // accessibility: ``role="button"`` + ``tabIndex`` + Enter / Space.
  const historyRow = (run: RunSummary) => {
    const ScopeIcon = run.source_kind === "task" ? ListChecks : MessageSquare;
    const canDelete = run.source_kind !== "task";
    return (
      <div
        key={run.session_id}
        role="button"
        tabIndex={0}
        onClick={() => openRun(run)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openRun(run);
          }
        }}
        className="group flex w-full cursor-pointer items-center gap-2 rounded-xl px-3 py-3 text-left transition-colors hover:bg-surface-soft"
      >
        <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-ink-muted">
          <ScopeIcon className="h-3 w-3" strokeWidth={2} />
          {sourceLabel(run)}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-heading">
          {run.title}
        </span>
        {renderStatusChip(run)}
        {canDelete ? (
          // Hover swaps the navigation arrow for a trash button — keeps the
          // row's right-edge slot stable but exposes the destructive action
          // only when the user is actively pointing at the row. Click stops
          // propagation so it doesn't also navigate into the conversation.
          <span className="relative flex h-4 w-4 shrink-0 items-center justify-center">
            <ArrowRight className="absolute h-4 w-4 text-ink-muted transition-opacity group-hover:opacity-0" />
            <button
              type="button"
              aria-label={t(tk("activity.deleteChat"))}
              onClick={(e) => {
                e.stopPropagation();
                setDeletingChat(run);
              }}
              className="absolute flex h-4 w-4 items-center justify-center text-ink-muted opacity-0 transition-opacity hover:text-error focus:opacity-100 group-hover:opacity-100"
            >
              <Trash2 className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          </span>
        ) : (
          <ArrowRight className="h-4 w-4 shrink-0 text-ink-muted" />
        )}
      </div>
    );
  };

  const handleDeleteChat = async () => {
    const target = deletingChat;
    if (!target) return;
    setDeleteInFlight(true);
    try {
      await sessionsApi.delete(target.session_id);
      // Optimistic local removal so the row disappears immediately. The 5s
      // periodic refresh will reconcile if the backend disagrees.
      setFinished((prev) =>
        prev.filter((r) => r.session_id !== target.session_id),
      );
      setDeletingChat(null);
    } catch {
      // Leave the dialog open on failure so the user can retry / read the
      // error from the underlying API call's console log.
    } finally {
      setDeleteInFlight(false);
    }
  };

  const renderHistory = (runs: RunSummary[]) => (
    <div className="flex flex-col">{runs.map(historyRow)}</div>
  );

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

  const groupedHistory = useMemo(() => {
    const now = new Date();
    const groups = new Map<TimeBucket, RunSummary[]>();
    for (const r of filteredFinished) {
      const b = bucketOf(r.updated_at, now);
      const list = groups.get(b) ?? [];
      list.push(r);
      groups.set(b, list);
    }
    return groups;
  }, [filteredFinished]);

  // ──────────────────────────────────────────────────────────────
  // Toolbar pieces
  // ──────────────────────────────────────────────────────────────

  const FILTERS: { value: SourceFilter; labelKey: string }[] = [
    { value: "all", labelKey: "activity.filterAll" },
    { value: "chat", labelKey: "activity.chatTag" },
    { value: "task", labelKey: "activity.taskTag" },
  ];

  // ──────────────────────────────────────────────────────────────
  // Render
  // ──────────────────────────────────────────────────────────────

  const hasAny = displayRunning.length > 0 || filteredFinished.length > 0;

  return (
    <div className="mx-auto max-w-3xl px-5 pb-12 pt-4">
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

      {/* History — grouped by time bucket; always rendered (no tab gate). */}
      <section className={displayRunning.length > 0 ? "" : "mt-5"}>
        {filteredFinished.length === 0 ? (
          hasAny ? null : (
            <div className="px-3 py-12 text-center text-sm text-ink-meta">
              {t(tk("activity.noHistory"))}
            </div>
          )
        ) : (
          <div className="flex flex-col gap-5">
            {BUCKET_ORDER.filter((b) => groupedHistory.has(b)).map((b) => (
              <div key={b}>
                <div className="mb-1.5 px-3 text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
                  {t(tk(BUCKET_KEY[b]))}
                </div>
                {renderHistory(groupedHistory.get(b) ?? [])}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Truly empty (nothing matches the filter, nothing running). Falls
          through to here only when there's neither a running nor a history
          entry matching the current filter. */}
      {!hasAny && filter !== "all" && (
        <div className="px-3 py-12 text-center text-sm text-ink-meta">
          {t(tk("activity.noHistory"))}
        </div>
      )}
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
