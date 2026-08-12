import {
  useCallback,
  useEffect,
  useLayoutEffect,
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
  BackLink,
  Badge,
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
  type RuntimeStartLocation,
} from "@valuz/ui";
import {
  agentsApi,
  tasksApi,
  projectsApi,
  useNotifications,
  useTaskEvents,
  useTranslation,
  type IntervenePayload,
  type MemberWithAgent,
  type TaskDetail,
  type TaskEvent,
  type TaskTokenUsage,
  recordEntityOrigin,
  useDefaultRuntimeLocation,
  useEntityOrigin,
} from "@valuz/core";
import type { FileTreeNode } from "@valuz/ui";
import { useProjectOutlet } from "@valuz/app/layout";
import { usePlatform } from "@valuz/app/platform";
import {
  TaskContextPanel,
  type PlannedSubtask,
} from "../components/TaskContextPanel";
import { toFileTree } from "../lib/file-tree";
import { TaskStatusLabel } from "../components/TaskStatusLabel";
import { TaskTokenUsagePopover } from "../components/TaskTokenUsagePopover";
import { NotificationCard } from "../components/NotificationInbox";
import {
  useLeadFollowUpChat,
  useAskUserQuestionCards,
  useSkillSubmissionCards,
} from "../hooks";
import { deriveDeliverable } from "./task-detail/deliverable";
import { ArtifactSplitPane } from "../components/ArtifactSplitPane";
import { useArtifactFile } from "../hooks/use-artifact-file";
import { eventDetail } from "../lib/task-event-detail";
import { toAbsoluteProjectPath, toProjectRelativePath } from "../lib/project-paths";

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
  // Chat-plan flow (draft → commit): without these entries both events fell
  // into FALLBACK_META and rendered as "任务已发起" — twice, with the raw
  // originating-session UUID as the actor.
  task_drafted: {
    icon: FileText,
    node: "bg-ink-meta/10 text-ink-body",
    labelKey: "task.event.taskDrafted",
  },
  committed: {
    icon: Flag,
    node: "bg-brand/10 text-brand",
    labelKey: "task.event.committed",
  },
  // Kickoff couldn't start the lead (missing credentials / build failure).
  // Without this entry the row fell back to the generic "kickoff" label and
  // read as "任务已发起" on a run that actually failed.
  kickoff_failed: {
    icon: XCircle,
    node: "bg-error-light text-error-text",
    labelKey: "task.event.kickoffFailed",
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
  // User cancelled a member run (stop_subtask / conversation-page interrupt).
  // Amber, not red — an intentional stop is not a failure; the node moved to
  // rework and stays re-dispatchable.
  subtask_stopped: {
    icon: Square,
    node: "bg-amber-500/10 text-amber-500",
    labelKey: "task.event.subtaskStopped",
  },
  // Lead → member: the lead sent a running member a follow-up instruction.
  // (Before 2026-07 this type also covered the member → lead direction, split
  // apart only by `payload.direction`; historical rows still land here.)
  subtask_message: {
    icon: MessageSquare,
    node: "bg-indigo-500/10 text-indigo-500",
    labelKey: "task.event.subtaskMessage",
  },
  // Member → lead: the member finished a round of work and reported back.
  subtask_reported: {
    icon: MessageSquare,
    node: "bg-indigo-500/10 text-indigo-500",
    labelKey: "task.event.subtaskReported",
  },
  user_note: {
    icon: MessageSquare,
    node: "bg-ink-meta/10 text-ink-body",
    labelKey: "task.event.userNote",
  },
  // A user instruction pushed into the lead — either a chat inject (S4) or
  // the text riding along with a resume (":intervene action=resume text=…").
  user_inject: {
    icon: MessageSquare,
    node: "bg-brand/10 text-brand",
    labelKey: "task.event.userInject",
  },
  // An agent (lead or member) is blocked on a user question (Decision Inbox).
  // Amber = needs your attention, not a failure.
  awaiting_user: {
    icon: MessageCircleQuestion,
    node: "bg-warning-light text-warning-text",
    labelKey: "task.event.awaitingUser",
  },
  user_answered: {
    icon: CheckCircle2,
    node: "bg-success-light text-success-text",
    labelKey: "task.event.userAnswered",
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
  // finish_task with a stopped final status. Same terminal-stop semantics as
  // ``stopped`` above, so it reuses that label; distinct type only because the
  // backend tags the lead-emitted finish path separately.
  task_stopped: {
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

// Unknown / newly-added event types. The label must stay NEUTRAL: this used
// to be ``task.event.kickoff``, so an ``abandoned`` draft and a DROPPED user
// instruction both rendered as "任务已发起".
const FALLBACK_META: EventMeta = {
  icon: MessageSquare,
  node: "bg-ink-meta/10 text-ink-body",
  labelKey: "task.event.unknown",
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


export const TaskDetailPage = () => {
  const { taskId = "" } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { t } = useTranslation();
  const platform = usePlatform();
  const { setHeader, setHideHeader, setRightPanel } = useProjectOutlet();
  // Pending confirmations (AskUserQuestion) raised by this task's agents —
  // surfaced prominently in the timeline so the user isn't left thinking the
  // task is just "working" when it's actually blocked on their answer.
  // Open question notifications for THIS task — surfaced inline so the user
  // isn't left thinking the task is just "working" when it's blocked on them.
  const taskPending = useNotifications().filter(
    (e) => e.kind === "question" && e.task_id === taskId,
  );

  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [tokenUsage, setTokenUsage] = useState<TaskTokenUsage | null>(null);
  const [members, setMembers] = useState<MemberWithAgent[]>([]);
  const [fileTree, setFileTree] = useState<FileTreeNode[]>([]);
  const [rootPath, setRootPath] = useState<string>("");
  // Project display name — only used as the "已绑定到 X" label on a
  // project-bound skill save in the follow-up chat.
  const [projectName, setProjectName] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const selectedFileParam = searchParams.get("file");

  // Deep-link origin fast path (multi-target editions): task links can carry
  // ``?origin=cloud`` so every task-scoped call below routes to the owning
  // backend without a probe. The edition adapter validates the value;
  // single-target builds have no adapter -> no-op.
  const originParam = searchParams.get("origin");
  useEffect(() => {
    if (originParam && taskId) recordEntityOrigin(taskId, originParam);
  }, [originParam, taskId]);

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

  const loadTokenUsage = useCallback(async () => {
    try {
      setTokenUsage(await tasksApi.getTaskUsage(taskId));
    } catch {
      // Usage is diagnostic metadata; a read failure must not obscure the task.
      setTokenUsage(null);
    }
  }, [taskId]);

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

  const runCount = detail?.runs.length ?? 0;
  useEffect(() => {
    void Promise.resolve().then(loadTokenUsage);
  }, [loadTokenUsage, status, runCount]);

  useEffect(() => {
    if (status !== "active") return;
    const interval = setInterval(() => void loadTokenUsage(), 10_000);
    return () => clearInterval(interval);
  }, [status, loadTokenUsage]);

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
      setProjectName("");
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
      setProjectName(ws?.name ?? "");
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

  const locateArtifactFile = useCallback(
    (path: string) => ({
      absolutePath: toAbsoluteProjectPath(path, rootPath),
      relativePath: toProjectRelativePath(path, rootPath) ?? path,
    }),
    [rootPath],
  );
  const artifactFile = useArtifactFile({
    projectId: projectId ?? null,
    platform,
    locate: locateArtifactFile,
    missingErrorMessage: t("task.artifactOpenInFinder" as Parameters<typeof t>[0]),
    // The file lives on the backend that owns the task — route the resolve with
    // the same ref the rest of this page uses.
    baseRef: { taskId: taskId || undefined, projectId: projectId ?? undefined },
    // The preview pane carries a tab strip, so opening a second document adds
    // to the set instead of replacing what's on screen.
    multiTab: true,
  });
  // The split pane consumes the loaded document itself; the page keeps only
  // what it needs for URL sync and the copy / reveal actions.
  const {
    activePath: activeArtifactPath,
    selectedPath: selectedArtifactPath,
    content: artifactContent,
    open: loadArtifact,
    reload: reloadArtifact,
    close: closeArtifact,
  } = artifactFile;

  const openArtifactFile = useCallback(
    async (relPath: string, options?: { syncUrl?: boolean }) => {
      if (!projectId) return;
      const normalized = toProjectRelativePath(relPath, rootPath);
      if (
        options?.syncUrl !== false &&
        normalized &&
        searchParams.get("file") !== normalized
      ) {
        setSearchParams(
          (current) => {
            const next = new URLSearchParams(current);
            next.set("file", normalized);
            return next;
          },
          { replace: false },
        );
      }
      await loadArtifact(relPath);
    },
    [loadArtifact, projectId, rootPath, searchParams, setSearchParams],
  );

  // ?file is an output of the focused tab, and only an input when it changed
  // from outside this page. Without that distinction the two effects below
  // ping-pong: each reads the other's not-yet-settled value and "corrects" it.
  // ?file names the focused document. It is an *output* while anything is
  // open — the tab strip is the source of truth — and only an input when the
  // preview is closed, i.e. on load or after a deep link. Treating a stale
  // param as an instruction is what made these two effects ping-pong: each
  // read the other's not-yet-settled value and "corrected" it.
  const authoredFileParamRef = useRef<string | null>(null);
  const hadArtifactRef = useRef(false);

  useEffect(() => {
    if (activeArtifactPath) {
      hadArtifactRef.current = true;
      if (activeArtifactPath === selectedFileParam) return;
      authoredFileParamRef.current = activeArtifactPath;
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("file", activeArtifactPath);
          return next;
        },
        { replace: true },
      );
      return;
    }
    // Nothing open. Clear the param only if something *was* open — on mount it
    // just means the deep link hasn't been consumed yet.
    if (!hadArtifactRef.current || !selectedFileParam) return;
    hadArtifactRef.current = false;
    // Claim the value being removed, not null: effects run in declaration
    // order, so the reader below sees this stale param before the clear lands
    // and would otherwise reopen what was just closed.
    authoredFileParamRef.current = selectedFileParam;
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("file");
        return next;
      },
      { replace: true },
    );
  }, [activeArtifactPath, selectedFileParam, setSearchParams]);

  useEffect(() => {
    if (!selectedFileParam) {
      // The clear has landed; drop the claim so a later deep link to the same
      // document is honoured rather than mistaken for our own echo.
      authoredFileParamRef.current = null;
      return;
    }
    // Something is already focused, so the param is this effect's own trailing
    // output rather than a request.
    if (activeArtifactPath) return;
    // Nothing is focused but the param still names what we last wrote — the
    // reader just closed it and the clear hasn't landed. Reopening it here is
    // how "close the last tab" used to bounce straight back.
    if (selectedFileParam === authoredFileParamRef.current) return;
    // The project root arrives with the detail fetch, and the path the deep
    // link names is relative to it. Resolving before it lands builds a bogus
    // absolute path and lands the tab in an error state it never retries out
    // of — wait for the root, then open.
    if (!rootPath) return;
    const timer = window.setTimeout(() => {
      void openArtifactFile(selectedFileParam, { syncUrl: false });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [rootPath, activeArtifactPath, openArtifactFile, selectedFileParam]);

  const handleArtifactReload = useCallback(() => {
    void reloadArtifact();
  }, [reloadArtifact]);

  const handleArtifactClose = useCallback(() => {
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("file");
        return next;
      },
      { replace: true },
    );
    closeArtifact();
  }, [closeArtifact, setSearchParams]);

  const handleArtifactCopy = useCallback(() => {
    if (artifactContent?.kind !== "text") return;
    void navigator.clipboard
      ?.writeText(artifactContent.content)
      .then(() => toast.success(t("common.copied" as Parameters<typeof t>[0])))
      .catch(() => toast.error(t("common.failed" as Parameters<typeof t>[0])));
  }, [artifactContent, t]);

  const handleArtifactOpenExternal = useCallback(() => {
    if (!selectedArtifactPath) return;
    void openArtifact(
      locateArtifactFile(selectedArtifactPath).absolutePath,
      t as Translator,
    );
  }, [locateArtifactFile, selectedArtifactPath, t]);

  // Open a project file from the right-rail file tree (double-click /
  // right-click → open). The tree node's ``path`` is project-relative, so
  // resolve it against the cwd and hand off to the same ``open_in_finder``
  // IPC (``shell.openPath``) the artifact list uses — opens the file in its
  // OS-associated app.
  const handleOpenFileExternal = useCallback(
    (relPath: string) => {
      if (!rootPath) return;
      void openArtifact(
        toAbsoluteProjectPath(relPath, rootPath),
        t as Translator,
      );
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
        taskStatus={detail.task.status}
        onRefreshFiles={refreshFileTree}
        onOpenInFinder={rootPath ? handleOpenProjectInFinder : undefined}
        onPreviewFile={
          projectId ? (path) => void openArtifactFile(path) : undefined
        }
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

  const [draftBusy, setDraftBusy] = useState<"commit" | "abandon" | null>(null);
  const runDraftAction = useCallback(
    async (action: "commit" | "abandon") => {
      setDraftBusy(action);
      try {
        const caller = detail?.task.id ?? "";
        if (action === "commit") {
          await tasksApi.commit(taskId, { caller_session_id: caller });
        } else {
          await tasksApi.abandon(taskId, { caller_session_id: caller });
        }
        await loadData();
      } catch (err) {
        console.warn(`${action}_task from TaskDetailPage failed`, err);
      } finally {
        setDraftBusy(null);
      }
    },
    [taskId, detail, loadData],
  );

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
  // Startup phase for the follow-up turn header, mirroring the conversation
  // page: while the lead's runtime is coming up the header names that rather
  // than claiming to process. A single-backend build observes no origin, so it
  // falls back to whatever that build declared its one backend to be.
  const leadExecOrigin = useEntityOrigin(leadSessionId, "session");
  const defaultRuntimeLocation = useDefaultRuntimeLocation();
  const followUpStartingRuntime: RuntimeStartLocation | null =
    followUp.awaitingRuntime
      ? (leadExecOrigin ?? defaultRuntimeLocation) === "cloud"
        ? "cloud"
        : "local"
      : null;
  // Render the Lead's ``AskUserQuestion`` tool as the interactive question card
  // (matching the main chat), driven by the follow-up event stream.
  const askCards = useAskUserQuestionCards({
    events: followUp.events,
    sessionId: isCompleted ? leadSessionId : null,
  });
  // Render the Lead's ``submit_skill`` tool as the skill-creator proposal card
  // (save / dismiss) — a follow-up tweak can spin up a skill just like the main
  // chat, so the card must work here too.
  const skillCards = useSkillSubmissionCards({
    sessionId: isCompleted ? leadSessionId : null,
    turns: followUp.turns,
    sending: followUp.sending,
    projectLabel: projectName || null,
  });
  // Compose the follow-up tool-card renderers: skill submission first, then the
  // AskUserQuestion card; the first non-null wins, otherwise the turn list
  // falls back to the generic tool card.
  const renderSkillCard = skillCards.renderToolCall;
  const renderAskCard = askCards.renderToolCall;
  const renderFollowUpToolCall = useCallback(
    (tool: { id: string; title: string; input?: string; output?: string }) =>
      renderSkillCard(tool) ?? renderAskCard(tool),
    [renderSkillCard, renderAskCard],
  );

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
    // The server terminal-closes streams of finished tasks; this subscriber
    // exists precisely to follow a completed task, so opt out.
    { keepAlive: true },
  );

  const [followUpDraft, setFollowUpDraft] = useState("");
  // Draft for the halted-task (paused/blocked/stopped) resume composer —
  // optional guidance that rides along with the resume intervene call.
  const [resumeDraft, setResumeDraft] = useState("");
  const followUpScrollRef = useRef<HTMLDivElement>(null);
  const followUpTurnsLenRef = useRef(0);
  // Anchor for the "open at the latest content" jump — see the scroll effect
  // below. ``initialScrollTaskRef`` gates it to once per task id.
  const contentRef = useRef<HTMLDivElement>(null);
  const initialScrollTaskRef = useRef<string | null>(null);
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
      // ``task_blocked`` is what the backend actually emits when the lead
      // turn errors (task-level failure folds into ``blocked``, never
      // ``task_failed`` — that type only survives for legacy rows). Without
      // it a lead-crash showed a bare "retry" button with no reason at all.
      if (
        e.type === "kickoff_failed" ||
        e.type === "task_failed" ||
        e.type === "task_blocked" ||
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
    if (
      status === "completed" ||
      status === "failed" ||
      status === "blocked" ||
      status === "stopped"
    ) {
      for (let i = events.length - 1; i >= 0; i -= 1) {
        const e = events[i];
        if (
          e.type === "task_completed" ||
          e.type === "kickoff_failed" ||
          e.type === "task_failed" ||
          e.type === "task_blocked" ||
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
        (e.type === "subtask_completed" ||
          e.type === "subtask_failed" ||
          e.type === "subtask_stopped")
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

  // On first open of a task, jump to the latest content instead of the top: the
  // newest timeline events are what the user wants when they revisit an active
  // task (its timeline scrolls the page). Gated to once per task id via
  // ``initialScrollTaskRef`` so streaming SSE updates — or a user who has
  // scrolled up to re-read history — aren't yanked back to the bottom. The
  // completed layout is viewport-locked (its follow-up chat owns its own
  // stick-to-bottom), so the scroll container isn't overflowing and this is a
  // no-op there.
  useEffect(() => {
    const id = detail?.task.id;
    if (!id || initialScrollTaskRef.current === id) return;
    const anchor = contentRef.current;
    if (!anchor) return;
    // The nearest scrollable ancestor is the AppShell ``<main>`` (overflow-auto).
    let sc: HTMLElement | null = anchor.parentElement;
    while (sc) {
      const oy = getComputedStyle(sc).overflowY;
      if (oy === "auto" || oy === "scroll") break;
      sc = sc.parentElement;
    }
    if (!sc) return;
    initialScrollTaskRef.current = id;
    const el = sc;
    // Double rAF: wait for the timeline (markdown / cards) to lay out so
    // ``scrollHeight`` is final before we jump.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      }),
    );
  }, [detail?.task.id, timelineNodes]);

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
  // ``stopped`` is a soft terminal: the backend state machine allows
  // stopped→active and ``resume_task`` accepts it (reconcile members +
  // re-drive the lead), so the page offers a resume entry — a stopped task
  // with no way forward strands the user ("任务停了啥也干不了").
  const isStopped = task.status === "stopped";
  // ``failed`` is a LEGACY status (pre-dates folding task failure into
  // ``blocked``). Old rows still carry it; the backend now resumes them like
  // blocked, so they get the same halted bar instead of a dead read-only page.
  const isFailedLegacy = task.status === "failed";
  // Every halted state shares one interaction surface: an optional
  // instruction composer + resume. "回复并恢复" is one intervene call —
  // the text lands in the respawned lead's recovery brief.
  const isHalted = isPaused || isBlocked || isStopped || isFailedLegacy;
  // A task created straight from a prompt has title === goal; showing both is
  // pure repetition. Only surface the goal card when it adds something — a goal
  // distinct from the title, or staged attachments.
  const goalDiffersFromTitle = task.goal.trim() !== task.title.trim();

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
                    leadSessionId={leadSessionId}
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
                {/* ``min-w-0`` is load-bearing: without it this flex child keeps
                    ``min-width: auto`` and grows to the widest unbreakable token
                    (long API-error JSON / paths), so the nested card's own
                    ``min-w-0`` can't shrink it and the text overflows the reading
                    column. EventBody is the flex child directly and already
                    carries min-w-0; this wrapper must match. */}
                <div className="min-w-0 flex-1">
                  <GroupedEventCard
                    spawn={node.spawn}
                    outcome={node.outcome}
                    spawnMeta={spawnMeta}
                    outcomeMeta={outcomeMeta}
                    members={members}
                    leadAgentName={leadAgentName}
                    leadAgentSlug={task.lead_agent_slug}
                    taskStatus={task.status}
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
              {/* Answer INLINE on the task page — reusing the same
                  ``DecisionEntryCard`` the inbox drawer uses (submit POSTs to
                  ``/actions``; the SSE ``resolved`` frame clears it). Previously
                  this card only navigated into the lead conversation, which is
                  exactly the "只能在 Lead 对话才能看到/回答" gap. The card keeps
                  its own "在会话中查看" secondary link for users who want the
                  full context. */}
              <div className="-mt-1 flex min-w-0 flex-1 flex-col gap-2">
                <span className="flex items-center gap-1.5">
                  <span className="text-sm font-semibold text-warning-text">
                    {t("task.needsConfirm" as Parameters<typeof t>[0])}
                  </span>
                  {taskPending.length > 1 && (
                    <Badge
                      variant="warning"
                      className="px-1.5 py-0 text-[10px] leading-4"
                    >
                      {taskPending.length}
                    </Badge>
                  )}
                </span>
                {taskPending.map((entry) => (
                  <NotificationCard key={entry.id} entry={entry} />
                ))}
              </div>
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
    <ArtifactSplitPane
      file={artifactFile}
      onReload={handleArtifactReload}
      onClose={handleArtifactClose}
      onCopyContent={handleArtifactCopy}
      onOpenExternal={handleArtifactOpenExternal}
    >
      {/* THIS surface owns its scroll. It used to lean on the AppShell's own
          scroll box, which stopped working the moment the page moved inside
          ``ArtifactSplitPane``: the split's content column is a viewport-height
          panel carrying an inline ``overflow: hidden`` (so a drag can never
          spill one column into the other). That clips FIRST, so the shell's
          box never sees overflowing content and never grows a scrollbar — the
          timeline was simply cut off at the fold, with no wheel or bar to
          reach the rest. */}
      <div className="h-full overflow-y-auto">
      {/* In-flight: ``min-h-full`` lets the wrapper fill the scrolling viewport
          so the sticky action bar can pin to its bottom edge even when content
          is short (``mt-auto`` on the bar pushes it down). Completed:
          ``h-full`` locks the wrapper to exactly the viewport so the follow-up
          chat below can flex to fill the remaining height and pin its composer
          to the bottom — the page becomes a chat surface, not a scrolling
          document. */}
      <div
      ref={contentRef}
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
          // ``break-words`` (inherited) breaks long unbreakable runs — API-error
          // JSON, URLs, hashes in timeline events / instruction / failure text —
          // so they wrap instead of overflowing the 760px reading column.
          "mx-auto w-full max-w-[760px] px-6 break-words",
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
              {/* "等待你确认" chip — the task is nominally still active/running
                  but is actually blocked on the user's answer. Without this the
                  header just says "Running" and the user has no signal the task
                  needs them. Amber, tappable to the inline card below. */}
              {taskPending.length > 0 && (
                <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-warning-light px-2 py-0.5 text-2xs font-medium text-warning-text">
                  <MessageCircleQuestion className="h-3 w-3" />
                  {t("task.awaitingUserChip" as Parameters<typeof t>[0])}
                  {taskPending.length > 1 && ` · ${taskPending.length}`}
                </span>
              )}
              <span className="mx-3 h-3 w-px bg-[#f3f4f6]" />
              <span className="inline-flex items-center gap-1.5 text-[#898f9c]">
                <span className="inline-flex h-4 shrink-0 items-center rounded-[4px] bg-brand-light px-1 text-[10px] font-normal leading-none text-brand-700">
                  Lead
                </span>
                {leadAgentName ?? task.lead_agent_slug}
              </span>
              {tokenUsage && (
                <>
                  <span className="mx-3 h-3 w-px bg-surface-border" />
                  <TaskTokenUsagePopover usage={tokenUsage} />
                </>
              )}
            </div>
          </div>
        </div>

        {/* Goal card — shown only when it adds something beyond the title: a
          goal distinct from the title, or staged attachments. A prompt-launched
          task (title === goal) would otherwise repeat the heading verbatim. */}
        {(goalDiffersFromTitle || kickoffAttachments.length > 0) && (
          <section className="mt-4 w-full rounded-lg border border-surface-border bg-[#f7f7f8] px-4 py-3">
            {goalDiffersFromTitle && (
              <ClampText
                text={task.goal}
                t={t}
                className="text-[12px] leading-5 text-[#131313]"
              />
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
                      const absolute = toAbsoluteProjectPath(path, rootPath);
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
                                    "task.artifactBy" as Parameters<
                                      typeof t
                                    >[0],
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
                  open
                  className={cn(
                    "group/d overflow-hidden bg-white",
                    completionInfo.artifacts.length > 0 &&
                      "border-t border-[#f3f4f6]",
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
          // Natural height (NOT ``flex-1``): the deliverable card + the
          // expanded completion summary can already fill the reading column, so
          // a ``flex-1`` follow-up would get 0 leftover and collapse the turns to
          // an invisible 0-height scroll box. Sizing to content lets the turns
          // render and the page scroll instead.
          <section className="mt-6 flex w-full flex-col">
            <div className="mb-3 flex shrink-0 items-center gap-2 text-[12px] font-medium text-ink-heading">
              <span className="h-px flex-1 bg-surface-border" />
              {t("task.followUp.heading")}
              <span className="h-px flex-1 bg-surface-border" />
            </div>
            {/* Turns render at natural height, capped so a long conversation
              scrolls within the box instead of pushing the composer off-screen.
              (Empty when there are no follow-up turns — the gate below.) */}
            <div
              ref={followUpScrollRef}
              className="max-h-[55vh] overflow-y-auto"
            >
              {followUp.turns.length > 0 && (
                <ConversationTurnList
                  turns={followUp.turns}
                  scrollContainerRef={followUpScrollRef}
                  sending={followUp.sending}
                  loading={false}
                  error={null}
                  renderToolCall={renderFollowUpToolCall}
                  startingRuntime={followUpStartingRuntime}
                />
              )}
            </div>
            {/* Minimal composer. The full ``@valuz/ui`` ``Composer`` is
              model/runtime/agent-aware (skills, attachments, model picker) —
              none of which applies to a lead follow-up where runtime + model are
              already locked to the session. A plain textarea + send/stop button
              keeps the surface honest: no dead selectors, no fake "Model" pill.
              Enter sends; Shift+Enter inserts a newline. */}
            <div className="mt-3 shrink-0 rounded-md border border-surface-border bg-surface">
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
                // ``disabled:bg-transparent`` overrides the Textarea's default
                // ``disabled:bg-surface-muted`` so the disabled (in-flight) state
                // keeps the wrapper's background instead of painting a mismatched
                // grey box over just the textarea.
                className="resize-none border-0 bg-transparent focus-visible:ring-0 focus-visible:border-transparent disabled:bg-transparent"
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
            {/* Bottom gap matching the conversation composer's ``pb-4``. A real
              box (not a margin) so the scroll container counts it — a trailing
              bottom margin is dropped from ``scrollHeight`` and the composer
              ends up flush with the panel's bottom border. */}
            <div aria-hidden className="h-4 shrink-0" />
          </section>
        )}

        {/* Failed / blocked / stopped → failure card (red). Pulls the most
          recent failure event's error or reason. ``blocked`` is the status a
          lead crash actually lands in — omitting it left those pages with a
          bare retry button and no explanation. */}
        {(task.status === "failed" ||
          task.status === "blocked" ||
          task.status === "stopped") &&
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
        {!isCompleted && (
          <section className="mt-5 w-full">{timelineBody}</section>
        )}
      </div>
      {/* /Reading column ---------------------------------------- */}

      {/* Sticky action bar — only shown while the task is still
          ``in-flight`` (active or paused). Completed / failed have no
          actionable next step on this page; the result is read-only by
          design — users continue work by opening a fresh task or chat
          from the project home. Blocked and stopped get their own
          resume bar below (the backend accepts stopped→active). Hiding
          the bar entirely keeps the page distraction-free at rest.

          The bar carries three controls only — modify goal, the
          status-conditional pause/resume toggle, and stop. We
          deliberately dropped the v30 trio (加备注 / Retry / 继续对话):
          notes weren't being read by the lead, Retry was a kernel-
          pending placeholder, and "继续对话" routed into the lead's
          internal session which is the wrong abstraction for the
          user. See PR discussion for the full reasoning. */}
      {isActive && (
        <div className="sticky bottom-0 -mx-5 mt-auto overflow-hidden px-5 py-3">
          <div className="absolute inset-0 bg-card/94 backdrop-blur-3xl" />
          <div className="relative z-10 mx-auto flex w-full max-w-[760px] flex-wrap items-center justify-center gap-2 px-6">
            <Button
              size="sm"
              variant="outline"
              className="text-xs"
              onClick={() => {
                setReviseGoal(task.goal);
                setReviseOpen(true);
              }}
              disabled={busy}
            >
              {t("task.reviseGoal")}
            </Button>
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
            {/* Stop is destructive AND the primary intent while active (the
                user is interrupting an in-flight task) — right edge. */}
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

      {/* Halted (paused / blocked / stopped / legacy failed) → one unified
          "talk to resume" surface. The composer text (optional) rides along
          with the resume intervene call and lands in the respawned lead's
          recovery brief — so "回复并恢复" is one step, not resume-then-race-
          the-mailbox. Blocked reads as "retry", the rest as "resume". */}
      {isHalted && (
        <div className="sticky bottom-0 -mx-5 mt-auto overflow-hidden px-5 py-3">
          <div className="absolute inset-0 bg-card/94 backdrop-blur-3xl" />
          <div className="relative z-10 mx-auto w-full max-w-[760px] px-6">
            <div className="rounded-md border border-surface-border bg-surface">
              <Textarea
                value={resumeDraft}
                onChange={(e) => setResumeDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    void runIntervene(
                      {
                        action: "resume",
                        ...(resumeDraft.trim()
                          ? { text: resumeDraft.trim() }
                          : {}),
                      },
                      "task.resumed",
                    ).then((ok) => {
                      if (ok) setResumeDraft("");
                    });
                  }
                }}
                placeholder={t(
                  "task.resumePlaceholder" as Parameters<typeof t>[0],
                )}
                aria-label={t(
                  "task.resumePlaceholder" as Parameters<typeof t>[0],
                )}
                rows={2}
                disabled={busy}
                className="resize-none border-0 bg-transparent focus-visible:ring-0 focus-visible:border-transparent disabled:bg-transparent"
              />
              <div className="flex items-center justify-between gap-2 px-2 pb-2">
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    className="text-xs"
                    onClick={() => {
                      setReviseGoal(task.goal);
                      setReviseOpen(true);
                    }}
                    disabled={busy}
                  >
                    {t("task.reviseGoal")}
                  </Button>
                  {/* stopped→stopped and legacy-failed→stopped are illegal
                      transitions — only paused/blocked can still be stopped. */}
                  {(isPaused || isBlocked) && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="text-xs text-error-text hover:text-error-text"
                      onClick={() =>
                        void runIntervene({ action: "stop" }, "task.stopped")
                      }
                      disabled={busy}
                    >
                      {t("task.stop")}
                    </Button>
                  )}
                </div>
                <Button
                  size="sm"
                  className="text-xs"
                  onClick={() =>
                    void runIntervene(
                      {
                        action: "resume",
                        ...(resumeDraft.trim()
                          ? { text: resumeDraft.trim() }
                          : {}),
                      },
                      "task.resumed",
                    ).then((ok) => {
                      if (ok) setResumeDraft("");
                    })
                  }
                  disabled={busy}
                  loading={busy}
                >
                  {resumeDraft.trim()
                    ? t("task.resumeWithInstruction" as Parameters<typeof t>[0])
                    : t(isBlocked || isFailedLegacy ? "task.retry" : "task.resume")}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* A draft is neither active nor halted, so neither bar above renders —
          the page used to be a dead end for the two actions the backend does
          expose (:commit / :abandon), which only the chat card offered. */}
      {task.status === "draft" && (
        <div className="sticky bottom-0 -mx-5 mt-auto overflow-hidden px-5 py-3">
          <div className="absolute inset-0 bg-card/94 backdrop-blur-3xl" />
          <div className="relative z-10 mx-auto flex w-full max-w-[760px] items-center justify-end gap-2 px-6">
            <button
              type="button"
              disabled={draftBusy !== null}
              onClick={() => void runDraftAction("abandon")}
              className="rounded-md border border-surface-border bg-surface px-3 py-1.5 text-xs font-medium text-ink-body transition-colors hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {draftBusy === "abandon"
                ? t("common.processing")
                : t("conversation.taskAbandon")}
            </button>
            <button
              type="button"
              disabled={draftBusy !== null}
              onClick={() => void runDraftAction("commit")}
              className="rounded-md bg-brand px-3.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {draftBusy === "commit"
                ? t("common.processing")
                : t("conversation.taskExecute")}
            </button>
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
      </div>
    </ArtifactSplitPane>
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
 *    ``members`` to get the display name
 *
 *  Member-attributed events (subtask_spawned / _completed / _failed) now carry
 *  the resolved name in ``payload.agent_name``, stamped at emit time by the
 *  backend. Prefer it: it's durable (survives the member being un-deployed /
 *  renamed) and needs no members list, so it doesn't race the async members
 *  fetch — the root cause of the intermittent "成员智能体名称查询不到". The
 *  members join + slug remain as fallbacks for events emitted before this
 *  landed. */
function resolveActor(
  evt: TaskEvent,
  members: MemberWithAgent[],
  leadAgentName: string | null,
  leadAgentSlug: string,
  t: Translator,
): string {
  const { actor, type } = evt;
  if (actor === "user") return t("task.actorYou");
  // Chat-plan events carry the ORIGINATING CHAT session id as actor (the
  // draft/commit came from the user's conversation) — show "你", never the
  // raw session UUID.
  if (type === "task_drafted" || type === "committed") {
    return t("task.actorYou");
  }
  // Historical chat-created kickoffs carry the raw chat session UUID as actor
  // (the create_task tool used to pass its session id as ``created_by``; new
  // rows carry "user"). The log is append-only, so those rows are permanent —
  // collapse them to "你" instead of rendering bare hex. Gated on the id shape
  // so an "automation" kickoff keeps its own label.
  if (type === "kickoff" && /^[0-9a-f]{32}$/.test(actor)) {
    return t("task.actorYou");
  }
  // Lead-driven events carry the lead SESSION id as actor — collapse to the
  // lead agent name (VALUZ-TASK adds plan/review events on this path).
  if (
    type === "task_completed" ||
    type === "task_failed" ||
    // ``task_blocked`` (lead turn errored / left unresolved subtasks) and
    // ``task_stopped`` (finish_task with stopped status) both carry the lead
    // SESSION id as actor — same lead-decision path as task_completed. Without
    // them here resolveActor falls through and renders the raw session UUID
    // instead of the lead's role name (the "kickoff 失败时展示 id" bug).
    type === "task_blocked" ||
    type === "task_stopped" ||
    type === "kickoff_failed" ||
    type === "task_planned" ||
    type === "plan_revised" ||
    type === "subtask_reviewed" ||
    // Host-decided halts: ``paused`` from an auto-finalize kickoff-cancel and
    // ``abandoned`` from a discarded draft both stamp the session id.
    type === "paused" ||
    type === "abandoned"
  ) {
    return leadAgentName ?? leadAgentSlug;
  }
  // A chat inject (delivered or dropped) carries the ORIGINATING chat session
  // id — that is the user talking, not an agent.
  if (type === "user_inject" || type === "user_inject_dropped") {
    return t("task.actorYou");
  }
  // Belt-and-braces for every remaining type: a bare 32-hex actor is a session
  // id, never a name. Rendering it raw is the "看到一串 id" bug; the log is
  // append-only so old rows keep arriving this way forever.
  if (/^[0-9a-f]{32}$/.test(actor)) {
    return leadAgentName ?? leadAgentSlug;
  }
  const payloadName = evt.payload?.agent_name;
  if (typeof payloadName === "string" && payloadName) return payloadName;
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

/** Long free-text (task goal, timeline event detail) clamped to ``maxLines``
 *  visual lines with a trailing 展开/收起 toggle on its own last line.
 *  ``whitespace-pre-wrap`` is kept in BOTH states so the author's line breaks
 *  survive the collapse; ``overflow:hidden`` preserves ``scrollHeight`` so the
 *  full height can be measured to decide whether a toggle is needed at all. */
function ClampText({
  text,
  t,
  className,
  maxLines = 20,
}: {
  text: string;
  t: Translator;
  className?: string;
  maxLines?: number;
}) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [maxH, setMaxH] = useState<number | null>(null);
  const [overflowing, setOverflowing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const cs = getComputedStyle(el);
    let lh = parseFloat(cs.lineHeight);
    if (!Number.isFinite(lh)) lh = parseFloat(cs.fontSize) * 1.5;
    const h = lh * maxLines;
    setMaxH(h);
    setOverflowing(el.scrollHeight > h + 1);
  }, [text, maxLines]);
  const clamped = overflowing && !expanded;
  return (
    <>
      <p
        ref={ref}
        className={cn("whitespace-pre-wrap", className)}
        style={
          clamped && maxH != null
            ? { maxHeight: maxH, overflow: "hidden" }
            : undefined
        }
      >
        {text}
      </p>
      {overflowing && (
        <div className="mt-0.5 flex justify-end">
          <button
            type="button"
            // stopPropagation: these live inside click-through rows (EventBody →
            // onOpenSession) — toggling must not also open the session.
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
            className="text-[11px] font-medium text-brand transition-colors hover:text-brand/80"
          >
            {t(
              (expanded ? "common.collapse" : "common.expand") as Parameters<
                typeof t
              >[0],
            )}
          </button>
        </div>
      )}
    </>
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
  leadSessionId,
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
  /** The Lead's own session — the link target for lead-decision events like
   *  ``subtask_reviewed`` (whose ``evt.session_id`` is the reviewee, not the
   *  Lead). ``null`` when the Lead run isn't resolved yet. */
  leadSessionId?: string | null;
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
    evt,
    members,
    leadAgentName,
    leadAgentSlug,
    t,
  );
  // "查看会话" jumps to a session for a read-only trace view.
  //  - ``subtask_reviewed`` is a LEAD decision: its ``evt.session_id`` is the
  //    REVIEWEE's sub-Run, which is the wrong place to land from "✓ 审核通过".
  //    Point it at the Lead's own session instead — same target + style as the
  //    Lead's other timeline rows.
  //  - ``task_planned`` / ``plan_revised`` — the right rail's 任务列表 already
  //    shows the current plan snapshot live, so a session jump here is
  //    redundant; the row stays as a historical marker but offers no link.
  // Everything else with a session_id stays linkable to that session.
  const linkTarget =
    evt.type === "subtask_reviewed" ? (leadSessionId ?? null) : evt.session_id;
  const nonLinkableTypes = new Set(["task_planned", "plan_revised"]);
  const linkSuppressed = hideSessionLink || nonLinkableTypes.has(evt.type);
  const clickable = !!linkTarget && !linkSuppressed;
  return (
    <div
      className={`${pad} ${
        clickable
          ? "-mt-1 -ml-1 min-w-0 flex-1 cursor-pointer rounded-md px-3 py-2 transition-colors group-hover:bg-[#f7f7f8]"
          : "-mt-1 -ml-1 min-w-0 flex-1 rounded-md px-3 py-2 transition-colors group-hover:bg-[#f7f7f8]"
      } ${compact ? "flex-1" : ""}`}
      onClick={
        clickable ? () => onOpenSession(linkTarget as string) : undefined
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
        <ClampText
          text={detail}
          t={t}
          className="mt-1 text-[12px] leading-5 text-ink-body"
        />
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
  taskStatus,
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
  taskStatus: string;
  t: Translator;
  onOpenSession: (sid: string) => void;
}) {
  const spawnDetail = eventDetail(spawn, t);
  const spawnActor = resolveActor(
    spawn,
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
    : // No outcome AND the task is halted: those members are gone, so
      // "等待回执" would promise a receipt that can never arrive.
      taskStatus === "active"
      ? t("task.subtaskWaiting" as Parameters<typeof t>[0])
      : t("task.subtaskHalted" as Parameters<typeof t>[0]);
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
        <ClampText
          text={spawnDetail}
          t={t}
          className="mt-1 text-[12px] leading-5 text-ink-body"
        />
      )}
      <div
        className={cn(
          "mt-2 inline-flex h-5 items-center rounded-[4px] px-2 py-0 text-[10px] leading-4",
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
