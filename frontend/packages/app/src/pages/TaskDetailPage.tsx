import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
} from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft,
  CheckCheck,
  CheckCircle2,
  ChevronRight,
  FileText,
  Flag,
  ListTodo,
  Loader2,
  MessageCircleQuestion,
  MessageSquare,
  Paperclip,
  Pause,
  Play,
  Send,
  Square,
  Target,
  User,
  XCircle,
} from "lucide-react";
import {
  ArtifactViewerShell,
  BackLink,
  Button,
  ConversationTurnList,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogField,
  PageLoader,
  Textarea,
  cn,
} from "@valuz/ui";
import {
  agentsApi,
  tasksApi,
  projectsApi,
  useDecisionPending,
  useTaskEvents,
  useTranslation,
  type ArtifactContent,
  type ArtifactDescriptor,
  type DecisionEntry,
  type IntervenePayload,
  type MemberWithAgent,
  type TaskDetail,
  type TaskEvent,
} from "@valuz/core";
import type { FileTreeNode } from "@valuz/ui";
import { useProjectOutlet } from "@valuz/app/layout";
import {
  TaskContextPanel,
  type PlannedSubtask,
} from "../components/TaskContextPanel";
import { toFileTree } from "../lib/file-tree";
import { TaskStatusLabel } from "../components/TaskStatusLabel";
import { useLeadFollowUpChat } from "../hooks";
import { deriveDeliverable } from "./task-detail/deliverable";

interface EventMeta {
  icon: ComponentType<{ className?: string }>;
  /** Tailwind classes for the timeline node (bg + text). */
  node: string;
  labelKey: string;
}

const EVENT_META: Record<string, EventMeta> = {
  kickoff: {
    icon: Flag,
    node: "bg-brand/10 text-brand",
    labelKey: "task.event.kickoff",
  },
  subtask_spawned: {
    icon: Send,
    node: "bg-sky-500/10 text-sky-500",
    labelKey: "task.event.subtaskSpawned",
  },
  subtask_completed: {
    icon: CheckCircle2,
    node: "bg-emerald-500/10 text-emerald-500",
    labelKey: "task.event.subtaskCompleted",
  },
  subtask_failed: {
    icon: XCircle,
    node: "bg-red-500/10 text-red-500",
    labelKey: "task.event.subtaskFailed",
  },
  subtask_message: {
    icon: MessageSquare,
    node: "bg-indigo-500/10 text-indigo-500",
    labelKey: "task.event.subtaskMessage",
  },
  user_note: {
    icon: MessageSquare,
    node: "bg-ink-meta/10 text-ink-body",
    labelKey: "task.event.userNote",
  },
  goal_revised: {
    icon: Target,
    node: "bg-amber-500/10 text-amber-500",
    labelKey: "task.event.goalRevised",
  },
  paused: {
    icon: Pause,
    node: "bg-amber-500/10 text-amber-500",
    labelKey: "task.event.paused",
  },
  resumed: {
    icon: Play,
    node: "bg-brand/10 text-brand",
    labelKey: "task.event.resumed",
  },
  stopped: {
    icon: Square,
    node: "bg-ink-meta/10 text-ink-body",
    labelKey: "task.event.stopped",
  },
  task_completed: {
    icon: CheckCheck,
    node: "bg-emerald-500/10 text-emerald-500",
    labelKey: "task.event.taskCompleted",
  },
  task_failed: {
    icon: XCircle,
    node: "bg-red-500/10 text-red-500",
    labelKey: "task.event.taskFailed",
  },
  task_blocked: {
    icon: XCircle,
    node: "bg-red-500/10 text-red-500",
    labelKey: "task.event.taskBlocked",
  },
  task_planned: {
    icon: ListTodo,
    node: "bg-brand/10 text-brand",
    labelKey: "task.event.taskPlanned",
  },
  plan_revised: {
    icon: ListTodo,
    node: "bg-amber-500/10 text-amber-500",
    labelKey: "task.event.planRevised",
  },
  subtask_reviewed: {
    icon: CheckCircle2,
    node: "bg-violet-500/10 text-violet-500",
    labelKey: "task.event.subtaskReviewed",
  },
};

const FALLBACK_META: EventMeta = {
  icon: MessageSquare,
  node: "bg-ink-meta/10 text-ink-body",
  labelKey: "task.event.kickoff",
};

function formatEventTime(ms: number): string {
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Format an elapsed duration in ms as ``Xh Ym`` / ``Xm Ys`` / ``Xs``.
 *  Uses i18n templates so zh-CN renders "X 分 Y 秒" and en-US "Xm Ys".
 *  Resolution drops to minutes once we cross the hour mark — second
 *  precision past an hour is noise on a task scale. */
type Translator = (
  key: string,
  params?: Record<string, string | number>,
) => string;

/** Resolve an artifact path to an absolute filesystem location. Agents
 *  typically pass project-relative paths to ``finish_task`` (e.g.
 *  ``"reports/desktop.md"``), but some pass absolute paths too. Join
 *  with the project cwd when relative, leave alone when absolute or
 *  cwd is unknown. */
function resolveArtifactPath(path: string, rootPath: string): string {
  if (!path) return path;
  if (path.startsWith("/") || /^[a-zA-Z]:[\\/]/.test(path)) return path;
  if (!rootPath) return path;
  const sep = rootPath.includes("\\") ? "\\" : "/";
  const trimmed = rootPath.endsWith(sep) ? rootPath.slice(0, -1) : rootPath;
  return `${trimmed}${sep}${path}`;
}

function toProjectRelativeArtifactPath(path: string, rootPath: string): string | null {
  if (!path) return null;
  const normalizedPath = path.replace(/\\/g, "/");
  if (!normalizedPath.startsWith("/") && !/^[a-zA-Z]:\//.test(normalizedPath)) {
    return normalizedPath.replace(/^\/+/, "");
  }
  if (!rootPath) return null;
  const normalizedRoot = rootPath.replace(/\\/g, "/").replace(/\/+$/, "");
  if (normalizedPath === normalizedRoot) return null;
  if (!normalizedPath.startsWith(`${normalizedRoot}/`)) return null;
  return normalizedPath.slice(normalizedRoot.length + 1);
}

function artifactIconClassName(filename: string): string {
  const extension = filename.split(".").pop()?.toLowerCase();
  if (extension === "md" || extension === "markdown") return "text-[#725cf9]";
  if (extension === "html" || extension === "htm") return "text-[#ff8710]";
  return "text-ink-muted";
}

function artifactIconBgClassName(filename: string): string {
  const extension = filename.split(".").pop()?.toLowerCase();
  if (extension === "md" || extension === "markdown") return "bg-[#725cf9]/10";
  if (extension === "html" || extension === "htm") return "bg-[#ff8710]/10";
  return "bg-ink-muted/10";
}

/** Open a file in the OS file manager (desktop only — Electron's
 *  ``shell.openPath`` via the existing ``open_in_finder`` IPC). On
 *  webui (no ``valuzDesktop`` bridge) we fall back to copying the
 *  path to the clipboard with a toast, since the browser can't reveal
 *  arbitrary filesystem paths. */
async function openArtifact(absolutePath: string, t: Translator) {
  const bridge = (
    window as Window & {
      valuzDesktop?: {
        invoke: <T>(ch: string, args?: unknown) => Promise<T>;
      };
    }
  ).valuzDesktop;
  if (bridge) {
    await bridge.invoke("open_in_finder", { path: absolutePath });
    return;
  }
  try {
    await navigator.clipboard.writeText(absolutePath);
    toast.success(t("task.artifactPathCopied"));
  } catch {
    toast.error(t("common.error"));
  }
}

function formatDuration(ms: number, t: Translator): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return t("task.durationSec", { sec: total });
  if (total < 3600) {
    return t("task.durationMinSec", {
      min: Math.floor(total / 60),
      sec: total % 60,
    });
  }
  return t("task.durationHourMin", {
    hour: Math.floor(total / 3600),
    min: Math.floor((total % 3600) / 60),
  });
}

/** Resolve a one-line, type-specific detail string for the timeline.
 *  Reads the right payload field per event type so each row carries
 *  useful info instead of a generic label — e.g. ``task_planned``
 *  surfaces "拆解为 N 个子任务", ``subtask_reviewed`` surfaces the
 *  approve/rework decision + feedback. Falls back to the legacy
 *  ``text|summary|goal|error`` lookup for event types without a
 *  custom rule. */
function eventDetail(evt: TaskEvent, t: Translator): string {
  const p = (evt.payload ?? {}) as Record<string, unknown>;
  switch (evt.type) {
    case "task_planned": {
      const subs = (p as { subtasks?: unknown[] }).subtasks;
      const n = Array.isArray(subs) ? subs.length : 0;
      if (n > 0) return t("task.event.planSummary", { count: n });
      break;
    }
    case "plan_revised": {
      const add = Array.isArray((p as { add?: unknown[] }).add)
        ? (p as { add: unknown[] }).add.length
        : 0;
      const upd = Array.isArray((p as { update?: unknown[] }).update)
        ? (p as { update: unknown[] }).update.length
        : 0;
      const rem = Array.isArray((p as { remove?: unknown[] }).remove)
        ? (p as { remove: unknown[] }).remove.length
        : 0;
      const parts: string[] = [];
      if (add) parts.push(t("task.event.planAdd", { count: add }));
      if (upd) parts.push(t("task.event.planUpdate", { count: upd }));
      if (rem) parts.push(t("task.event.planRemove", { count: rem }));
      if (parts.length > 0) return parts.join(" · ");
      break;
    }
    case "subtask_reviewed": {
      const decision = String((p as { decision?: unknown }).decision || "");
      const feedback =
        typeof (p as { feedback?: unknown }).feedback === "string"
          ? ((p as { feedback: string }).feedback || "").trim()
          : "";
      if (decision === "approve") {
        return feedback
          ? t("task.event.subtaskReviewApproveReason", { feedback })
          : t("task.event.subtaskReviewApprove");
      }
      if (decision === "rework") {
        return feedback
          ? t("task.event.subtaskReviewRework", { feedback })
          : t("task.event.subtaskReviewReworkNoFeedback");
      }
      break;
    }
  }
  for (const key of ["text", "summary", "goal", "error"]) {
    const v = p[key];
    if (typeof v === "string" && v.trim()) return v;
  }
  return "";
}

/** First question text from a pending decision's AskUserQuestion payload —
 *  used as the confirm card's one-line summary. */
const pendingQuestionText = (entry: DecisionEntry): string => {
  const qs = (entry.question_payload as { questions?: { question?: string }[] })
    ?.questions;
  return Array.isArray(qs) && qs[0]?.question ? String(qs[0].question) : "";
};

export const TaskDetailPage = () => {
  const { taskId = "" } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { t } = useTranslation();
  const { setHeader, setHideHeader, setRightPanel } = useProjectOutlet();
  // Pending confirmations (AskUserQuestion) raised by this task's agents —
  // surfaced prominently in the timeline so the user isn't left thinking the
  // task is just "working" when it's actually blocked on their answer.
  const taskPending = useDecisionPending().filter((e) => e.task_id === taskId);

  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [members, setMembers] = useState<MemberWithAgent[]>([]);
  const [fileTree, setFileTree] = useState<FileTreeNode[]>([]);
  const [rootPath, setRootPath] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [selectedArtifactPath, setSelectedArtifactPath] = useState<string | null>(
    null,
  );
  const [artifact, setArtifact] = useState<ArtifactDescriptor | null>(null);
  const [artifactContent, setArtifactContent] =
    useState<ArtifactContent | null>(null);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const artifactRequestSeqRef = useRef(0);
  const selectedFileParam = searchParams.get("file");

  // revise-goal dialog (note dialog removed — backend wasn't reading
  // user_note events back into lead context, so the action was a no-op
  // from the user's perspective).
  const [reviseOpen, setReviseOpen] = useState(false);
  const [reviseGoal, setReviseGoal] = useState("");

  // v30: removed project-file-tree + file-preview state. The right
  // ContextPanel no longer shows files (only Team / Todo / Runs), and
  // artifact previews now route through the lead conversation page.

  const loadData = useCallback(async () => {
    try {
      const res = await tasksApi.getTask(taskId);
      setDetail(res);
    } catch {
      toast.error(t("common.error"));
    } finally {
      setLoading(false);
    }
  }, [taskId, t]);

  useEffect(() => {
    void Promise.resolve().then(loadData);
  }, [loadData]);

  useEffect(() => {
    // Task detail is self-titled (the goal card carries the task name +
    // status badge) — hide the project header strip entirely so the
    // app-title "Valuz Agent" doesn't sit above an already-titled page.
    setHeader(null);
    setHideHeader(true);
    return () => {
      setHeader(null);
      setHideHeader(false);
    };
  }, [setHeader, setHideHeader]);

  // Poll while the task is still active so dispatched runs + events stream in.
  const status = detail?.task.status;
  useEffect(() => {
    if (status !== "active") return;
    const interval = setInterval(() => void loadData(), 3000);
    return () => clearInterval(interval);
  }, [status, loadData]);

  // Pull project members so the right-rail Team panel can show each
  // agent's bound model alongside the slug.
  const projectId = detail?.task.project_id;
  useEffect(() => {
    if (!projectId) {
      setMembers([]);
      return;
    }
    let cancelled = false;
    void agentsApi
      .listMembers(projectId)
      .then((res) => {
        if (!cancelled) setMembers(res.agents);
      })
      .catch(() => {
        if (!cancelled) setMembers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Pull the project file tree + cwd so the right-rail "项目文件" tab
  // can show the project files alongside the context sections — same
  // surface ProjectDetailPage shows, so users get the same affordance
  // wherever they are in the project. Extracted as a callback so the
  // refresh button on the file panel can call it on demand.
  const refreshFileTree = useCallback(() => {
    if (!projectId) {
      setFileTree([]);
      return;
    }
    void projectsApi
      .listFiles(projectId, { depth: 3 })
      .then((res) => setFileTree(toFileTree(res.files)))
      .catch(() => setFileTree([]));
  }, [projectId]);

  useEffect(() => {
    if (!projectId) {
      setFileTree([]);
      setRootPath("");
      return;
    }
    let cancelled = false;
    void Promise.all([
      projectsApi.get(projectId).catch(() => null),
      projectsApi
        .listFiles(projectId, { depth: 3 })
        .catch(() => ({ files: [] })),
    ]).then(([ws, filesRes]) => {
      if (cancelled) return;
      setRootPath(ws?.cwd ?? "");
      setFileTree(toFileTree(filesRes.files));
    });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Reveal the project cwd in the OS file manager via the existing
  // ``open_in_finder`` IPC; web fallback copies path to clipboard.
  const handleOpenProjectInFinder = useCallback(() => {
    if (!rootPath) return;
    void openArtifact(rootPath, t as Translator);
  }, [rootPath, t]);

  const openArtifactFile = useCallback(
    async (relPath: string, options?: { syncUrl?: boolean }) => {
      if (!projectId) return;
      const requestSeq = artifactRequestSeqRef.current + 1;
      artifactRequestSeqRef.current = requestSeq;
      const normalized = toProjectRelativeArtifactPath(relPath, rootPath);
      if (!normalized) {
        setSelectedArtifactPath(relPath);
        setArtifact(null);
        setArtifactContent(null);
        setArtifactLoading(false);
        setArtifactError(t("task.artifactOpenInFinder" as Parameters<typeof t>[0]));
        return;
      }
      if (options?.syncUrl !== false && searchParams.get("file") !== normalized) {
        setSearchParams(
          (current) => {
            const next = new URLSearchParams(current);
            next.set("file", normalized);
            return next;
          },
          { replace: false },
        );
      }
      setSelectedArtifactPath(normalized);
      setArtifactLoading(true);
      setArtifactError(null);
      try {
        const result = await projectsApi.readFile(projectId, normalized);
        if (artifactRequestSeqRef.current !== requestSeq) return;
        setArtifact(result.artifact);
        setArtifactContent(result.content);
      } catch (error) {
        if (artifactRequestSeqRef.current !== requestSeq) return;
        setArtifact(null);
        setArtifactContent(null);
        setArtifactError(error instanceof Error ? error.message : String(error));
      } finally {
        if (artifactRequestSeqRef.current === requestSeq) {
          setArtifactLoading(false);
        }
      }
    },
    [projectId, rootPath, searchParams, setSearchParams, t],
  );

  useEffect(() => {
    if (!selectedFileParam) {
      if (selectedArtifactPath) {
        const timer = window.setTimeout(() => {
          setSelectedArtifactPath(null);
          setArtifact(null);
          setArtifactContent(null);
          artifactRequestSeqRef.current += 1;
          setArtifactLoading(false);
          setArtifactError(null);
        }, 0);
        return () => window.clearTimeout(timer);
      }
      return;
    }
    if (
      selectedFileParam === selectedArtifactPath &&
      (artifact || artifactLoading || artifactError)
    ) {
      return;
    }
    const timer = window.setTimeout(() => {
      void openArtifactFile(selectedFileParam, { syncUrl: false });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    artifact,
    artifactError,
    artifactLoading,
    openArtifactFile,
    selectedArtifactPath,
    selectedFileParam,
  ]);

  const handleArtifactReload = useCallback(() => {
    if (selectedArtifactPath) {
      void openArtifactFile(selectedArtifactPath);
    }
  }, [openArtifactFile, selectedArtifactPath]);

  const handleArtifactClose = useCallback(() => {
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("file");
        return next;
      },
      { replace: true },
    );
    setSelectedArtifactPath(null);
    setArtifact(null);
    setArtifactContent(null);
    artifactRequestSeqRef.current += 1;
    setArtifactLoading(false);
    setArtifactError(null);
  }, [setSearchParams]);

  const handleArtifactCopy = useCallback(() => {
    if (artifactContent?.kind !== "text") return;
    void navigator.clipboard
      ?.writeText(artifactContent.content)
      .then(() => toast.success(t("common.copied" as Parameters<typeof t>[0])))
      .catch(() => toast.error(t("common.failed" as Parameters<typeof t>[0])));
  }, [artifactContent, t]);

  const handleArtifactOpenExternal = useCallback(() => {
    if (!rootPath || !selectedArtifactPath) return;
    void openArtifact(
      resolveArtifactPath(selectedArtifactPath, rootPath),
      t as Translator,
    );
  }, [rootPath, selectedArtifactPath, t]);

  // Open a project file from the right-rail file tree (double-click /
  // right-click → open). The tree node's ``path`` is project-relative, so
  // resolve it against the cwd and hand off to the same ``open_in_finder``
  // IPC (``shell.openPath``) the artifact list uses — opens the file in its
  // OS-associated app.
  const handleOpenFileExternal = useCallback(
    (relPath: string) => {
      if (!rootPath) return;
      void openArtifact(resolveArtifactPath(relPath, rootPath), t as Translator);
    },
    [rootPath, t],
  );

  // Render the right rail via AppShell's panel slot — same mechanism the
  // ProjectDetailPage uses, so the panel inherits the rounded card shell +
  // collapse toggle instead of being a bespoke inline ``<aside>``.
  useEffect(() => {
    if (!detail) {
      setRightPanel(null);
      return;
    }
    const { runs } = detail;

    // Real plan for the 待办清单: the latest ``task_plan_update`` snapshot
    // (VALUZ-TASK). Backend emits one per plan mutation; the last wins.
    const lastPlan = [...(detail.events ?? [])]
      .reverse()
      .find((e) => e.type === "task_plan_update");
    const plannedSubtasks = Array.isArray(lastPlan?.payload?.subtasks)
      ? (lastPlan.payload.subtasks as PlannedSubtask[])
      : [];

    setRightPanel(
      <TaskContextPanel
        runs={runs}
        members={members}
        fileTree={fileTree}
        rootPath={rootPath}
        plannedSubtasks={plannedSubtasks}
        onRefreshFiles={refreshFileTree}
        onOpenInFinder={rootPath ? handleOpenProjectInFinder : undefined}
        onPreviewFile={projectId ? (path) => void openArtifactFile(path) : undefined}
        onOpenFile={rootPath ? handleOpenFileExternal : undefined}
      />,
    );
    return () => setRightPanel(null);
  }, [
    detail,
    members,
    fileTree,
    rootPath,
    projectId,
    setRightPanel,
    refreshFileTree,
    handleOpenProjectInFinder,
    openArtifactFile,
    handleOpenFileExternal,
  ]);

  const runIntervene = useCallback(
    async (payload: IntervenePayload, successKey: string): Promise<boolean> => {
      setBusy(true);
      try {
        await tasksApi.intervene(taskId, payload);
        toast.success(t(successKey as Parameters<typeof t>[0]));
        await loadData();
        return true;
      } catch {
        toast.error(t("task.interveneFailed"));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [taskId, t, loadData],
  );

  // Status-driven cards (PRD §3.5 v29): when the task ends, surface the
  // lead's deliverable or the failure reason as a card directly under
  // the goal. Both pull from the events feed — no dedicated backend field.
  // Hooks must run on every render before any conditional return, so
  // we derive against ``detail?.events`` and short-circuit when absent.
  // Deliverable card content. ``deriveDeliverable`` folds the original
  // ``task_completed`` payload with any later ``deliverable_updated`` events
  // (post-completion follow-up edits) — so refining the deliverable in the
  // inline chat below updates this card in place. ``completedAt`` always tracks
  // the original completion (the follow-up "since" cutoff).
  const completionInfo = useMemo(
    () => deriveDeliverable(detail?.events ?? []),
    [detail],
  );

  // Completed-state follow-up chat. When the task is done the page flips: the
  // run-timeline collapses, the deliverable becomes the subject, and an inline
  // conversation with the lead session opens below it so the user can ask for
  // tweaks. The chat is scoped to events strictly after ``completedAt`` so the
  // orchestration history above the finish line never leaks into it.
  // ALL hooks below run before the ``if (loading)`` / ``if (!detail)`` early
  // returns at the end of the hook block, so the hook order stays stable.
  const isCompleted = detail?.task.status === "completed";
  const leadSessionId = useMemo(
    () => detail?.runs.find((r) => r.kind === "lead")?.session_id ?? null,
    [detail],
  );
  const followUp = useLeadFollowUpChat({
    leadSessionId: isCompleted ? leadSessionId : null,
    sinceTs: completionInfo?.completedAt ?? null,
  });

  // Completed tasks stop polling (the 3s poll above is active-only), so the
  // deliverable card is kept fresh by streaming task events instead: when the
  // lead calls ``update_deliverable`` during the follow-up chat it appends a
  // ``deliverable_updated`` event, which arrives here and merges into
  // ``detail.events`` (deduped by id). ``completionInfo`` then re-derives and
  // the card updates in place — no full refetch, no leaked turn. Inert until
  // the task is completed (``taskId = null``).
  useTaskEvents(
    isCompleted ? taskId : null,
    useCallback((ev: TaskEvent) => {
      setDetail((prev) => {
        if (!prev || prev.events.some((e) => e.id === ev.id)) return prev;
        return { ...prev, events: [...prev.events, ev] };
      });
    }, []),
  );

  const [followUpDraft, setFollowUpDraft] = useState("");
  const followUpScrollRef = useRef<HTMLDivElement>(null);
  const followUpTurnsLenRef = useRef(0);
  // Whether the follow-up chat should keep itself pinned to the bottom. Starts
  // true; flips off the moment the user scrolls up to re-read history, back on
  // when they scroll back down or send a new message.
  const followUpStickRef = useRef(true);
  // The run-timeline is demoted to a collapsed-by-default disclosure once the
  // task completes — the deliverable, not the process, is the subject now.
  const [runTimelineOpen, setRunTimelineOpen] = useState(false);
  // The deliverable card is the headline (open by default), but collapsible so
  // the user can fold it away and hand the full height to the follow-up chat.
  const [deliverableOpen, setDeliverableOpen] = useState(true);

  const hasFollowUpTurns = followUp.turns.length > 0;
  // Disengage stick-to-bottom only on a real user scroll-up gesture (wheel up),
  // and re-engage once they wheel back down to the bottom. We deliberately key
  // off ``wheel`` rather than the ``scroll`` event: ``ConversationTurnList`` is
  // virtualized and fires programmatic ``scroll`` events while it measures rows,
  // which a ``scroll`` listener can't tell apart from a user dragging the bar —
  // that ambiguity was silently turning follow-mode off mid-stream.
  useEffect(() => {
    const el = followUpScrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) {
        followUpStickRef.current = false;
      } else if (el.scrollHeight - el.scrollTop - el.clientHeight < 40) {
        followUpStickRef.current = true;
      }
    };
    el.addEventListener("wheel", onWheel, { passive: true });
    return () => el.removeEventListener("wheel", onWheel);
  }, [hasFollowUpTurns]);

  // Pin the conversation to the bottom as it grows. ``followUp.turns`` changes
  // on every streaming chunk and whenever a new turn lands; a NEW turn (the user
  // just sent) re-engages follow-mode. Because the virtualizer inflates
  // ``scrollHeight`` a frame or two AFTER ``turns`` updates, a single scroll
  // would land short — so we pump the pin across a handful of frames, each
  // guarded on the stick flag so a mid-stream scroll-up cancels it cleanly.
  useEffect(() => {
    const el = followUpScrollRef.current;
    if (!el) return;
    if (followUp.turns.length > followUpTurnsLenRef.current) {
      followUpStickRef.current = true;
    }
    followUpTurnsLenRef.current = followUp.turns.length;
    if (!followUpStickRef.current) return;
    let raf = 0;
    let frame = 0;
    const pump = () => {
      if (!followUpStickRef.current) return;
      el.scrollTop = el.scrollHeight;
      if (++frame < 8) raf = requestAnimationFrame(pump);
    };
    pump();
    return () => cancelAnimationFrame(raf);
  }, [followUp.turns]);

  const handleFollowUpSend = useCallback(async () => {
    const text = followUpDraft.trim();
    if (!text) return;
    setFollowUpDraft("");
    try {
      // ``send`` returns as soon as the turn is accepted; the lead runs in the
      // background. Any ``deliverable_updated`` it emits later arrives via the
      // ``useTaskEvents`` stream above and refreshes the card — no refetch here
      // (a refetch would race ahead of the lead's turn and see nothing new).
      await followUp.send(text);
    } catch {
      // Restore the draft so a failed send doesn't silently drop the input.
      setFollowUpDraft(text);
    }
  }, [followUpDraft, followUp.send]);
  const failureInfo = useMemo<{
    reason: string;
    failedAt: number;
  } | null>(() => {
    const events = detail?.events ?? [];
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const e = events[i];
      if (
        e.type === "kickoff_failed" ||
        e.type === "task_failed" ||
        e.type === "stopped"
      ) {
        const p = (e.payload ?? {}) as { error?: unknown; reason?: unknown };
        const v = p.error ?? p.reason;
        if (typeof v === "string" && v.trim()) {
          return { reason: v, failedAt: e.created_at };
        }
      }
    }
    return null;
  }, [detail]);
  // Lead agent display name (e.g. "产品原型设计师") — preferred over the
  // kernel slug ("pm") in the deliverable/failure metadata line.
  const leadAgentName = useMemo(() => {
    const slug = detail?.task.lead_agent_slug;
    if (!slug) return null;
    const m = members.find((x) => x.member.agent_slug === slug);
    return m?.agent?.name ?? slug;
  }, [members, detail]);

  // Kickoff attachments — staged by the user when launching the task.
  // Data shape (backend-driven): ``kickoff.payload.attachments`` is a
  // ``list[{ filename: string }]``. Until the backend writes this into
  // the kickoff event the array stays empty and the chip row hides
  // itself; UI is ready for the data to land.
  const kickoffAttachments = useMemo<string[]>(() => {
    const events = detail?.events ?? [];
    const ko = events.find((e) => e.type === "kickoff");
    if (!ko) return [];
    const raw = (ko.payload as { attachments?: unknown } | undefined)
      ?.attachments;
    if (!Array.isArray(raw)) return [];
    return raw
      .map((x) => {
        if (typeof x === "string") return x;
        if (x && typeof x === "object" && "filename" in x) {
          const fn = (x as { filename?: unknown }).filename;
          return typeof fn === "string" ? fn : "";
        }
        return "";
      })
      .filter((s) => s.length > 0);
  }, [detail]);
  // Total elapsed time from kickoff to terminal state (or now if still
  // running). Picks the earliest kickoff event as start and the latest
  // task_completed / *failed / stopped event as end. We re-render every
  // 1s while the task is active so the ticking duration counts up live
  // (matching the project-home task cards); the interval is torn down the
  // moment the task leaves ``active``, so idle pages don't keep ticking.
  const [nowTick, setNowTick] = useState(() => Date.now());
  useEffect(() => {
    if (detail?.task.status !== "active") return;
    const id = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, [detail?.task.status]);
  const taskDurationMs = useMemo<number | null>(() => {
    const events = detail?.events ?? [];
    if (events.length === 0) return null;
    const kickoff = events.find((e) => e.type === "kickoff") ?? events[0];
    const start = new Date(kickoff.created_at).getTime();
    if (Number.isNaN(start)) return null;
    const status = detail?.task.status;
    // Terminal state → take the last terminal event's timestamp; otherwise
    // the clock runs to ``nowTick`` (frozen while paused since the ticker
    // is gated on ``active``).
    let end = nowTick;
    if (status === "completed" || status === "failed" || status === "stopped") {
      for (let i = events.length - 1; i >= 0; i -= 1) {
        const e = events[i];
        if (
          e.type === "task_completed" ||
          e.type === "kickoff_failed" ||
          e.type === "task_failed" ||
          e.type === "stopped"
        ) {
          const t = new Date(e.created_at).getTime();
          if (!Number.isNaN(t)) {
            end = t;
            break;
          }
        }
      }
    }
    // Subtract time spent paused so the clock stops while paused and a
    // resumed task continues from where it left off rather than jumping
    // forward by the pause gap. Walks ``paused`` → ``resumed`` pairs; an
    // open trailing ``paused`` is counted up to ``end``.
    let paused = 0;
    let pauseStart: number | null = null;
    for (const e of events) {
      const ts = new Date(e.created_at).getTime();
      if (Number.isNaN(ts)) continue;
      if (e.type === "paused") {
        pauseStart = ts;
      } else if (e.type === "resumed" && pauseStart !== null) {
        paused += Math.max(0, ts - pauseStart);
        pauseStart = null;
      }
    }
    if (pauseStart !== null) paused += Math.max(0, end - pauseStart);
    return Math.max(0, end - start - paused);
  }, [detail, nowTick]);

  // Timeline nodes — collapse subtask_spawned + matching subtask outcome
  // (completed/failed/message with the same session_id) into one nested
  // group. Lets the user see the "parent dispatched → child returned"
  // relationship rather than a flat event stream. Other events stay
  // top-level.
  type TimelineNode =
    | { kind: "event"; event: TaskEvent }
    | {
        kind: "group";
        spawn: TaskEvent;
        outcome: TaskEvent | null;
      };
  const timelineNodes = useMemo<TimelineNode[]>(() => {
    const events = detail?.events ?? [];
    const nodes: TimelineNode[] = [];
    const groupBySession = new Map<string, TimelineNode & { kind: "group" }>();
    for (const e of events) {
      // ``task_plan_update`` is a plan SNAPSHOT stream consumed by the right
      // rail's 任务列表 (TaskContextPanel) — it's not a timeline event. Drop
      // it here so the activity feed isn't spammed with one row per node
      // status change (VALUZ-TASK). ``task_planned`` and ``plan_revised``
      // stay on the timeline as historical markers (when did Lead decide
      // the plan / change it), but their session-link is suppressed in
      // EventBody since the user looks at the right rail for current plan.
      if (e.type === "task_plan_update") continue;
      // ``deliverable_updated`` is a post-completion refinement marker emitted
      // when the lead edits the deliverable via the follow-up chat. It feeds the
      // deliverable card (``deriveDeliverable``), not the orchestration
      // timeline — drop it so a tweak doesn't surface as a stray "event".
      if (e.type === "deliverable_updated") continue;
      if (e.type === "subtask_spawned") {
        const node = {
          kind: "group" as const,
          spawn: e,
          outcome: null as TaskEvent | null,
        };
        nodes.push(node);
        if (e.session_id) groupBySession.set(e.session_id, node);
        continue;
      }
      if (
        e.session_id &&
        (e.type === "subtask_completed" || e.type === "subtask_failed")
      ) {
        const grp = groupBySession.get(e.session_id);
        if (grp && grp.outcome === null) {
          grp.outcome = e;
          continue;
        }
      }
      nodes.push({ kind: "event", event: e });
    }
    return nodes;
  }, [detail]);

  // Tail "Lead is working" indicator — shown when the task is active
  // AND the last node isn't already a "waiting for outcome" group (we
  // only want one in-flight signal, not two). Covers the gap r2 left
  // open: r2 spinner only fires inside a group with outcome=null, so
  // the lead's pre-dispatch phase (kickoff landed, nothing spawned
  // yet) had no live feedback at all.
  const showLeadTail = useMemo(() => {
    if (detail?.task.status !== "active") return false;
    if (timelineNodes.length === 0) return true;
    const last = timelineNodes[timelineNodes.length - 1];
    if (last.kind === "group" && last.outcome === null) return false;
    return true;
  }, [detail, timelineNodes]);

  if (loading) {
    return <PageLoader />;
  }

  if (!detail) {
    return (
      <div className="px-5 pt-6">
        <BackLink onClick={() => navigate(-1)} label={t("common.back")} />
        <p className="mt-6 text-sm text-ink-body">{t("common.error")}</p>
      </div>
    );
  }

  const { task, events } = detail;
  const isActive = task.status === "active";
  const isPaused = task.status === "paused";
  // ``blocked`` is the failed-but-resumable terminal (lead turn errored — e.g.
  // an API/socket drop — or unresolved subtasks). Surface a retry/继续 entry
  // that re-launches the lead via ``resume_task`` (the :intervene resume path).
  const isBlocked = task.status === "blocked";
  // A task created straight from a prompt has title === goal; showing both is
  // pure repetition. Only surface the goal card when it adds something — a goal
  // distinct from the title, or staged attachments.
  const goalDiffersFromTitle = task.goal.trim() !== task.title.trim();

  if (selectedArtifactPath || artifactLoading || artifactError) {
    return (
      <div className="flex h-full min-h-0 flex-col p-3">
        <ArtifactViewerShell
          artifact={artifact}
          content={artifactContent}
          loading={artifactLoading}
          error={artifactError}
          onReload={handleArtifactReload}
          onClose={handleArtifactClose}
          onCopyContent={handleArtifactCopy}
          onOpenExternal={handleArtifactOpenExternal}
        />
      </div>
    );
  }

  // ``leadSessionId`` / ``subtaskRuns`` / ``activeSubtask`` used to live here
  // for the inline right-rail aside. The aside now lives in the AppShell's
  // panel slot via ``setRightPanel(<TaskContextPanel … />)`` (see the effect
  // above), which re-derives those from ``detail`` itself — no need to
  // duplicate them in the render closure.

  // Activity / event timeline, extracted to a variable so the completed-state
  // layout can demote it into a collapsed disclosure (rendered before the
  // deliverable) while the active/paused/blocked layouts keep it inline in its
  // original position. ``subtask_spawned`` + matching outcome get nested into
  // one card so the user reads "PM dispatched X → X returned Y" as a unit.
  const timelineBody = (
    <>
      <div className="mb-3 flex items-center gap-2">
        <ListTodo className="h-3.5 w-3.5 text-[#6b63e8]" />
        <h2 className="text-[14px] font-semibold text-[#131313]">
          {t("task.eventsTitle")}
        </h2>
      </div>
      {events.length === 0 ? (
        <p className="text-xs text-ink-meta">{t("task.noEvents")}</p>
      ) : (
        <ol className="flex flex-col gap-4">
          {timelineNodes.map((node) => {
            if (node.kind === "event") {
              return (
                <li key={node.event.id} className="group flex gap-2">
                  <EventAvatar
                    evt={node.event}
                    members={members}
                    leadAgentName={leadAgentName}
                    leadAgentSlug={task.lead_agent_slug}
                    t={t}
                  />
                  <EventBody
                    evt={node.event}
                    meta={EVENT_META[node.event.type] ?? FALLBACK_META}
                    members={members}
                    leadAgentName={leadAgentName}
                    leadAgentSlug={task.lead_agent_slug}
                    t={t}
                    onOpenSession={(sid) =>
                      navigate(
                        `/conversation/${encodeURIComponent(sid)}?from_task=${encodeURIComponent(task.id)}`,
                      )
                    }
                    pad=""
                  />
                </li>
              );
            }
            // Group: parent spawn + nested outcome card.
            const spawnMeta = EVENT_META[node.spawn.type] ?? FALLBACK_META;
            const outcomeMeta = node.outcome
              ? (EVENT_META[node.outcome.type] ?? FALLBACK_META)
              : null;
            return (
              <li key={node.spawn.id} className="group flex gap-2">
                <EventAvatar
                  evt={node.spawn}
                  members={members}
                  leadAgentName={leadAgentName}
                  leadAgentSlug={task.lead_agent_slug}
                  t={t}
                />
                <div className="flex-1">
                  <GroupedEventCard
                    spawn={node.spawn}
                    outcome={node.outcome}
                    spawnMeta={spawnMeta}
                    outcomeMeta={outcomeMeta}
                    members={members}
                    leadAgentName={leadAgentName}
                    leadAgentSlug={task.lead_agent_slug}
                    t={t}
                    onOpenSession={(sid) =>
                      navigate(
                        `/conversation/${encodeURIComponent(sid)}?from_task=${encodeURIComponent(task.id)}`,
                      )
                    }
                  />
                </div>
              </li>
            );
          })}
          {/* Blocked-on-you: an agent raised an AskUserQuestion and the
            task can't proceed until the user answers. Far louder than the
            top-right inbox dot — a tappable amber card right where the
            user is reading, jumping straight into the session to answer. */}
          {taskPending.length > 0 && (
            <li className="flex gap-2">
              <div className="flex w-6 shrink-0 flex-col items-center self-stretch pt-0.5">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-100 text-amber-600">
                  <MessageCircleQuestion className="h-3.5 w-3.5" />
                </span>
                <span className="mt-1 -mb-3.5 w-px flex-1 bg-[#f7f8fa]" />
              </div>
              <button
                type="button"
                onClick={() =>
                  navigate(
                    `/conversation/${encodeURIComponent(taskPending[0].session_id)}`,
                  )
                }
                className="-mt-1 flex min-w-0 flex-1 flex-col gap-1 rounded-lg border border-amber-300/70 bg-amber-50/70 px-3.5 py-2.5 text-left transition-colors hover:bg-amber-50"
              >
                <span className="flex items-center gap-1.5">
                  <span className="text-sm font-semibold text-amber-900">
                    {t("task.needsConfirm" as Parameters<typeof t>[0])}
                  </span>
                  {taskPending.length > 1 && (
                    <span className="rounded-full bg-amber-200/80 px-1.5 text-[10px] font-medium leading-4 text-amber-800">
                      {taskPending.length}
                    </span>
                  )}
                </span>
                {pendingQuestionText(taskPending[0]) && (
                  <span className="line-clamp-2 text-xs leading-5 text-amber-800">
                    {pendingQuestionText(taskPending[0])}
                  </span>
                )}
                <span className="mt-0.5 inline-flex items-center gap-0.5 text-xs font-medium text-amber-700">
                  {t("task.goConfirm" as Parameters<typeof t>[0])}
                  <ChevronRight className="h-3 w-3" />
                </span>
              </button>
            </li>
          )}
          {taskPending.length === 0 && showLeadTail && (
            // Mirror ``EventAvatar`` (``pt-0.5``) + ``EventBody``
            // (``-mt-1 px-3 py-2``) so the loader icon and the
            // "Lead is working…" label line up with the You /
            // event rows above instead of drifting 12 px left and
            // a couple px up.
            <li className="flex gap-2">
              <div className="flex w-6 shrink-0 flex-col items-center self-stretch pt-0.5">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand/10 text-brand">
                  <Loader2 className="h-3 w-3 animate-spin" />
                </span>
                <span className="mt-1 -mb-3.5 w-px flex-1 bg-[#f7f8fa]" />
              </div>
              <div className="-mt-1 flex min-w-0 flex-1 items-center gap-2 rounded-md px-3 py-2">
                <span className="animate-pulse text-sm text-ink-meta">
                  {t("task.event.leadWorking" as Parameters<typeof t>[0])}
                </span>
              </div>
            </li>
          )}
        </ol>
      )}
    </>
  );

  // Derive sub-sidebar sections from runs/events.
  // - lead Run always sits at the top; sub-Runs follow in dispatch order.
  // v30 layout: 3-column shell (AppShell + center main + right ContextPanel).
  // The right rail rolls up Team / Todo / Runs from ``runs`` inside
  // TaskContextPanel itself — no derived state at the page level.

  return (
    // In-flight: ``min-h-full`` lets the wrapper fill the scrolling viewport so
    // the sticky action bar can pin to its bottom edge even when content is
    // short (``mt-auto`` on the bar pushes it down). Completed: ``h-full`` locks
    // the wrapper to exactly the viewport so the follow-up chat below can flex
    // to fill the remaining height and pin its composer to the bottom — the
    // page becomes a chat surface, not a scrolling document.
    <div
      className={cn(
        "flex w-full flex-col px-5 pb-5 pt-5",
        isCompleted ? "h-full" : "min-h-full",
      )}
    >
      <div className="flex min-w-0 items-center gap-2 text-sm leading-5">
        <button
          type="button"
          // "返回项目" lands on the project home. The legacy
          // ``/project-tasks/{id}`` page was retired — task kickoff is now the
          // project composer's "task" mode.
          onClick={() =>
            navigate(`/projects/${encodeURIComponent(task.project_id)}`)
          }
          className="inline-flex shrink-0 items-center gap-1 text-ink-meta transition-colors hover:text-ink-heading"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          <span>{t("task.back")}</span>
        </button>
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
        <span className="min-w-0 truncate font-medium text-ink-heading">
          {t("task.detailTitle" as Parameters<typeof t>[0])}
        </span>
      </div>

      {/* Reading column — every section between the breadcrumb above and
          the sticky action bar below shares the same 760-px column with
          24-px horizontal padding. The sticky bar lives outside this
          wrapper so it can extend edge-to-edge and run its own
          backdrop, but its inner action row mirrors the same width. */}
      <div
        className={cn(
          "mx-auto w-full max-w-[760px] px-6",
          // Completed: become a flex column that fills the locked-height
          // wrapper so the follow-up chat section can claim the leftover space
          // (``flex-1``) and the composer pins to the bottom.
          isCompleted && "flex min-h-0 flex-1 flex-col",
        )}
      >
        <div className="mt-4 flex w-full items-start justify-between gap-4">
          <div className="min-w-0">
            {/* Title row: just the title — status + agent + duration move
              to a dedicated metadata strip below so they line up under
              the title rather than wrapping inline. */}
            <h1 className="text-[18px] font-semibold leading-6 text-ink-heading">
              {task.title}
            </h1>
            <div className="mt-2 flex flex-wrap items-center text-[11px] font-normal leading-4">
              <span
                className={cn(
                  "inline-flex items-center gap-1",
                  task.status === "active"
                    ? "text-[#725cf9]"
                    : "text-[#898f9c]",
                )}
              >
                {task.status === "active" && (
                  <span className="h-[5px] w-[5px] rounded-full bg-[#725cf9] animate-pulse" />
                )}
                <TaskStatusLabel status={task.status} />
                {taskDurationMs !== null && (
                  <>
                    {" · "}
                    {t("task.totalDuration" as Parameters<typeof t>[0], {
                      duration: formatDuration(taskDurationMs, t as Translator),
                    })}
                  </>
                )}
              </span>
              <span className="mx-3 h-3 w-px bg-[#f3f4f6]" />
              <span className="inline-flex items-center gap-1.5 text-[#898f9c]">
                <span className="inline-flex h-4 shrink-0 items-center rounded-[4px] bg-[#725cf9]/10 px-1 text-[10px] font-normal leading-none text-[#725cf9]">
                  Lead
                </span>
                {leadAgentName ?? task.lead_agent_slug}
              </span>
            </div>
          </div>
        </div>

        {/* Goal card — shown only when it adds something beyond the title: a
          goal distinct from the title, or staged attachments. A prompt-launched
          task (title === goal) would otherwise repeat the heading verbatim. */}
        {(goalDiffersFromTitle || kickoffAttachments.length > 0) && (
        <section className="mt-4 w-full rounded-lg border border-surface-border bg-[#f7f7f8] px-4 py-3">
          {goalDiffersFromTitle && (
            <p className="whitespace-pre-wrap text-[12px] leading-5 text-[#131313]">
              {task.goal}
            </p>
          )}
          {/* Attachment chips — files staged by the user when launching
            this task. Source: ``kickoff.payload.attachments``. Hides
            entirely when empty so the card stays clean for goal-only
            tasks. */}
          {kickoffAttachments.length > 0 && (
            <ul className="mt-3 flex flex-wrap gap-1.5">
              {kickoffAttachments.map((filename) => (
                <li
                  key={filename}
                  className="inline-flex items-center gap-1.5 rounded-md border border-surface-border bg-surface-soft px-2 py-1 text-2xs text-ink-body"
                >
                  <Paperclip className="h-3 w-3 text-ink-meta" />
                  <span className="truncate max-w-[200px]" title={filename}>
                    {filename}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
        )}

        {/* Completed-state: the page flips. The run timeline is demoted to a
          collapsed disclosure (the process is no longer the subject), the
          deliverable card is promoted right under it as the main subject, and
          an inline follow-up chat with the lead is appended so the user can ask
          for tweaks. */}
        {isCompleted && (
          <button
            type="button"
            onClick={() => setRunTimelineOpen((v) => !v)}
            className="mt-4 flex w-full items-center gap-1.5 rounded-md text-[12px] text-ink-meta transition-colors hover:text-ink-heading focus-visible:outline-none focus-visible:ring-[1px] focus-visible:ring-ring/50"
            aria-expanded={runTimelineOpen}
          >
            <ChevronRight
              className={cn(
                "h-3.5 w-3.5 transition-transform",
                runTimelineOpen && "rotate-90",
              )}
            />
            <span>{t("task.followUp.runTimelineToggle")}</span>
            {!runTimelineOpen && (
              <span className="text-ink-muted">
                · {t("task.followUp.runTimelineExpand")}
              </span>
            )}
          </button>
        )}
        {isCompleted && runTimelineOpen && (
          <section className="mt-3 w-full">{timelineBody}</section>
        )}

        {/* Completed → deliverable card (green). Pulls the lead's final
          summary from the ``task_completed`` event payload. Footer makes
          the provenance explicit: who submitted it, when, and how many
          artifacts came with it — without that, the long body looks
          like a magic blob of text. */}
        {task.status === "completed" && completionInfo && (
          <section className="mt-5 w-full shrink-0">
            {/* Header: title + provenance metadata on the same row (who /
              when), matching the prototype's "✓ 交付结果 PM (lead) · 时间".
              The whole row is a toggle so the user can fold the deliverable
              away and give the full height to the follow-up chat below. */}
            <button
              type="button"
              onClick={() => setDeliverableOpen((v) => !v)}
              aria-expanded={deliverableOpen}
              className="mb-3 flex w-full flex-wrap items-center gap-x-2 gap-y-1 text-left"
            >
              <ChevronRight
                className={cn(
                  "h-3.5 w-3.5 shrink-0 text-[#98a1b2] transition-transform",
                  deliverableOpen && "rotate-90",
                )}
              />
              <CheckCheck className="h-3.5 w-3.5 text-[#6b63e8]" />
              <span className="text-sm font-semibold text-[#131313]">
                {t("task.deliverableTitle" as Parameters<typeof t>[0])}
              </span>
              {leadAgentName && (
                <span className="text-sm font-medium text-[#9aa3b2]">
                  {leadAgentName}
                </span>
              )}
              <span className="ml-auto text-sm tabular-nums text-[#9aa3b2]">
                {formatEventTime(completionInfo.completedAt)}
              </span>
            </button>

            {/* Artifacts file list (top half of the card per the prototype).
              Each row: 📄 filename + 「由 X 生成」. Path is the raw value
              the lead passed to ``finish_task(artifacts=…)``; we only
              show the basename so long project-relative paths don't
              dominate the row. */}
            {deliverableOpen && (
            <div className="overflow-hidden rounded-[8px] border border-[#e6e7e9] bg-white">
              {completionInfo.artifacts.length > 0 && (
                // ``max-h-[240px] overflow-y-auto`` caps the artifact list
                // so a 30-file deliverable doesn't push the summary
                // accordion off-screen; the user scrolls inside the list
                // instead of scrolling the whole page.
                <ul className="flex max-h-[280px] flex-col overflow-y-auto">
	                  {completionInfo.artifacts.map((path) => {
	                    const basename = path.split(/[\\/]/).pop() || path;
	                    const absolute = resolveArtifactPath(path, rootPath);
	                    return (
	                      <li key={path}>
	                        <button
	                          type="button"
	                          onClick={() => void openArtifactFile(path)}
	                          title={absolute}
                          className="group flex h-[54px] w-full items-center gap-3 px-4 text-left transition-colors hover:bg-[#fafbfd]"
                        >
                          <span
                            className={cn(
                              "flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px]",
                              artifactIconBgClassName(basename),
                            )}
                          >
                            <FileText
                              className={cn(
                                "h-4 w-4",
                                artifactIconClassName(basename),
                              )}
                            />
                          </span>
                          <div className="flex min-w-0 flex-1 flex-col justify-center">
                            <span
                              className="truncate text-[13px] font-semibold leading-5 text-[#1f2937]"
                              title={absolute}
                            >
                              {basename}
                            </span>
                            {leadAgentName && (
                              <span className="relative -top-0.5 text-[11px] leading-4 text-[#9aa3b2]">
                                {t(
                                  "task.artifactBy" as Parameters<typeof t>[0],
                                  {
                                    agent: leadAgentName,
                                  },
                                )}
                              </span>
                            )}
                          </div>
                          <ChevronRight className="h-4 w-4 shrink-0 text-[#c4cad4] transition-transform group-hover:translate-x-0.5" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}

              <details
                className={cn(
                  "group/d overflow-hidden bg-white",
                  completionInfo.artifacts.length > 0 && "border-t border-[#f3f4f6]",
                )}
              >
                <summary className="flex h-12 cursor-pointer items-center gap-3 px-4 text-left list-none [&::-webkit-details-marker]:hidden">
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[#98a1b2] transition-transform group-open/d:rotate-90" />
                  <span className="min-w-0 flex-1 text-[13px] font-semibold leading-5 text-[#131313]">
                    {t("task.completionSummary" as Parameters<typeof t>[0])}
                  </span>
                </summary>
                <div className="whitespace-pre-wrap px-3 pb-3 pt-0 text-[12px] leading-6 text-ink-body">
                  {completionInfo.summary}
                </div>
              </details>
            </div>
            )}
          </section>
        )}

        {/* Completed → inline follow-up chat with the lead. Scoped to the lead
          session's events strictly after completion (the hook gates on
          ``sinceTs``), so the user gets a clean conversation surface to request
          deliverable tweaks without the orchestration history bleeding in. */}
        {isCompleted && (
          // ``flex-1 min-h-0 flex-col``: claim all the height the reading
          // column has left, so the conversation is the page's main body and
          // the composer pins to the very bottom of the viewport.
          <section className="mt-6 flex w-full min-h-0 flex-1 flex-col">
            <div className="mb-3 flex shrink-0 items-center gap-2 text-[12px] font-medium text-ink-heading">
              <span className="h-px flex-1 bg-surface-border" />
              {t("task.followUp.heading")}
              <span className="h-px flex-1 bg-surface-border" />
            </div>
            {/* Scroll region is ALWAYS present (a ``flex-1`` spacer when empty)
              so the composer stays anchored to the bottom from the first
              render — no max-height cap; the turns scroll within the leftover
              viewport height as the conversation grows. */}
            <div
              ref={followUpScrollRef}
              className="min-h-0 flex-1 overflow-y-auto"
            >
              {followUp.turns.length > 0 && (
                <ConversationTurnList
                  turns={followUp.turns}
                  scrollContainerRef={followUpScrollRef}
                  sending={followUp.sending}
                  loading={false}
                  error={null}
                />
              )}
            </div>
            {/* Minimal composer. The full ``@valuz/ui`` ``Composer`` is
              model/runtime/agent-aware (skills, attachments, model picker) —
              none of which applies to a lead follow-up where runtime + model are
              already locked to the session. A plain textarea + send/stop button
              keeps the surface honest: no dead selectors, no fake "Model" pill.
              Enter sends; Shift+Enter inserts a newline. */}
            <div className="mt-3 shrink-0 rounded-md border border-surface-border bg-surface focus-within:border-ring focus-within:ring-[1px] focus-within:ring-ring/50">
              <Textarea
                value={followUpDraft}
                onChange={(e) => setFollowUpDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    void handleFollowUpSend();
                  }
                }}
                placeholder={t("task.followUp.placeholder")}
                aria-label={t("task.followUp.placeholder")}
                rows={2}
                disabled={followUp.sending}
                className="resize-none border-0 bg-transparent focus-visible:ring-0 focus-visible:border-transparent"
              />
              <div className="flex items-center justify-end px-2 pb-2">
                <Button
                  size="sm"
                  className="text-[12px]"
                  onClick={() => void handleFollowUpSend()}
                  disabled={followUp.sending || !followUpDraft.trim()}
                  loading={followUp.sending}
                >
                  {t("conversation.send")}
                </Button>
              </div>
            </div>
          </section>
        )}

        {/* Failed / stopped → failure card (red). Pulls the most recent
          failure event's error or reason. */}
        {(task.status === "failed" || task.status === "stopped") &&
          failureInfo && (
            <section className="mt-3 w-full rounded-xl border border-red-500/30 bg-red-50 p-4 dark:bg-red-500/10">
              <div className="mb-2 flex items-center gap-2">
                <XCircle className="h-4 w-4 text-red-600" />
                <span className="text-xs font-semibold text-red-700 dark:text-red-400">
                  {t("task.failureReasonTitle" as Parameters<typeof t>[0])}
                </span>
              </div>
              <div className="whitespace-pre-wrap text-sm leading-6 text-ink-body">
                {failureInfo.reason}
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-red-500/20 pt-2 text-2xs text-ink-meta">
                {leadAgentName && (
                  <span>
                    {t("task.failureBy" as Parameters<typeof t>[0], {
                      agent: leadAgentName,
                    })}
                  </span>
                )}
                <span className="tabular-nums">
                  · {formatEventTime(failureInfo.failedAt)}
                </span>
              </div>
            </section>
          )}

        {/* v30: per-action chips above the timeline have been folded into the
          sticky action bar at the bottom of the page — see the ``<div>``
          right before the dialogs. Keeping all task-level actions in one
          spot (modify goal / note / retry / pause / resume / stop /
          continue chat) matches the v28 5×4 button matrix from PRD §3.5. */}

        {/* Activity / event timeline — the live process view for in-flight,
          paused, and blocked tasks. For completed tasks the same ``timelineBody``
          is rendered higher up inside a collapsed disclosure (the deliverable,
          not the process, is the subject once the task finishes). */}
        {!isCompleted && <section className="mt-5 w-full">{timelineBody}</section>}
      </div>
      {/* /Reading column ---------------------------------------- */}

      {/* Sticky action bar — only shown while the task is still
          ``in-flight`` (active or paused). Terminal states (completed /
          failed / stopped) have no actionable next step on this page;
          the result is read-only by design — users continue work by
          opening a fresh task or chat from the project home. Hiding
          the bar entirely keeps the page distraction-free at rest.

          The bar carries three controls only — modify goal, the
          status-conditional pause/resume toggle, and stop. We
          deliberately dropped the v30 trio (加备注 / Retry / 继续对话):
          notes weren't being read by the lead, Retry was a kernel-
          pending placeholder, and "继续对话" routed into the lead's
          internal session which is the wrong abstraction for the
          user. See PR discussion for the full reasoning. */}
      {(isActive || isPaused) && (
        <div className="sticky bottom-0 -mx-5 mt-auto overflow-hidden px-5 py-3">
          <div className="absolute inset-0 bg-card/94 backdrop-blur-3xl" />
          <div className="relative z-10 mx-auto flex w-full max-w-[760px] flex-wrap items-center justify-center gap-2 px-6">
            <Button
              size="sm"
              variant="outline"
              className="text-[12px]"
              onClick={() => {
                setReviseGoal(task.goal);
                setReviseOpen(true);
              }}
              disabled={busy}
            >
              {t("task.reviseGoal")}
            </Button>
            {/* Status-driven middle slot: active → pause, paused →
                resume (primary, the natural next step after a pause). */}
            {isActive && (
              <Button
                size="sm"
                variant="outline"
                className="text-[12px]"
                onClick={() =>
                  void runIntervene({ action: "pause" }, "task.paused")
                }
                disabled={busy}
              >
                {t("task.pause")}
              </Button>
            )}
            {isPaused && (
              <Button
                size="sm"
                className="text-[12px]"
                onClick={() =>
                  void runIntervene({ action: "resume" }, "task.resumed")
                }
                disabled={busy}
              >
                {t("task.resume")}
              </Button>
            )}
            {/* Stop is destructive in both states; while active it's
                also the primary intent (the user is interrupting an
                in-flight task), so we keep it on the right edge. */}
            <Button
              size="sm"
              variant="destructive"
              className="bg-[#f54b4b] text-[12px] hover:bg-[#f54b4b]/90 focus-visible:ring-[#f54b4b]/20"
              onClick={() =>
                void runIntervene({ action: "stop" }, "task.stopped")
              }
              disabled={busy}
            >
              {t("task.stop")}
            </Button>
          </div>
        </div>
      )}

      {/* Blocked (failed-but-resumable): the lead turn errored — e.g. an API /
          socket drop — or left unresolved subtasks. Offer a single primary
          "retry/继续" that re-launches the lead via ``resume_task`` (the
          :intervene resume path, which accepts ``blocked``). */}
      {isBlocked && (
        <div className="sticky bottom-0 -mx-5 mt-auto overflow-hidden px-5 py-3">
          <div className="absolute inset-0 bg-card/94 backdrop-blur-3xl" />
          <div className="relative z-10 mx-auto flex w-full max-w-[760px] flex-wrap items-center justify-center gap-2 px-6">
            <Button
              size="sm"
              className="text-[12px]"
              onClick={() =>
                void runIntervene({ action: "resume" }, "task.resumed")
              }
              disabled={busy}
            >
              {t("task.retry")}
            </Button>
          </div>
        </div>
      )}

      {/* v30: file preview dialog removed. Artifact preview was a side
          feature of the inline Runs section (now also removed); for the
          MVP, users open the lead conversation to inspect artifacts. */}

      {/* Revise-goal dialog */}
      <Dialog open={reviseOpen} onOpenChange={setReviseOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("task.reviseGoal")}</DialogTitle>
          </DialogHeader>
          <DialogField label={t("task.goalLabel")} required>
            <Textarea
              value={reviseGoal}
              onChange={(e) => setReviseGoal(e.target.value)}
              rows={4}
            />
          </DialogField>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setReviseOpen(false)}
              disabled={busy}
            >
              {t("common.cancel")}
            </Button>
            <Button
              onClick={async () => {
                const ok = await runIntervene(
                  { action: "revise_goal", goal: reviseGoal.trim() },
                  "task.goalRevised",
                );
                if (ok) setReviseOpen(false);
              }}
              disabled={busy || !reviseGoal.trim()}
            >
              {t("task.reviseGoalSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

// ---------------------------------------------------------------------
// Timeline sub-components
// ---------------------------------------------------------------------

/** Resolve actor → display label. Backend conventions:
 *  - ``user``                → "You"
 *  - terminal events (task_completed / kickoff_failed) use the lead
 *    session id; collapse to the lead agent name
 *  - everything else is an ``agent_slug`` we can ``join`` against
 *    ``members`` to get the display name */
function resolveActor(
  actor: string,
  type: string,
  members: MemberWithAgent[],
  leadAgentName: string | null,
  leadAgentSlug: string,
  t: Translator,
): string {
  if (actor === "user") return t("task.actorYou");
  // Lead-driven events carry the lead SESSION id as actor — collapse to the
  // lead agent name (VALUZ-TASK adds plan/review events on this path).
  if (
    type === "task_completed" ||
    type === "task_failed" ||
    type === "kickoff_failed" ||
    type === "task_planned" ||
    type === "plan_revised" ||
    type === "subtask_reviewed"
  ) {
    return leadAgentName ?? leadAgentSlug;
  }
  const m = members.find((x) => x.member.agent_slug === actor);
  return m?.agent?.name ?? actor;
}

function eventAvatarTone(evt: TaskEvent): string {
  void evt;
  return "bg-brand/10 text-brand";
}

function eventAvatarIcon(
  evt: TaskEvent,
): ComponentType<{ className?: string }> {
  if (evt.actor === "user") return User;
  return (EVENT_META[evt.type] ?? FALLBACK_META).icon;
}

function EventAvatar({
  evt,
  members,
  leadAgentName,
  leadAgentSlug,
  t,
}: {
  evt: TaskEvent;
  members: MemberWithAgent[];
  leadAgentName: string | null;
  leadAgentSlug: string;
  t: Translator;
}) {
  void members;
  void leadAgentName;
  void leadAgentSlug;
  void t;
  const Icon = eventAvatarIcon(evt);
  return (
    <div className="flex w-6 shrink-0 flex-col items-center self-stretch pt-0.5">
      <span
        className={cn(
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
          eventAvatarTone(evt),
        )}
      >
        <Icon className="h-3 w-3" />
      </span>
      <span className="mt-1 -mb-3.5 w-px flex-1 bg-[#f7f8fa]" />
    </div>
  );
}

function EventBody({
  evt,
  meta,
  members,
  leadAgentName,
  leadAgentSlug,
  t,
  onOpenSession,
  pad,
  compact,
  hideSessionLink,
}: {
  evt: TaskEvent;
  meta: EventMeta;
  members: MemberWithAgent[];
  leadAgentName: string | null;
  leadAgentSlug: string;
  t: Translator;
  onOpenSession: (sid: string) => void;
  pad: string;
  compact?: boolean;
  /** Suppress the "查看会话" link + click affordance even when the
   *  event has a session_id. Used by:
   *  - subtask group's nested outcome card: spawn + outcome share the
   *    same member session, so one link on the parent is enough.
   *  - the caller passes this on event types where the link target
   *    is conceptually wrong (e.g. ``subtask_reviewed`` whose session
   *    is the reviewee, but the review itself is a lead decision). */
  hideSessionLink?: boolean;
}) {
  const detail = eventDetail(evt, t);
  const actorLabel = resolveActor(
    evt.actor,
    evt.type,
    members,
    leadAgentName,
    leadAgentSlug,
    t,
  );
  // "查看会话" jumps to the event's session_id for a read-only trace
  // view. Three types we suppress the link on:
  //  - ``subtask_reviewed`` — session_id is the REVIEWEE's session,
  //    but the review itself is a lead decision; jumping to the
  //    sub-Run's chat from "✓ 审核通过" is the wrong mental model.
  //  - ``task_planned`` / ``plan_revised`` — the right rail's 任务列表
  //    already shows the current plan snapshot live, so a session
  //    jump here just adds redundant clutter; the row stays visible
  //    on the timeline as a historical marker but offers no link.
  // Everything else with a session_id stays linkable.
  const nonLinkableTypes = new Set([
    "subtask_reviewed",
    "task_planned",
    "plan_revised",
  ]);
  const linkSuppressed = hideSessionLink || nonLinkableTypes.has(evt.type);
  const clickable = !!evt.session_id && !linkSuppressed;
  return (
    <div
      className={`${pad} ${
        clickable
          ? "-mt-1 -ml-1 min-w-0 flex-1 cursor-pointer rounded-md px-3 py-2 transition-colors group-hover:bg-[#f7f7f8]"
          : "-mt-1 -ml-1 min-w-0 flex-1 rounded-md px-3 py-2 transition-colors group-hover:bg-[#f7f7f8]"
      } ${compact ? "flex-1" : ""}`}
      onClick={
        clickable ? () => onOpenSession(evt.session_id as string) : undefined
      }
    >
      <div className="flex items-center gap-2">
        <span className="text-[12px] font-semibold leading-5 text-ink-heading">
          {actorLabel}
        </span>
        <span className="text-[11px] font-semibold leading-5 text-ink-meta">
          {t(meta.labelKey as Parameters<typeof t>[0])}
        </span>
        <span className="ml-auto flex min-w-[112px] items-center justify-end gap-2 text-right opacity-0 transition-opacity group-hover:opacity-100">
          <span className="text-[11px] tabular-nums text-ink-meta">
            {formatEventTime(evt.created_at)}
          </span>
          {clickable && (
            <span className="text-[11px] text-brand">
              {t("task.viewSession" as Parameters<typeof t>[0])}
            </span>
          )}
        </span>
      </div>
      {detail && (
        <p className="mt-1 whitespace-pre-wrap text-[12px] leading-5 text-ink-body">
          {detail}
        </p>
      )}
    </div>
  );
}

function GroupedEventCard({
  spawn,
  outcome,
  spawnMeta,
  outcomeMeta,
  members,
  leadAgentName,
  leadAgentSlug,
  t,
  onOpenSession,
}: {
  spawn: TaskEvent;
  outcome: TaskEvent | null;
  spawnMeta: EventMeta;
  outcomeMeta: EventMeta | null;
  members: MemberWithAgent[];
  leadAgentName: string | null;
  leadAgentSlug: string;
  t: Translator;
  onOpenSession: (sid: string) => void;
}) {
  const spawnDetail = eventDetail(spawn, t);
  const spawnActor = resolveActor(
    spawn.actor,
    spawn.type,
    members,
    leadAgentName,
    leadAgentSlug,
    t,
  );
  const clickable = !!spawn.session_id;
  const outcomeLabel = outcome
    ? t(
        (outcomeMeta?.labelKey ?? "task.event.subtaskCompleted") as Parameters<
          typeof t
        >[0],
      )
    : t("task.subtaskWaiting" as Parameters<typeof t>[0]);
  const outcomeTime = outcome ? formatEventTime(outcome.created_at) : "";

  return (
    <div
      className={cn(
        "-mt-1 -ml-1 min-w-0 flex-1 rounded-md px-3 py-2 transition-colors group-hover:bg-[#f7f7f8]",
        clickable && "cursor-pointer",
      )}
      onClick={
        clickable ? () => onOpenSession(spawn.session_id as string) : undefined
      }
    >
      <div className="flex items-center gap-2">
        <span className="text-[12px] font-semibold leading-5 text-ink-heading">
          {spawnActor}
        </span>
        <span className="text-[11px] font-semibold leading-5 text-ink-meta">
          {t(spawnMeta.labelKey as Parameters<typeof t>[0])}
        </span>
        <span className="ml-auto flex min-w-[112px] items-center justify-end gap-2 text-right opacity-0 transition-opacity group-hover:opacity-100">
          <span className="text-[11px] tabular-nums text-ink-meta">
            {formatEventTime(spawn.created_at)}
          </span>
          {clickable && (
            <span className="text-[11px] text-brand">
              {t("task.viewSession" as Parameters<typeof t>[0])}
            </span>
          )}
        </span>
      </div>
      {spawnDetail && (
        <p className="mt-1 whitespace-pre-wrap text-[12px] leading-5 text-ink-body">
          {spawnDetail}
        </p>
      )}
      <div
        className={cn(
          "mt-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] leading-4",
          outcome
            ? "bg-emerald-50 text-emerald-700"
            : "bg-surface-soft text-ink-meta",
        )}
      >
        {outcomeLabel}
        {outcomeTime && <span className="ml-2">· {outcomeTime}</span>}
      </div>
    </div>
  );
}
