import { useRef, useState, useEffect } from "react";
import {
  ChevronRight,
  Search,
  FileText,
  FolderTree,
  Bot,
  Check,
  CheckSquare,
  Circle,
  Clock,
  Database,
  Folder,
  FolderOpen,
  Loader2,
  Paperclip,
  Trash2,
  Unlink,
  Crown,
  X,
  Plus,
  MoreHorizontal,
  FilePenLine,
  Pause,
  Play,
  Power,
  Zap,
  RefreshCw,
  Upload,
  PanelRightOpen,
  AlertTriangle,
  GitBranch,
  Link2,
  LogIn,
  MessageSquare,
  MessageSquarePlus,
  Sparkles,
} from "lucide-react";
import { modelLabel } from "@valuz/shared";
import {
  InstructionsEditor,
  type InstructionsEditorHandle,
} from "./InstructionsEditor";
import { ProjectFileTree, type FileTreeNode } from "./ProjectFileTree";
import { ConnectorPickerDialog } from "./ConnectorPickerDialog";
import { cn } from "../../lib/cn";
import { getFileTypeIcon } from "../../lib/file-type-icons";
import { useI18n } from "../../hooks/use-i18n";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";
import { Checkbox } from "../ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

/**
 * Total file count across the whole tree (recursive into folders),
 * not just the top-level entries. Used as the right-aligned count
 * badge on the file-tree accordion title — folders themselves don't
 * count, only leaves.
 */
const countFiles = (nodes: FileTreeNode[]): number =>
  nodes.reduce(
    (sum, n) => sum + (n.type === "file" ? 1 : countFiles(n.children ?? [])),
    0,
  );

function FileTypeIcon({ filename }: { filename: string }) {
  const Icon = getFileTypeIcon(filename);

  return (
    <span
      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[8px] bg-surface-muted"
      data-testid="uploaded-file-type-icon"
    >
      <Icon aria-hidden="true" className="h-3.5 w-3.5 text-ink-muted" />
    </span>
  );
}

/* ── Types ────────────────────────────────────────────────────── */

export interface ProjectSkill {
  id: string;
  name: string;
  description?: string;
  source: "user" | "project";
  enabled?: boolean;
  is_locked?: boolean;
  lock_reason?: string | null;
}

export interface ProjectDoc {
  id: string;
  name: string;
  status: "ready" | "indexing" | "failed";
  referenced: boolean;
  chunks?: number;
}

export interface KbBindingTreeNode {
  id: string;
  name: string;
  kind: "kb" | "folder" | "document";
  status?: string;
  documentCount?: number;
  children?: KbBindingTreeNode[];
  childrenLoaded?: boolean;
}

export interface KbBindingSelection {
  binding_kind: "kb" | "folder" | "document";
  target_id: string;
}

export interface ScheduledTaskSummary {
  id: string;
  name: string;
  cron: string;
  humanReadable: string;
  status: "on" | "off";
  nextRun: string;
}

/** One project worktree row for the panel's "Worktrees" section. Mirrors the
 *  backend's computed-on-read WorktreeItem: `null` counts mean the state
 *  could not be verified (anchor lost / git failure) — rendered "unknown". */
export interface WorktreeSummary {
  name: string;
  branch: string | null;
  origin: string;
  dirtyFiles: number | null;
  aheadCommits: number | null;
}

/** A project member agent for the config panel's "Member agents" section. */
export interface ProjectMemberItem {
  id: string;
  name: string;
  slug: string;
  /** Global library agent slug this member was deployed from. Passed to
   *  ``onOpenMember`` so the host can show the SHARED agent's detail (not
   *  the project-local member). Falls back to ``slug`` when unknown
   *  (e.g. legacy rows). */
  sourceAgentSlug?: string | null;
  /** Effective model id (falls back to the agent slug when unknown). */
  model?: string | null;
  /** Runtime id (e.g. ``claude_agent``) for the composer's agent selector. */
  runtime?: string | null;
  /** Human-facing runtime label (e.g. ``Claude Code``) for display rows.
   *  Caller computes this via ``RUNTIME_DISPLAY_NAME`` so this UI stays
   *  decoupled from ``@valuz/core`` (only allowed deps: ``@valuz/shared``). */
  runtimeLabel?: string;
  /** True when the underlying library agent was removed — render a
   *  "removed from the agent library" placeholder with only the remove (undeploy) action. */
  orphan?: boolean;
}

export interface UploadedFileItem {
  id: string;
  name: string;
  /** Optional human-readable byte size, e.g. "1.2 MB". */
  size?: string;
  /** Parser/upload status for the row badge. */
  status?: "uploaded" | "ok" | "failed";
  /** Async parse status of the attachment. ``parsing`` renders an inline
   *  spinner + "parsing"; ``failed`` renders the error badge; ``native`` is an
   *  image with no local text extract that the runtime reads directly (no
   *  error). Distinct from ``status`` so the live poll can drive the indicator
   *  without disturbing legacy callers. */
  parseStatus?: "parsing" | "ready" | "failed" | "native";
  /** Origin of the attachment: ``local`` (multipart upload) vs
   * ``kb_doc`` (live reference to a global knowledge-base document).
   * Drives the row icon — KB picks render a ``Database`` glyph so
   * users can tell the two paths apart at a glance. Defaults to
   * ``local`` for back-compat with callers that haven't been
   * updated yet (e.g. before the kb-attachment flow shipped). */
  sourceKind?: "local" | "kb_doc";
}

/**
 * One agent-delivered deliverable — the "生成文件" list, recorded by the
 * built-in ``deliver_artifacts`` MCP tool. The inverse of
 * {@link UploadedFileItem} (user uploads): these are files the agent produced
 * and explicitly marked as outputs. Read-only; the row click opens the file.
 */
export interface GeneratedArtifactItem {
  id: string;
  name: string;
  /** Optional human-readable byte size, e.g. "1.2 MB". */
  size?: string;
  /**
   * Absolute path the row opens via ``onOpenGeneratedFile``. Points at this
   * version's immutable snapshot, so it keeps working after the agent edits
   * its working copy.
   */
  path: string;
  /**
   * 1-based version of the deliverable, when the backend reports one. Shown as
   * "v2" from the second version on — a lone "v1" would be noise on every row.
   */
  versionNo?: number;
  /**
   * Whether this is still the deliverable's latest version. ``false`` means a
   * later turn or another session superseded it; the row dims so a stale
   * version is not mistaken for the current deliverable.
   */
  isCurrent?: boolean;
  /**
   * Stable id of the deliverable this is a version of. Required to offer the
   * version history — without it the row is just a file, as before.
   */
  artifactId?: string;
}

/**
 * One entry in a deliverable's history, oldest first.
 *
 * Loaded on demand: a session panel can list many deliverables and almost none
 * of their histories get opened, so fetching every one up front would be
 * traffic spent on nothing.
 */
export interface GeneratedArtifactVersion {
  id: string;
  versionNo: number;
  /** Absolute path of THIS version's snapshot. Empty when its bytes are gone. */
  path: string;
  /** Human-readable size, e.g. "1.2 MB". */
  size?: string;
  /** Human-readable timestamp for the row's right edge. */
  when?: string;
  /**
   * False when the version was recorded but its bytes are no longer on disk —
   * a removed worktree, most often. Shown, but not offered as openable: the
   * deliverable did have this generation, and hiding it would misrepresent the
   * history.
   */
  openable: boolean;
}

/**
 * One entry in the agent's TODO list snapshot. Matches the kernel
 * ``Session.todos`` element shape (which mirrors the Claude Agent SDK's
 * ``TodoWrite`` payload verbatim — camelCase ``activeForm`` is the wire
 * name).
 */
export interface TodoListItem {
  content: string;
  /** "pending" | "in_progress" | "completed" — typed loosely for
   * forward-compat with future SDK additions. */
  status: string;
  /** Gerund form rendered while ``in_progress``. Falls back to
   * ``content`` when omitted (some runtimes don't emit it). */
  activeForm?: string | null;
}

export interface ProjectContextPanelProps {
  width?: number;
  /** Panel header title (spec 5.8: 15px / 500 / #131313). */
  title?: string;
  /** Optional section label overrides for project-specific wording. */
  instructionsTitle?: string;
  scheduledTasksTitle?: string;
  /** Hide the session TODO section for project home panels. */
  showTodos?: boolean;
  /**
   * Project Instructions markdown. ``undefined`` hides the section entirely
   * (used by the chat-default project, which has no project-level prompt).
   */
  instructions?: string;
  onInstructionsChange?: (value: string) => void;
  /**
   * Project member agents (PRD-NEXT §3.4). ``undefined`` hides the section;
   * an empty array shows the section with an empty-state nudge.
   */
  members?: ProjectMemberItem[];
  onAddMember?: () => void;
  /** Open a member's shared agent (live-reference deployment: editing is global —
   *  the host navigates to the agent detail page). */
  onOpenMember?: (slug: string) => void;
  /** Remove a member from the project (undeploy). The host is expected to confirm. */
  onRemoveMember?: (slug: string) => void;
  /** Member slug that leads tasks when none is named; null/undefined = unset. */
  defaultLeadSlug?: string | null;
  /** Set (or clear, with null) the project's default task lead. */
  onSetDefaultLead?: (slug: string | null) => void;
  /** IM groups bound to this project ("this group is that project"). */
  chatBindings?: {
    id: string;
    name: string;
    platformLabel?: string;
    /** Valuz created it, so the bot owns it and may dissolve it. */
    createdByValuz?: boolean;
    /** …and nobody has joined yet, so a join link is the only way in. */
    needsJoin?: boolean;
  }[];
  /** Open the group picker; undefined hides the section entirely. */
  onBindChat?: () => void;
  onUnbindChat?: (externalChatId: string) => void;
  onJoinChat?: (externalChatId: string) => void;
  onDeleteChat?: (externalChatId: string) => void;
  skills?: ProjectSkill[];
  onAddSkill?: () => void;
  onCreateProjectSkill?: () => void;
  onManageGlobalSkills?: () => void;
  onToggleSkill?: (skillId: string) => void;
  onRemoveSkill?: (skillId: string) => void;
  mcpServers?: {
    slug: string;
    display_name: string;
    description: string | null;
    enabled: boolean;
  }[];
  onToggleMcpServer?: (slug: string, enabled: boolean) => void;
  docs?: ProjectDoc[];
  onToggleDoc?: (docId: string) => void;
  onImportFile?: () => void;
  onDeleteDoc?: (docId: string) => void;
  onManageGlobalDocs?: () => void;
  kbTree?: KbBindingTreeNode[];
  bindings?: KbBindingSelection[];
  onToggleBinding?: (
    kind: "kb" | "folder" | "document",
    targetId: string,
  ) => void;
  onExpandKbFolder?: (kbId: string, folderId: string) => Promise<void>;
  /**
   * Every deliverable the project holds, at its current version — regardless of
   * which conversation produced it. Distinct from ``generatedFiles``, which is
   * one session's output: a project view has no session to scope to, and a
   * session that delivered nothing would otherwise show nothing at all while
   * the workspace is full of deliverables.
   *
   * Pass ``undefined`` to hide the section.
   */
  projectArtifacts?: GeneratedArtifactItem[];
  /**
   * Fetch a deliverable's version history. Provide it to make the version badge
   * expandable; omit it and rows stay plain files. Called at most once per
   * artifact — the panel caches what it gets back.
   */
  onLoadArtifactVersions?: (
    artifactId: string,
  ) => Promise<GeneratedArtifactVersion[]>;
  /** Remove a whole KB from the project (the ``×`` on a KB header row). */
  onRemoveKb?: (kbId: string) => void;
  /** Reset a KB back to "whole knowledge base in scope" (the ``select-all``
   *  affordance shown on a narrowed KB's header row). */
  onSelectAllInKb?: (kbId: string) => void;
  /**
   * Files the user uploaded against the current session. Always shown when
   * the array is provided (even if empty — empty state nudges the user to
   * upload). Pass ``undefined`` to hide the section entirely.
   */
  uploadedFiles?: UploadedFileItem[];
  onUploadFile?: () => void;
  onRemoveUploadedFile?: (id: string) => void;
  /**
   * Files the agent delivered as finished outputs (the "生成文件" list,
   * recorded by the ``deliver_artifacts`` tool). Provide the array (even
   * empty) to render the section; ``undefined`` hides it. Read-only — rows
   * open via {@link onOpenGeneratedFile}.
   */
  generatedFiles?: GeneratedArtifactItem[];
  /** Open one delivered artifact (its absolute path) in the OS. */
  onOpenGeneratedFile?: (path: string) => void;
  /**
   * Latest TODO snapshot from the agent (kernel V5+messages emits this
   * via ``todo_update`` events whenever the agent calls TodoWrite). The
   * section auto-hides when this is ``undefined``, ``null``, or empty —
   * the agent only produces a TODO list when it decides to plan, so an
   * empty panel section would just be noise.
   */
  todos?: TodoListItem[] | null;
  /**
   * Project git worktrees (isolated branch copies sessions can run in).
   * Pass the list (even empty) to show the section; ``undefined`` hides it
   * (non-git projects, or callers that don't surface worktrees). Rows are
   * computed on read by the backend — git is the source of truth.
   */
  worktrees?: WorktreeSummary[];
  /** Start a new conversation in this worktree (wired to the row action). */
  onContinueWorktree?: (name: string) => void;
  /** Discard (delete) a worktree — caller owns the confirm dialog. */
  onDiscardWorktree?: (worktree: WorktreeSummary) => void;
  scheduledTasks?: ScheduledTaskSummary[];
  onAddScheduledTask?: () => void;
  /** Open a scheduled task's detail page — wired to the row click. */
  onOpenScheduledTask?: (taskId: string) => void;
  /** Open the edit dialog for a scheduled task — wired to the row's "Edit"
   *  menu item (no longer the row click, which now opens the detail page). */
  onEditScheduledTask?: (taskId: string) => void;
  onToggleScheduledTask?: (taskId: string, nextStatus: "on" | "off") => void;
  onDeleteScheduledTask?: (taskId: string) => void;
  onRunScheduledTask?: (taskId: string) => void;
  onManageScheduledTasks?: () => void;
  fileTree?: FileTreeNode[];
  /** Section title for the file-tree accordion. Project projects use
   * "{t("project.projectFiles")}"; chat projects use "generated files" — the underlying tree
   * is the same component, only the label changes per context. */
  fileTreeTitle?: string;
  /** Render the file tree as a top-level tab instead of an accordion. This is
   * opt-in so conversation side panels keep their original layout. */
  fileTreeInTab?: boolean;
  /** Project-scope memory entries (auto-curated). ``undefined`` hides the
   * project-memory tab entirely — pass it only on the real-project home panel. */
  projectMemory?: string[];
  /** Delete one memory entry (located by its exact text). */
  onMemoryDeleteEntry?: (text: string) => void;
  /** Clear all project memory. */
  onMemoryClear?: () => void;
  /** Hide project-level edit/manage affordances for read-only conversation
   * context panels while keeping them available on the project home panel. */
  hideProjectContextActions?: boolean;
  rootPath?: string;
  onFileClick?: (path: string) => void;
  onFileDoubleClick?: (path: string) => void;
  onOpenInFinder?: () => void;
  /** Manual refresh trigger for the file-tree section. Auto-refresh
   * fires on turn-end too — this is the user-side bail-out for cases
   * the page can't predict (mid-turn writes the user wants to peek
   * at, or background changes from outside the conversation).
   * The button manages its own spin animation locally; the page
   * doesn't need to thread a loading flag back through. */
  onRefreshFiles?: () => void;
  /** Upload files into the project cwd (multipart). Wired only when the
   *  page can accept uploads — i.e. the backend hosting the project is
   *  reachable over HTTP. The button owns its own hidden
   *  ``<input type=file multiple>``; the page gets the picked files and
   *  is responsible for the actual ``projectsApi.uploadFiles`` call,
   *  the success toast, and re-listing the tree. */
  onUploadFiles?: (files: File[]) => void;
  onOpenInSystem?: (path: string) => void;
  onDeleteFile?: (path: string) => void;
  /** Initial accordion section. ``null`` starts every section collapsed. */
  initialOpenSection?: string | null;
  /** When ``true``, every section is independently toggleable and starts
   *  expanded by default — closer to a settings list than a single-pane
   *  accordion. Project home uses this so the user can see Project
   *  README / Agent Team / Docs at a glance without clicking. ``false``
   *  (default) keeps the exclusive single-open behaviour the chat
   *  project rail relies on. */
  multiOpen?: boolean;
  /** Optional controlled collapsed state. When provided, the panel uses this
   * value instead of its internal state — useful when an external chrome
   * (TopBar toggle) drives panel visibility. */
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
}

/* ── KB binding tree row ─────────────────────────────────────── */

function KbTreeRow({
  node,
  depth,
  expanded,
  onToggle,
  onExpand,
  isDirectlyBound,
  isCoveredByParent,
  onToggleBinding,
  onRemoveKb,
  onSelectAllInKb,
}: {
  node: KbBindingTreeNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onExpand: (id: string) => void;
  /** Returns true when the project binding table has an exact row for
   * ``(node.kind, node.id)``. Drives the checked-state visual. */
  isDirectlyBound?: (kind: "kb" | "folder" | "document", id: string) => boolean;
  /** Returns true when an ancestor row is bound (and therefore implicitly
   * covers this node). Rendered as a disabled / dimmed checkbox so the
   * user knows "this is already in scope via the parent — toggle the
   * parent if you want to change it". */
  isCoveredByParent?: (
    kind: "kb" | "folder" | "document",
    id: string,
  ) => boolean;
  onToggleBinding?: (kind: "kb" | "folder" | "document", id: string) => void;
  /** Remove this KB from the project — only wired / meaningful on a
   * ``kb`` node. When omitted the KB header shows no ``×``. */
  onRemoveKb?: (kbId: string) => void;
  /** Reset this KB to whole-KB scope — only shown on a ``kb`` node that
   * is currently narrowed (has folder / document bindings instead of a
   * top-level ``kb`` binding). */
  onSelectAllInKb?: (kbId: string) => void;
}) {
  const { t } = useI18n();
  const isExpanded = expanded.has(node.id);
  const isMissing = node.status === "missing";
  const isKbOrFolder = node.kind === "kb" || node.kind === "folder";

  // Tri-state binding indicator. ``direct`` means the project has a
  // row for this exact (kind,id); ``covered`` means an ancestor row
  // implicitly includes it. We render the same checkbox for both but
  // ``covered`` is half-opacity and click is a no-op — toggling the
  // implicit parent is the right affordance. ``showBindingState`` gates
  // whether the checkbox renders at all: a caller that only needs a
  // navigation tree (no binding lookups wired) gets a plain tree.
  const showBindingState =
    isDirectlyBound !== undefined || isCoveredByParent !== undefined;
  const direct = isDirectlyBound?.(node.kind, node.id) ?? false;
  const covered = !direct && (isCoveredByParent?.(node.kind, node.id) ?? false);
  const checkboxClass = cn(
    "h-3 w-3 shrink-0 rounded-[3px] border transition-colors",
    direct
      ? "border-brand bg-brand text-white"
      : covered
        ? "border-brand/40 bg-brand/20"
        : "border-surface-border-hover hover:border-brand/50",
  );

  return (
    <>
      <div
        className={cn(
          "group flex items-center gap-1.5 rounded-md py-1.5 pr-2 text-xs transition-colors hover:bg-surface-muted/60",
          isMissing && "opacity-55",
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {/* expand arrow */}
        {isKbOrFolder ? (
          <button
            type="button"
            onClick={() => {
              onToggle(node.id);
              if (!isExpanded && !node.childrenLoaded) onExpand(node.id);
            }}
            className="flex h-4 w-4 shrink-0 items-center justify-center"
          >
            <ChevronRight
              className={cn(
                "h-2.5 w-2.5 text-ink-muted transition-transform",
                isExpanded && "rotate-90",
              )}
            />
          </button>
        ) : (
          <span className="w-4 shrink-0" />
        )}

        {/* binding checkbox — three states (direct / covered-by-parent
            / unbound). Rendered whenever the caller supplies the
            binding-state lookups, so a session panel can show the
            current selection read-only. It's only *interactive* when
            ``onToggleBinding`` is also wired (the project-detail
            page); inside a session the checkbox is a plain indicator
            — per product rule, KB binding edits happen on the
            project page and take effect for the next session. */}
        {showBindingState && node.kind !== "kb" ? (
          onToggleBinding ? (
            <button
              type="button"
              disabled={isMissing}
              onClick={() => {
                if (isMissing) return;
                // A ``covered`` node is in scope via a parent (the KB
                // or an ancestor folder). Clicking it is still valid —
                // the hook "explodes" the parent into per-sibling
                // bindings and drops this one, which is how the user
                // narrows a whole-KB / whole-folder selection down to
                // specific files.
                onToggleBinding(node.kind, node.id);
              }}
              className="flex items-center justify-center"
              title={direct || covered ? "Unbind this node" : "Bind this node"}
            >
              <span className={checkboxClass}>
                {direct ? (
                  <Check className="h-2.5 w-2.5" strokeWidth={3} />
                ) : null}
              </span>
            </button>
          ) : (
            <span
              className="flex items-center justify-center"
              title={
                direct
                  ? "Bound to this project"
                  : covered
                    ? "Covered by a parent binding"
                    : "Not bound"
              }
            >
              <span className={checkboxClass}>
                {direct ? (
                  <Check className="h-2.5 w-2.5" strokeWidth={3} />
                ) : null}
              </span>
            </span>
          )
        ) : null}

        {/* icon */}
        {node.kind === "kb" ? (
          <Database className="h-3 w-3 shrink-0 text-ink-muted" />
        ) : node.kind === "folder" ? (
          isExpanded ? (
            <FolderOpen className="h-3 w-3 shrink-0 text-ink-muted" />
          ) : (
            <Folder className="h-3 w-3 shrink-0 text-ink-muted" />
          )
        ) : (
          <FileText className="h-3 w-3 shrink-0 text-ink-muted" />
        )}

        {/* label */}
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-ink-label",
            node.kind === "kb" && "font-medium",
          )}
        >
          {node.name}
        </span>

        {/* meta */}
        {isMissing && (
          <AlertTriangle className="h-2.5 w-2.5 shrink-0 text-warning-text" />
        )}
        {node.kind !== "document" && node.documentCount != null && (
          <span className="shrink-0 text-2xs text-ink-meta">
            {node.documentCount}
          </span>
        )}
        {/* KB header actions — the KB row is a membership marker, not a
            selectable checkbox. ``全选`` resets a narrowed KB back to
            whole-KB scope; ``×`` removes the KB from the project. Both
            reveal on row hover. */}
        {node.kind === "kb" && onSelectAllInKb && !direct ? (
          <button
            type="button"
            onClick={() => onSelectAllInKb(node.id)}
            className="shrink-0 rounded px-1 text-2xs text-ink-meta opacity-0 transition-colors hover:bg-surface-border hover:text-ink-body group-hover:opacity-100"
          >
            {t("knowledge.selectAllKb")}
          </button>
        ) : null}
        {node.kind === "kb" && onRemoveKb ? (
          <button
            type="button"
            onClick={() => onRemoveKb(node.id)}
            className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-ink-muted opacity-0 transition-colors hover:bg-surface-border hover:text-error-text group-hover:opacity-100"
            title={t("common.remove")}
          >
            <X className="h-3 w-3" />
          </button>
        ) : null}
      </div>

      {/* children */}
      {isKbOrFolder &&
        isExpanded &&
        node.children?.map((child) => (
          <KbTreeRow
            key={child.id}
            node={child}
            depth={depth + 1}
            expanded={expanded}
            onToggle={onToggle}
            onExpand={onExpand}
            isDirectlyBound={isDirectlyBound}
            isCoveredByParent={isCoveredByParent}
            onToggleBinding={onToggleBinding}
            onRemoveKb={onRemoveKb}
            onSelectAllInKb={onSelectAllInKb}
          />
        ))}
    </>
  );
}

/* ── Tree traversal helpers ───────────────────────────────────── */

function findNodeInTree(
  nodes: KbBindingTreeNode[],
  id: string,
): KbBindingTreeNode | null {
  for (const n of nodes) {
    if (n.id === id) return n;
    if (n.children) {
      const found = findNodeInTree(n.children, id);
      if (found) return found;
    }
  }
  return null;
}

function containsNode(node: KbBindingTreeNode, id: string): boolean {
  if (node.id === id) return true;
  return node.children?.some((c) => containsNode(c, id)) ?? false;
}

/* ── Todos list ───────────────────────────────────────────────── */

/**
 * Compact TODO list rendered inside the context panel.
 *
 * Status icons mirror the upstream Agent Harness ``todo-list-panel``
 * convention: spinner for in_progress, check for completed, empty
 * circle for pending. ``in_progress`` rows show ``activeForm`` (the
 * gerund "Planning…") when the runtime provides it; everything else
 * renders ``content``.
 */
function TodosList({ items }: { items: TodoListItem[] }) {
  return (
    <ol className="space-y-1.5">
      {items.map((todo, i) => {
        const text =
          todo.status === "in_progress"
            ? (todo.activeForm ?? todo.content)
            : todo.content;
        return (
          <li
            key={`${i}-${todo.content}`}
            className={cn(
              "flex items-start gap-2 text-xs leading-snug",
              todo.status === "completed" &&
                "text-ink-body line-through decoration-ink-body",
              todo.status === "in_progress" && "font-medium text-ink-heading",
              todo.status === "pending" && "text-ink-heading",
            )}
          >
            <span className="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center">
              {todo.status === "completed" ? (
                <Check className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
              ) : todo.status === "in_progress" ? (
                <Loader2 className="h-3 w-3 animate-spin text-brand" />
              ) : (
                <Circle className="h-2.5 w-2.5 text-ink-muted" />
              )}
            </span>
            <span className="min-w-0 flex-1 break-words">{text}</span>
          </li>
        );
      })}
    </ol>
  );
}

/* ── Project memory tab ───────────────────────────────────────── */

function MemoryTab({
  entries,
  onDeleteEntry,
  onClear,
}: {
  entries: string[];
  onDeleteEntry?: (text: string) => void;
  onClear?: () => void;
}) {
  const { t } = useI18n();
  const [confirmClear, setConfirmClear] = useState(false);
  return (
    <div className="py-2">
      <div className="mb-2 flex items-center justify-between px-1">
        <span className="text-2xs text-ink-meta">
          {t("project.memoryAuto")} · {entries.length}
        </span>
        {entries.length > 0 && onClear && (
          <button
            type="button"
            onClick={() => {
              if (confirmClear) {
                onClear();
                setConfirmClear(false);
              } else {
                setConfirmClear(true);
              }
            }}
            onBlur={() => setConfirmClear(false)}
            className="rounded-md px-2 py-0.5 text-2xs text-ink-meta transition hover:bg-error-light hover:text-error-text"
          >
            {confirmClear
              ? t("project.memoryClearConfirm")
              : t("settings.memory.clearScope")}
          </button>
        )}
      </div>
      {entries.length === 0 ? (
        <p className="px-2 py-8 text-center text-2xs leading-relaxed text-ink-meta">
          {t("project.memoryEmpty")}
        </p>
      ) : (
        <ul className="space-y-1">
          {entries.map((entry, i) => (
            <li
              key={`${i}-${entry.slice(0, 16)}`}
              className="group flex items-start gap-2 rounded-md px-2 py-2 hover:bg-surface-soft"
            >
              <span className="flex-1 whitespace-pre-wrap text-2xs leading-relaxed text-ink-body">
                {entry}
              </span>
              {onDeleteEntry && (
                <button
                  type="button"
                  aria-label={t("common.delete")}
                  onClick={() => onDeleteEntry(entry)}
                  className="shrink-0 rounded p-1 text-ink-meta opacity-0 transition group-hover:opacity-100 hover:text-error-text"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Manual refresh button. Owns its own spinning state — clicking
 * triggers a 2s spin animation regardless of how fast the parent's
 * ``onClick`` callback completes (local listFiles is ~3ms, faster
 * than a single CSS spin frame, so without a hold the click looks
 * like a no-op). Auto-refresh paths in the page don't go through
 * this button, so the spinner only ever appears on user intent.
 */
// Exported so other right-rail file panels (e.g. ``TaskContextPanel``)
// can render the same throttled-spinner refresh button without
// duplicating the 2-second hold logic.
export function FileRefreshButton({ onClick }: { onClick: () => void }) {
  const { t } = useI18n();
  const [spinning, setSpinning] = useState(false);
  const timerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );
  return (
    <button
      type="button"
      disabled={spinning}
      onClick={(e) => {
        e.stopPropagation();
        if (spinning) return;
        setSpinning(true);
        onClick();
        timerRef.current = window.setTimeout(() => {
          setSpinning(false);
          timerRef.current = null;
        }, 2000);
      }}
      className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-border disabled:pointer-events-none"
      title={spinning ? t("system.refreshing") : t("system.refreshFiles")}
    >
      <RefreshCw className={cn("h-3.5 w-3.5", spinning && "animate-spin")} />
    </button>
  );
}

/**
 * File-tree upload button — the multipart-upload companion to
 * {@link FileRefreshButton}. Owns a hidden ``<input type=file multiple>``
 * and hands the picked files to the page's ``onUploadFiles``. The page
 * does the actual ``projectsApi.uploadFiles`` call, toast, and re-list.
 *
 * Exported so other right-rail file panels can render the same button.
 */
export function FileUploadButton({
  onUploadFiles,
}: {
  onUploadFiles: (files: File[]) => void;
}) {
  const { t } = useI18n();
  const inputRef = useRef<HTMLInputElement | null>(null);
  return (
    <>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          inputRef.current?.click();
        }}
        className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-border"
        title={t("project.uploadFile")}
      >
        <Upload className="h-3.5 w-3.5" />
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          const files = e.target.files ? Array.from(e.target.files) : [];
          // Reset so picking the same file twice still fires ``change``.
          e.target.value = "";
          if (files.length > 0) onUploadFiles(files);
        }}
      />
    </>
  );
}

/* ── Accordion Section ────────────────────────────────────────── */

// Exported so other right-rail panels (e.g. ``TaskContextPanel``) can
// render their own sections with the same visual language — same card
// chrome, same header / chevron / count semantics.
export function AccordionSection({
  open: controlledOpen,
  onOpenChange,
  title,
  icon: Icon,
  iconClassName,
  count,
  action,
  defaultOpen = false,
  className,
  contentClassName,
  fill = false,
  children,
}: {
  title: string;
  icon: React.ElementType;
  /** Override icon color (e.g. "text-success" / "text-warning" / "text-brand"). */
  iconClassName?: string;
  count?: number | string;
  action?: React.ReactNode;
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Extra classes on the section root (e.g. ``shrink-0`` / ``flex-1``). */
  className?: string;
  contentClassName?: string;
  /**
   * Stretch the open section (root → grid → content) to fill its flex parent
   * so the ``children`` list can own the remaining height and scroll inside it,
   * instead of the section sizing to content. Opt-in; off leaves the section
   * exactly as before. The parent must be a flex column with a bounded height.
   */
  fill?: boolean;
  children: React.ReactNode;
}) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const open = controlledOpen ?? internalOpen;
  const setOpen = (nextOpen: boolean) => {
    if (controlledOpen === undefined) setInternalOpen(nextOpen);
    onOpenChange?.(nextOpen);
  };

  return (
    // Spec 5.8 Context Section card: bg #F7F8FA, border 1px #F3F4F6,
    // radius 12, mb 8px. Header min-h 40, padding 10px 14px, gap 9px,
    // title 13px / 500 / #131313, count 12px / #6E7481, chevron #94A3B8.
    <div
      className={cn(
        "mb-2 overflow-hidden rounded-xl border border-[#F3F4F6] bg-surface-soft dark:border-surface-border",
        fill && "flex min-h-0 flex-1 flex-col",
        className,
      )}
    >
      {/* Hover lives on the row wrapper so background color spans the
          entire header (including the trailing action button), not just
          the toggle hit-area. */}
      <div className="flex items-center transition-colors duration-[120ms] hover:bg-[rgba(0,0,0,0.02)]">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex min-h-10 flex-1 items-center gap-[9px] px-3.5 py-2.5"
        >
          <ChevronRight
            className={cn(
              "h-3 w-3 shrink-0 text-ink-muted transition-transform duration-[150ms]",
              open && "rotate-90",
            )}
          />
          <Icon
            className={cn(
              "h-3.5 w-3.5 shrink-0",
              iconClassName ?? "text-ink-body",
            )}
            strokeWidth={1.9}
          />
          <span className="flex-1 text-left text-[13px] font-medium text-ink-heading">
            {title}
          </span>
          {count !== undefined && (
            <span className="text-xs text-ink-body">{count}</span>
          )}
        </button>
        {action && <div className="pr-3">{action}</div>}
      </div>
      <div
        className={cn(
          "grid transition-[grid-template-rows] duration-200 ease-in-out",
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
          // ``fill``: let the open section grow to fill its flex parent so the
          // content's own scroll container owns the leftover height.
          fill && open && "min-h-0 flex-1",
        )}
      >
        <div className={cn("overflow-hidden", fill && "min-h-0")}>
          {/* Nested white content surface (no border, top corners only) —
              layered over the outer surface-soft for depth. Mirrors the
              ContextSection in frontend/docs/design/app.jsx. */}
          <div
            className={cn(
              "overflow-hidden rounded-t-xl bg-surface px-3 py-3",
              contentClassName,
            )}
          >
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Panel ────────────────────────────────────────────────────── */

export const ProjectDetailContextPanel = ({
  width,
  title,
  instructionsTitle,
  scheduledTasksTitle,
  showTodos = true,
  instructions,
  onInstructionsChange,
  members,
  onAddMember,
  onOpenMember,
  onRemoveMember,
  defaultLeadSlug,
  onSetDefaultLead,
  chatBindings,
  onBindChat,
  onUnbindChat,
  onJoinChat,
  onDeleteChat,
  skills,
  onAddSkill,
  onRemoveSkill,
  mcpServers,
  onToggleMcpServer,
  docs = [],
  onToggleDoc,
  onImportFile,
  kbTree,
  bindings = [],
  onToggleBinding,
  onExpandKbFolder,
  projectArtifacts,
  onLoadArtifactVersions,
  onRemoveKb,
  onSelectAllInKb,
  uploadedFiles,
  onRemoveUploadedFile,
  generatedFiles,
  onOpenGeneratedFile,
  todos,
  worktrees,
  onContinueWorktree,
  onDiscardWorktree,
  scheduledTasks,
  onAddScheduledTask,
  onOpenScheduledTask,
  onEditScheduledTask,
  onToggleScheduledTask,
  onDeleteScheduledTask,
  onRunScheduledTask,
  fileTree,
  fileTreeTitle,
  fileTreeInTab = false,
  projectMemory,
  onMemoryDeleteEntry,
  onMemoryClear,
  hideProjectContextActions = false,
  rootPath = "",
  onFileClick,
  onFileDoubleClick,
  onOpenInFinder,
  onRefreshFiles,
  onUploadFiles,
  onOpenInSystem,
  onDeleteFile,
  initialOpenSection,
  multiOpen = false,
  collapsed: controlledCollapsed,
  onCollapsedChange,
}: ProjectContextPanelProps) => {
  const { t } = useI18n();

  // Resolve default titles through i18n when caller doesn't override.
  // Default header label is the conversation project name; the project
  // detail page overrides this with its own ``title`` prop.
  const resolvedTitle = title ?? t("conversation.project");
  const resolvedInstructionsTitle =
    instructionsTitle ?? t("project.instruction");
  const resolvedScheduledTasksTitle =
    scheduledTasksTitle ?? t("project.scheduledTasks");
  // Enabled ("on") tasks sort ahead of paused ones; order within each group is
  // preserved (Array.sort is stable in modern engines).
  const sortedScheduledTasks = [...(scheduledTasks ?? [])].sort(
    (a, b) => Number(b.status === "on") - Number(a.status === "on"),
  );
  const resolvedFileTreeTitle = fileTreeTitle ?? t("project.fileTree");

  // Section visibility — chat project omits sections it has no data for.
  const showInstructions = instructions !== undefined;
  const showScheduled =
    scheduledTasks !== undefined || onAddScheduledTask !== undefined;
  const showKbDocs =
    (kbTree !== undefined && kbTree !== null) || (docs && docs.length > 0);
  const showUploadedFiles = uploadedFiles !== undefined;
  const visibleUploadedFiles = uploadedFiles ?? [];
  const showGeneratedFiles = generatedFiles !== undefined;
  const visibleGeneratedFiles = generatedFiles ?? [];
  // Files section shows whenever the caller provides a fileTree array. Chat
  // projects should pass ``undefined`` to hide the section altogether.
  const showFiles = fileTree !== undefined;
  // Memory tab shows only when the caller passes project memory (real-project
  // home panel). Conversation panels / chat projects pass undefined → no tab.
  const showMemory = projectMemory !== undefined;
  const defaultOpenSection =
    initialOpenSection !== undefined
      ? initialOpenSection
      : showInstructions
        ? "instructions"
        : todos && todos.length > 0
          ? "todos"
          : visibleUploadedFiles.length > 0
            ? "uploads"
            : (fileTree?.length ?? 0) > 0
              ? "files"
              : (scheduledTasks ?? []).length > 0
                ? "scheduled"
                : null;
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const [connectorPickerOpen, setConnectorPickerOpen] = useState(false);
  // Version history, keyed by artifact id. Undefined = never asked, null =
  // asked and it failed. Cached because a history does not change while the
  // panel is open — a new delivery reloads the whole list anyway.
  const [openVersions, setOpenVersions] = useState<Record<string, boolean>>({});
  const [versionsById, setVersionsById] = useState<
    Record<string, GeneratedArtifactVersion[] | null>
  >({});
  const [userOpenSection, setUserOpenSection] = useState<string | null>(null);
  const [hasUserToggledSection, setHasUserToggledSection] = useState(false);
  const openSection = hasUserToggledSection
    ? userOpenSection
    : defaultOpenSection;
  // Tracks the last-clicked file in the file-tree section so it gets the
  // brand-tinted active background — same affordance the skill-detail
  // preview panel offers via ``activeFilePath``. Local-only state; the
  // page doesn't need to know which file is "selected" here.
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const collapsed = controlledCollapsed ?? internalCollapsed;

  const toggleCollapsed = (value: boolean) => {
    if (controlledCollapsed === undefined) setInternalCollapsed(value);
    onCollapsedChange?.(value);
  };
  const [docSearch, setDocSearch] = useState("");
  const [kbExpanded, setKbExpanded] = useState<Set<string>>(new Set());
  const instructionsEditorRef = useRef<InstructionsEditorHandle>(null);

  // Two modes:
  //  - multiOpen=true   → uncontrolled; ``defaultOpen=true`` on first
  //    render and each section toggles independently. AccordionSection
  //    keeps its own internal open state.
  //  - multiOpen=false  → single-open accordion driven by
  //    ``openSection`` (legacy chat project behaviour).
  const sectionState = (id: string, defaultOpen = false) =>
    multiOpen
      ? { defaultOpen: true }
      : {
          open: openSection === id,
          onOpenChange: (nextOpen: boolean) => {
            setHasUserToggledSection(true);
            setUserOpenSection(nextOpen ? id : null);
          },
          defaultOpen: initialOpenSection === undefined && defaultOpen,
        };

  const referencedDocs = docs.filter((d) => d.referenced);
  const readyDocs = docs.filter((d) => !d.referenced && d.status === "ready");
  const filteredReadyDocs = docSearch
    ? readyDocs.filter((d) =>
        d.name.toLowerCase().includes(docSearch.toLowerCase()),
      )
    : readyDocs;
  const filteredReferencedDocs = docSearch
    ? referencedDocs.filter((d) =>
        d.name.toLowerCase().includes(docSearch.toLowerCase()),
      )
    : referencedDocs;

  /* ── Collapsed icon strip ─────────────────────────────────── */
  if (collapsed) {
    return (
      <TooltipProvider delayDuration={150}>
        <div className="flex h-full w-full flex-col items-center bg-surface-base py-3">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => toggleCollapsed(false)}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-body transition-colors duration-[120ms] hover:bg-surface-soft hover:text-ink-heading"
              >
                <PanelRightOpen className="h-4 w-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="left">{t("common.expand")}</TooltipContent>
          </Tooltip>
        </div>
      </TooltipProvider>
    );
  }

  const fileCount = countFiles(fileTree ?? []);
  const fileActions =
    onRefreshFiles || onOpenInFinder || onUploadFiles ? (
      <div className="flex items-center gap-1.5">
        {onUploadFiles ? (
          <FileUploadButton onUploadFiles={onUploadFiles} />
        ) : null}
        {onRefreshFiles ? <FileRefreshButton onClick={onRefreshFiles} /> : null}
        {onOpenInFinder ? (
          <button
            type="button"
            onClick={onOpenInFinder}
            className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-border"
            title={t("project.inFinder")}
          >
            <FolderOpen className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
    ) : null;

  const fileTreePanel = (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      {/* Root row's vertical gap to the first child folder must equal
          the gap between any two sibling child folders (``TreeNode``
          rows = 26px tall, ``text-xs`` line-height + ``py-1``).
          The root row's content height is driven by its ``h-6`` (24px)
          refresh / open buttons, so we add ``py-[1px]`` to bring the
          row to 26px — that puts the folder icon at the same 6px
          vertical offset from the row's bottom edge as a child row's
          folder icon, making the inter-row gap symmetric at 12px. */}
      <div className="flex shrink-0 items-center gap-2 px-3.5 py-[1px]">
        <FolderOpen className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
        <span className="min-w-0 flex-1 truncate text-xs text-ink-label">
          {rootPath}
        </span>
        {fileCount > 0 ? (
          <span className="shrink-0 text-xs text-ink-body">{fileCount}</span>
        ) : null}
        {fileActions}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3.5 pb-2">
        <ProjectFileTree
          rootPath={rootPath}
          tree={fileTree ?? []}
          onFileClick={(path) => {
            setSelectedFilePath(path);
            onFileClick?.(path);
          }}
          onFileDoubleClick={onFileDoubleClick}
          onOpenInSystem={onOpenInSystem}
          onDeleteFile={onDeleteFile}
          activeFilePath={selectedFilePath}
          defaultOpenDepth={0}
          hideRootRow
        />
      </div>
    </div>
  );

  const filesAccordion = showFiles ? (
    <AccordionSection
      {...sectionState("files", (fileTree?.length ?? 0) > 0)}
      title={resolvedFileTreeTitle}
      icon={FolderTree}
      iconClassName="text-brand"
      contentClassName="px-5 py-2"
      count={fileCount > 0 ? fileCount : undefined}
      action={
        onRefreshFiles || onOpenInFinder || onUploadFiles ? (
          // Auto-refresh fires on turn-end; the manual refresh
          // button is the bail-out for mid-turn writes the
          // user wants to peek at before the turn closes.
          <div className="flex items-center gap-2">
            {onUploadFiles ? (
              <FileUploadButton onUploadFiles={onUploadFiles} />
            ) : null}
            {onRefreshFiles ? (
              <FileRefreshButton onClick={onRefreshFiles} />
            ) : null}
            {onOpenInFinder ? (
              <button
                type="button"
                onClick={(e) => {
                  // Stop the AccordionSection's header-wide
                  // toggle from firing when the user actually
                  // meant to open the folder externally.
                  e.stopPropagation();
                  onOpenInFinder();
                }}
                className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-border"
                title={t("project.inFinder")}
              >
                <FolderOpen className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
        ) : undefined
      }
    >
      <ProjectFileTree
        rootPath={rootPath}
        tree={fileTree ?? []}
        onFileClick={(path) => {
          setSelectedFilePath(path);
          onFileClick?.(path);
        }}
        onFileDoubleClick={onFileDoubleClick}
        onOpenInSystem={onOpenInSystem}
        onDeleteFile={onDeleteFile}
        activeFilePath={selectedFilePath}
        defaultOpenDepth={0}
      />
    </AccordionSection>
  ) : null;

  const uploadedFilesSection = showUploadedFiles ? (
    <AccordionSection
      {...sectionState("uploads", visibleUploadedFiles.length > 0)}
      title={t("conversation.uploadedFiles")}
      icon={Paperclip}
      iconClassName="text-brand"
      contentClassName="px-5 py-2"
      count={visibleUploadedFiles.length || undefined}
    >
      {visibleUploadedFiles.length > 0 ? (
        <div className="-mr-3 max-h-[25vh] space-y-1 overflow-y-auto overflow-x-hidden pr-3">
          {visibleUploadedFiles.map((f) => {
            // KB-sourced rows render a database glyph so the user can
            // visually tell "this is a live reference to a global KB
            // document" apart from a regular local upload.
            const isKb = f.sourceKind === "kb_doc";
            return (
              <div
                key={f.id}
                className="group -mx-2 flex items-center gap-2.5 rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-surface-muted/60"
              >
                {isKb ? (
                  <Database className="h-3.5 w-3.5 shrink-0 text-[#1d4ed8]" />
                ) : (
                  <FileTypeIcon filename={f.name} />
                )}
                <span className="flex-1 truncate text-ink-heading">
                  {f.name}
                </span>
                {f.parseStatus === "parsing" ? (
                  <span className="flex shrink-0 items-center gap-1 text-2xs text-ink-meta">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    {t("conversation.attachmentParsing")}
                  </span>
                ) : f.size ? (
                  <span className="shrink-0 text-2xs text-ink-meta">
                    {f.size}
                  </span>
                ) : null}
                {f.status === "failed" || f.parseStatus === "failed" ? (
                  <span className="shrink-0 rounded bg-error-light px-1 py-px text-2xs text-error-text">
                    {t("common.failed")}
                  </span>
                ) : null}
                {onRemoveUploadedFile ? (
                  <button
                    type="button"
                    onClick={() => onRemoveUploadedFile(f.id)}
                    className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-ink-meta opacity-0 transition group-hover:opacity-100 hover:bg-red-50 hover:text-red-500"
                    title={t("common.remove")}
                  >
                    <X className="h-3 w-3" />
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-2xs text-ink-meta">{t("knowledge.noUploadFiles")}</p>
      )}
    </AccordionSection>
  ) : null;

  // Shared by the session list and the project list: a row is a deliverable
  // with its version badge, and the badge expands into the deliverable's whole
  // history. Which set of deliverables is on screen is the caller's question;
  // how one is drawn is not.
  const renderArtifactRow = (f: GeneratedArtifactItem) => {
    // Offer history wherever there IS history — which is not the same
    // as "this row is a later version". A session that delivered v1 and
    // was then superseded by another session shows a v1 row, and the
    // versions it cannot see are exactly the ones worth reaching.
    const hasHistory =
      (f.versionNo != null && f.versionNo > 1) || f.isCurrent === false;
    const canExpand = Boolean(
      onLoadArtifactVersions && f.artifactId && hasHistory,
    );
    // Gated on ``canExpand`` too: the open flag is keyed by artifact, so
    // a row that offers no expander must not sprout one because another
    // row of the same deliverable was opened.
    const expanded = Boolean(
      canExpand && f.artifactId && openVersions[f.artifactId],
    );
    const versions = f.artifactId ? versionsById[f.artifactId] : undefined;

    const toggle = () => {
      const id = f.artifactId;
      if (!id || !onLoadArtifactVersions) return;
      setOpenVersions((prev) => ({ ...prev, [id]: !prev[id] }));
      if (versionsById[id] !== undefined) return; // already fetched
      void onLoadArtifactVersions(id)
        .then((items) => setVersionsById((prev) => ({ ...prev, [id]: items })))
        .catch(() => setVersionsById((prev) => ({ ...prev, [id]: null })));
    };

    return (
      <div key={f.id}>
        <div className="group -mx-2 flex w-[calc(100%+1rem)] items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-surface-muted/60">
          <button
            type="button"
            onClick={() => onOpenGeneratedFile?.(f.path)}
            disabled={!onOpenGeneratedFile}
            className="flex min-w-0 flex-1 items-center gap-2.5 text-left disabled:cursor-default"
            title={f.path}
          >
            <FileTypeIcon filename={f.name} />
            <span
              className={`flex-1 truncate ${
                f.isCurrent === false ? "text-ink-meta" : "text-ink-heading"
              }`}
            >
              {f.name}
            </span>
          </button>
          {f.versionNo != null && (f.versionNo > 1 || canExpand) ? (
            canExpand ? (
              <button
                type="button"
                onClick={toggle}
                aria-expanded={expanded}
                className="flex shrink-0 items-center gap-0.5 rounded bg-surface-muted px-1 text-2xs text-ink-meta transition-colors hover:bg-surface-muted/80"
                title={t("conversation.artifactShowVersions")}
              >
                <ChevronRight
                  className={`h-2.5 w-2.5 transition-transform ${
                    expanded ? "rotate-90" : ""
                  }`}
                />
                v{f.versionNo}
              </button>
            ) : (
              <span
                className="shrink-0 rounded bg-surface-muted px-1 text-2xs text-ink-meta"
                title={
                  f.isCurrent === false
                    ? t("conversation.artifactSupersededHint")
                    : undefined
                }
              >
                v{f.versionNo}
              </span>
            )
          ) : null}
          {f.size ? (
            <span className="shrink-0 text-2xs text-ink-meta">{f.size}</span>
          ) : null}
        </div>

        {expanded ? (
          <div className="ml-5 border-l border-[#f3f4f6] pl-2">
            {versions === undefined ? (
              <p className="py-1 text-2xs text-ink-meta">
                {t("conversation.artifactVersionsLoading")}
              </p>
            ) : versions === null ? (
              <p className="py-1 text-2xs text-ink-meta">
                {t("conversation.artifactVersionsFailed")}
              </p>
            ) : (
              versions
                .slice()
                .reverse()
                .map((v) => (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() =>
                      v.openable ? onOpenGeneratedFile?.(v.path) : undefined
                    }
                    disabled={!v.openable || !onOpenGeneratedFile}
                    className="flex w-full items-center gap-2 rounded px-1 py-1 text-left text-2xs text-ink-meta transition-colors hover:bg-surface-muted/60 disabled:cursor-default disabled:hover:bg-transparent"
                    title={
                      v.openable
                        ? v.path
                        : t("conversation.artifactVersionUnavailable")
                    }
                  >
                    <span className="w-6 shrink-0 tabular-nums">
                      v{v.versionNo}
                    </span>
                    <span className="flex-1 truncate">
                      {v.openable
                        ? (v.when ?? "")
                        : t("conversation.artifactVersionUnavailable")}
                    </span>
                    {v.size ? <span className="shrink-0">{v.size}</span> : null}
                  </button>
                ))
            )}
          </div>
        ) : null}
      </div>
    );
  };

  const generatedFilesSection = showGeneratedFiles ? (
    <AccordionSection
      {...sectionState("generated", visibleGeneratedFiles.length > 0)}
      title={t("conversation.generatedFiles")}
      icon={Sparkles}
      iconClassName="text-brand"
      contentClassName="px-5 py-2"
      count={visibleGeneratedFiles.length || undefined}
    >
      {visibleGeneratedFiles.length > 0 ? (
        <div className="space-y-1">
          {visibleGeneratedFiles.map(renderArtifactRow)}
        </div>
      ) : (
        <p className="text-2xs text-ink-meta">
          {t("conversation.noGeneratedFiles")}
        </p>
      )}
    </AccordionSection>
  ) : null;

  const contextSections = (
    <div>
      {/* Todos — agent-driven. Always renders the section so the
          user has a stable place to look for "what is the agent
          doing"; an empty list shows a hint instead of vanishing
          the whole accordion (which would make the panel layout
          shift around as the agent emits / clears todos). */}
      {showTodos && (
        <AccordionSection
          {...sectionState("todos", Boolean(todos && todos.length > 0))}
          title={t("conversation.todos")}
          icon={CheckSquare}
          iconClassName="text-brand"
          contentClassName="px-5 py-2"
          count={
            todos && todos.length > 0
              ? `${todos.filter((t) => t.status === "completed").length}/${todos.length}`
              : undefined
          }
        >
          {todos && todos.length > 0 ? (
            <TodosList items={todos} />
          ) : (
            <p className="text-2xs text-ink-meta">
              {t("conversation.noTodos", "暂无待办")}
            </p>
          )}
        </AccordionSection>
      )}

      {/* Instructions — project-only; chat project omits this. */}
      {showInstructions && (
        <AccordionSection
          {...sectionState("instructions", initialOpenSection === undefined)}
          title={resolvedInstructionsTitle}
          icon={FileText}
          iconClassName="text-context-icon"
          action={
            hideProjectContextActions ? undefined : (
              <button
                type="button"
                onClick={() => instructionsEditorRef.current?.openEditor()}
                className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
                title={t("project.writeInstructions")}
              >
                <FilePenLine className="h-3.5 w-3.5" />
              </button>
            )
          }
        >
          <InstructionsEditor
            ref={instructionsEditorRef}
            value={instructions ?? ""}
            onChange={onInstructionsChange ?? (() => {})}
            hideEditAction={hideProjectContextActions}
            showInlineEditAction={false}
          />
        </AccordionSection>
      )}

      {/* Member agents (PRD-NEXT §3.4) — the project's agent team. Caller
          passes ``members`` (even empty) to show the section; ``undefined``
          hides it (chat projects have no team). */}
      {members !== undefined && (
        <AccordionSection
          {...sectionState("members")}
          title={t("project.membersTitle" as Parameters<typeof t>[0])}
          icon={Bot}
          iconClassName="text-context-icon"
          count={members.length || undefined}
          action={
            onAddMember ? (
              <button
                type="button"
                onClick={(event) => {
                  event.currentTarget.blur();
                  onAddMember();
                }}
                className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
                title={t("agent.addMember" as Parameters<typeof t>[0])}
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            ) : undefined
          }
        >
          {members.length > 0 ? (
            // Cap the member list so a large team doesn't push the sections
            // below it (Files / Automations) out of view on a short window —
            // it scrolls internally instead. ``vh`` keeps it window-relative:
            // tall windows show the whole list, short ones scroll. The list
            // fills the (symmetric) scroll content box so the row hover bg is
            // padded equally on both sides.
            <div className="-mr-3 max-h-[25vh] overflow-y-auto overflow-x-hidden pr-3">
              <div>
                {members.map((member, index) => {
                  const isOrphan = member.orphan === true;
                  const isDefaultLead =
                    !!defaultLeadSlug && member.slug === defaultLeadSlug;
                  return (
                    <div key={member.id}>
                      {index > 0 ? (
                        <div className="h-px w-full bg-[#f7f8fa]" />
                      ) : null}
                      <div className="group relative rounded-lg bg-card">
                        <div className="pointer-events-none absolute inset-0 rounded-lg transition-colors group-hover:bg-[#f7f8fa]" />
                        <div className="relative z-10 flex items-center gap-2.5 px-2 py-2.5">
                          {/* Row body opens the shared agent (global edit). */}
                          <button
                            type="button"
                            disabled={isOrphan || !onOpenMember}
                            onClick={(event) => {
                              event.currentTarget.blur();
                              // Only open the overlay when we know the global
                              // library slug — local member slug isn't valid
                              // for the agent detail endpoint (would 404).
                              if (member.sourceAgentSlug) {
                                onOpenMember?.(member.sourceAgentSlug);
                              }
                            }}
                            className="flex min-w-0 flex-1 items-center gap-2.5 text-left disabled:cursor-default"
                          >
                            <div
                              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${
                                isOrphan
                                  ? "bg-surface-soft text-ink-muted"
                                  : "bg-brand/8 text-brand"
                              }`}
                            >
                              <Bot className="h-3 w-3" />
                            </div>
                            <div className="min-w-0 flex-1">
                              <div
                                className={`truncate text-xs font-medium ${
                                  isOrphan
                                    ? "text-ink-meta"
                                    : "text-ink-heading"
                                }`}
                              >
                                {member.name}
                              </div>
                              <div className="truncate text-2xs text-ink-meta">
                                {isOrphan
                                  ? t(
                                      "agent.memberOrphaned" as Parameters<
                                        typeof t
                                      >[0],
                                    )
                                  : `${
                                      member.runtimeLabel
                                        ? `${member.runtimeLabel} · `
                                        : ""
                                    }${
                                      member.model
                                        ? modelLabel(member.model)
                                        : member.slug
                                    }`}
                              </div>
                            </div>
                          </button>
                          {onSetDefaultLead && !isOrphan && (
                            // The current lead stays visible without hovering —
                            // "who leads this project" should be readable at a
                            // glance, not discovered by pointing at rows.
                            <button
                              type="button"
                              onClick={(event) => {
                                event.currentTarget.blur();
                                onSetDefaultLead(
                                  isDefaultLead ? null : member.slug,
                                );
                              }}
                              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded transition-opacity ${
                                isDefaultLead
                                  ? "text-brand opacity-100"
                                  : "text-ink-muted opacity-0 hover:text-ink-body group-hover:opacity-100"
                              }`}
                              title={
                                isDefaultLead
                                  ? t(
                                      "agent.clearDefaultLead" as Parameters<
                                        typeof t
                                      >[0],
                                    )
                                  : t(
                                      "agent.setDefaultLead" as Parameters<
                                        typeof t
                                      >[0],
                                    )
                              }
                              aria-label={
                                isDefaultLead
                                  ? t(
                                      "agent.clearDefaultLead" as Parameters<
                                        typeof t
                                      >[0],
                                    )
                                  : t(
                                      "agent.setDefaultLead" as Parameters<
                                        typeof t
                                      >[0],
                                    )
                              }
                            >
                              <Crown className="h-3.5 w-3.5" />
                            </button>
                          )}
                          {onRemoveMember && (
                            <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                              <button
                                type="button"
                                onClick={(event) => {
                                  event.currentTarget.blur();
                                  onRemoveMember(member.slug);
                                }}
                                className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-red-50 hover:text-red-600"
                                title={t(
                                  "agent.deleteMember" as Parameters<
                                    typeof t
                                  >[0],
                                )}
                                aria-label={t(
                                  "agent.deleteMember" as Parameters<
                                    typeof t
                                  >[0],
                                )}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <p className="text-2xs leading-5 text-ink-meta">
              {t("agent.noMembers" as Parameters<typeof t>[0])}
            </p>
          )}
        </AccordionSection>
      )}

      {/* Worktrees — isolated branch copies sessions run in (git projects
          only). Rows are computed on read; ``null`` counts render as
          "unknown" and the caller's confirm dialog owns the fail-closed
          discard consent. Only shown when there is something to manage —
          an empty list would just advertise plumbing. */}
      {worktrees !== undefined && worktrees.length > 0 && (
        <AccordionSection
          {...sectionState("worktrees")}
          title={t("project.worktreesTitle" as Parameters<typeof t>[0])}
          icon={GitBranch}
          iconClassName="text-context-icon"
          count={worktrees.length}
        >
          <div className="-mr-3 max-h-[25vh] overflow-y-auto overflow-x-hidden pr-3">
            {worktrees.map((wt, index) => {
              const verified =
                wt.dirtyFiles !== null && wt.aheadCommits !== null;
              const clean =
                verified && wt.dirtyFiles === 0 && wt.aheadCommits === 0;
              const statusText = !verified
                ? t("project.worktreeUnknown" as Parameters<typeof t>[0])
                : clean
                  ? t("project.worktreeClean" as Parameters<typeof t>[0])
                  : t("project.worktreeStatus" as Parameters<typeof t>[0], {
                      dirty: wt.dirtyFiles ?? 0,
                      ahead: wt.aheadCommits ?? 0,
                    });
              const mergeHint =
                verified && (wt.aheadCommits ?? 0) > 0 && wt.branch
                  ? t("project.worktreeMergeHint" as Parameters<typeof t>[0], {
                      branch: wt.branch,
                    })
                  : undefined;
              return (
                <div key={wt.name}>
                  {index > 0 ? (
                    <div className="h-px w-full bg-[#f7f8fa]" />
                  ) : null}
                  <div className="group relative rounded-lg bg-card">
                    <div className="pointer-events-none absolute inset-0 rounded-lg transition-colors group-hover:bg-[#f7f8fa]" />
                    <div
                      className="relative z-10 flex items-center gap-2.5 px-2 py-2.5"
                      title={mergeHint}
                    >
                      <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand/8 text-brand">
                        <GitBranch className="h-3 w-3" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium text-ink-heading">
                          {wt.name}
                        </div>
                        <div className="truncate text-2xs text-ink-meta">
                          {wt.branch ? `${wt.branch} · ` : ""}
                          {statusText}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                        {onContinueWorktree && (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.currentTarget.blur();
                              onContinueWorktree(wt.name);
                            }}
                            className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
                            title={t(
                              "project.worktreeContinue" as Parameters<
                                typeof t
                              >[0],
                            )}
                            aria-label={t(
                              "project.worktreeContinue" as Parameters<
                                typeof t
                              >[0],
                            )}
                          >
                            <MessageSquarePlus className="h-3.5 w-3.5" />
                          </button>
                        )}
                        {onDiscardWorktree && (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.currentTarget.blur();
                              onDiscardWorktree(wt);
                            }}
                            className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-red-50 hover:text-red-600"
                            title={t(
                              "project.worktreeDiscard" as Parameters<
                                typeof t
                              >[0],
                            )}
                            aria-label={t(
                              "project.worktreeDiscard" as Parameters<
                                typeof t
                              >[0],
                            )}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </AccordionSection>
      )}

      {/* Skills — project projects only. Chat projects have
          no per-conversation binding semantics (the global skill
          catalog applies to every chat), so the panel skips this
          section to avoid suggesting configurability that isn't
          there. Caller signals "show this section" by passing
          ``skills`` (even an empty array); ``undefined`` hides
          the whole accordion. */}
      {skills !== undefined && (
        <AccordionSection
          {...sectionState("skills")}
          title={t("commandPalette.skills")}
          icon={Zap}
          iconClassName="text-context-icon"
          count={skills.filter((s) => s.enabled).length || undefined}
          action={
            onAddSkill ? (
              <button
                type="button"
                onClick={(event) => {
                  event.currentTarget.blur();
                  onAddSkill();
                }}
                className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
                title={t("project.addSkill")}
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            ) : undefined
          }
        >
          {skills.filter((s) => s.enabled).length > 0 ? (
            <div className="space-y-2">
              {skills
                .filter((s) => s.enabled)
                .map((skill) => (
                  <div
                    key={skill.id}
                    className="group flex items-start gap-2.5 rounded-lg bg-card px-3 py-2.5 transition-colors hover:bg-surface"
                  >
                    <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand/8 text-brand">
                      <Bot className="h-3 w-3" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-xs font-medium text-ink-heading">
                          {skill.name}
                        </span>
                        <span
                          className={cn(
                            "shrink-0 rounded px-1.5 py-px text-2xs",
                            skill.source === "project"
                              ? "bg-purple-50 text-purple-600"
                              : "bg-surface-soft text-ink-meta",
                          )}
                        >
                          {skill.source === "project"
                            ? t("skill.project")
                            : t("skill.user")}
                        </span>
                      </div>
                      {skill.description && (
                        <p className="mt-1 line-clamp-2 text-2xs leading-4 text-ink-body">
                          {skill.description}
                        </p>
                      )}
                    </div>
                    {/* X button — unbind the skill from this project */}
                    {onRemoveSkill ? (
                      <button
                        type="button"
                        onClick={() => onRemoveSkill(skill.id)}
                        className="mt-1 flex h-5 w-5 shrink-0 items-center justify-center rounded text-ink-meta opacity-0 transition group-hover:opacity-100 hover:bg-red-50 hover:text-red-500"
                        title={t("common.remove")}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    ) : null}
                  </div>
                ))}
            </div>
          ) : (
            <p className="text-2xs leading-5 text-ink-meta">
              {t("project.noSkills")}
              <br />
              {t("project.skillHint")}
            </p>
          )}
        </AccordionSection>
      )}

      {/* Connectors — MCP server selection */}
      {mcpServers !== undefined && (
        <AccordionSection
          {...sectionState("connectors")}
          title={t("project.connectors" as Parameters<typeof t>[0])}
          icon={Link2}
          iconClassName="text-context-icon"
          count={mcpServers.filter((s) => s.enabled).length || undefined}
          action={
            onToggleMcpServer ? (
              <button
                type="button"
                onClick={() => setConnectorPickerOpen(true)}
                className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
                title={t("project.addConnector" as Parameters<typeof t>[0])}
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            ) : undefined
          }
        >
          {mcpServers.filter((s) => s.enabled).length > 0 ? (
            <div className="space-y-2">
              {mcpServers
                .filter((s) => s.enabled)
                .map((server) => (
                  <div
                    key={server.slug}
                    className="group flex items-start gap-2.5 rounded-lg bg-card px-3 py-2.5 transition-colors hover:bg-surface"
                  >
                    <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand/8 text-brand">
                      <Link2 className="h-3 w-3" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-medium text-ink-heading">
                        {server.display_name}
                      </div>
                      {server.description && (
                        <p className="mt-1 line-clamp-2 text-2xs leading-4 text-ink-body">
                          {server.description}
                        </p>
                      )}
                    </div>
                    {onToggleMcpServer && (
                      <button
                        type="button"
                        onClick={() => onToggleMcpServer(server.slug, false)}
                        className="mt-1 flex h-5 w-5 shrink-0 items-center justify-center rounded text-ink-meta opacity-0 transition group-hover:opacity-100 hover:bg-red-50 hover:text-red-500"
                        title={t("common.remove" as Parameters<typeof t>[0])}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                ))}
            </div>
          ) : (
            <p className="text-2xs leading-5 text-ink-meta">
              {t("project.noConnectorsPlaceholder" as Parameters<typeof t>[0])}
            </p>
          )}
        </AccordionSection>
      )}

      {/* Generated files (agent-delivered artifacts — the 生成文件 list) */}
      {generatedFilesSection}

      {/* Scheduled — project-only; chat project omits this. */}
      {showScheduled && (
        <AccordionSection
          {...sectionState("scheduled", (scheduledTasks ?? []).length > 0)}
          key={`sched-${(scheduledTasks ?? []).length}`}
          title={resolvedScheduledTasksTitle}
          icon={Clock}
          iconClassName="text-context-icon"
          count={(scheduledTasks ?? []).length || undefined}
          action={
            onAddScheduledTask ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onAddScheduledTask();
                }}
                className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
                title={t("project.addScheduledTask")}
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            ) : undefined
          }
        >
          {(scheduledTasks ?? []).length > 0 ? (
            <div className="-mr-3 max-h-[25vh] overflow-y-auto overflow-x-hidden pr-3">
              <div className="divide-y divide-[#f3f4f6]">
                {sortedScheduledTasks.map((task) => (
                  <div
                    key={task.id}
                    // Row click opens the task's detail page; force the default
                    // arrow cursor (no pointer/hand per design, and no text
                    // I-beam over the task name).
                    className="group relative cursor-default rounded-lg bg-card"
                    role={onOpenScheduledTask ? "button" : undefined}
                    tabIndex={onOpenScheduledTask ? 0 : undefined}
                    onClick={
                      onOpenScheduledTask
                        ? () => onOpenScheduledTask(task.id)
                        : undefined
                    }
                    onKeyDown={
                      onOpenScheduledTask
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onOpenScheduledTask(task.id);
                            }
                          }
                        : undefined
                    }
                  >
                    <div className="pointer-events-none absolute inset-0 rounded-lg transition-colors group-hover:bg-[#f7f8fa]" />
                    <div className="relative z-10 px-2 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate text-xs font-medium text-ink-heading">
                          {task.name}
                        </span>
                        <span
                          className={cn(
                            "rounded px-1.5 py-px text-2xs",
                            task.status === "on"
                              ? "bg-emerald-50 text-emerald-600"
                              : "bg-surface-soft text-ink-meta",
                          )}
                        >
                          {task.status === "on"
                            ? t("project.taskEnabled")
                            : t("project.taskPaused")}
                        </span>
                        {(onEditScheduledTask ||
                          onToggleScheduledTask ||
                          onDeleteScheduledTask) && (
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <button
                                type="button"
                                className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-ink-meta transition-colors hover:bg-surface-muted hover:text-ink-label"
                                title={t("project.taskActions")}
                                // The row itself opens the detail page; keep a
                                // menu click from also triggering that handler.
                                onClick={(e) => e.stopPropagation()}
                                onPointerDown={(e) => e.stopPropagation()}
                              >
                                <MoreHorizontal className="h-3.5 w-3.5" />
                              </button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent
                              align="end"
                              className="min-w-[140px]"
                              onCloseAutoFocus={(e) => e.preventDefault()}
                              // Portaled, but clicks still bubble through the
                              // React tree to the row's onClick (which opens the
                              // detail page). Stop it so toggling pause/enable or
                              // deleting doesn't also navigate away.
                              onClick={(e) => e.stopPropagation()}
                            >
                              {onEditScheduledTask && (
                                <DropdownMenuItem
                                  onSelect={() => onEditScheduledTask(task.id)}
                                >
                                  <FilePenLine />
                                  {t("common.edit")}
                                </DropdownMenuItem>
                              )}
                              {onToggleScheduledTask && (
                                <DropdownMenuItem
                                  onSelect={() =>
                                    onToggleScheduledTask(
                                      task.id,
                                      task.status === "on" ? "off" : "on",
                                    )
                                  }
                                >
                                  {task.status === "on" ? <Pause /> : <Power />}
                                  {task.status === "on"
                                    ? t("cron.pause")
                                    : t("project.enable")}
                                </DropdownMenuItem>
                              )}
                              {onRunScheduledTask && (
                                <DropdownMenuItem
                                  disabled={task.status !== "on"}
                                  onSelect={() => {
                                    if (task.status === "on") {
                                      onRunScheduledTask(task.id);
                                    }
                                  }}
                                >
                                  <Play />
                                  {t("cron.runNow")}
                                </DropdownMenuItem>
                              )}
                              {onDeleteScheduledTask && (
                                <>
                                  <DropdownMenuSeparator />
                                  <DropdownMenuItem
                                    variant="destructive"
                                    onSelect={() =>
                                      onDeleteScheduledTask(task.id)
                                    }
                                  >
                                    <Trash2 />
                                    {t("common.delete")}
                                  </DropdownMenuItem>
                                </>
                              )}
                            </DropdownMenuContent>
                          </DropdownMenu>
                        )}
                      </div>
                      <div className="mt-1.5 flex items-center gap-2 text-2xs text-ink-meta">
                        <span>{task.humanReadable}</span>
                        <span className="text-surface-border">·</span>
                        <span>
                          {t("project.nextRun" as Parameters<typeof t>[0], {
                            time: task.nextRun,
                          })}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-2xs text-ink-meta">
              {t("project.noScheduledTasks")}
            </p>
          )}
        </AccordionSection>
      )}

      {/* Uploaded files (session attachments) — sits between the automations
          and knowledge-base sections. */}
      {uploadedFilesSection}

      {/* Docs — KB tree or flat list (project-only) */}
      {showKbDocs && (
        <AccordionSection
          {...sectionState("docs")}
          title={t("project.knowledgeBase")}
          icon={Database}
          iconClassName="text-context-icon"
          count={
            kbTree
              ? bindings.length
                ? t("knowledge.bindings", { count: String(bindings.length) })
                : undefined
              : referencedDocs.length
                ? t("knowledge.referencedDocs", {
                    count: String(referencedDocs.length),
                  })
                : undefined
          }
          action={
            onImportFile ? (
              <button
                type="button"
                onClick={onImportFile}
                className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
                title={t("knowledge.manageKb")}
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            ) : undefined
          }
        >
          {kbTree && kbTree.length > 0 ? (
            <div>
              {/* Search */}
              {kbTree.length > 1 && (
                <div className="relative mb-2">
                  <Search className="absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-ink-muted" />
                  <input
                    type="text"
                    value={docSearch}
                    onChange={(e) => setDocSearch(e.target.value)}
                    placeholder={t("knowledge.searchKbPlaceholder")}
                    className="h-7 w-full rounded-md border border-surface-border bg-card pl-7 pr-2.5 text-2xs text-ink-label placeholder:text-ink-muted/60 transition-colors focus:border-brand/40 focus:outline-none"
                  />
                </div>
              )}
              <div className="-mr-3 max-h-[25vh] space-y-0 overflow-y-auto overflow-x-hidden pr-3">
                {(() => {
                  // Precompute binding lookups so each KbTreeRow can
                  // resolve its checkbox state without re-walking the
                  // ``bindings`` array. ``directlyBound`` is the exact
                  // ``(kind, target_id)`` match; ``coveredByParent``
                  // walks the tree to see if an ancestor row implicitly
                  // includes this node (a kb binding covers every
                  // folder/doc inside; a folder binding covers every
                  // doc inside).
                  const directSet = new Set(
                    bindings.map((b) => `${b.binding_kind}:${b.target_id}`),
                  );
                  const isDirectlyBound = (
                    kind: "kb" | "folder" | "document",
                    id: string,
                  ): boolean => directSet.has(`${kind}:${id}`);
                  const isCoveredByParent = (
                    kind: "kb" | "folder" | "document",
                    id: string,
                  ): boolean => {
                    if (kind === "kb" || !kbTree) return false;
                    for (const kb of kbTree) {
                      if (
                        isDirectlyBound("kb", kb.id) &&
                        containsNode(kb, id)
                      ) {
                        return true;
                      }
                      if (kind === "document") {
                        // Walk folders inside kb to find a folder
                        // binding that contains this doc.
                        const folder = findNodeInTree(kbTree, id);
                        if (folder) {
                          // We need the folder's ancestor folder. Use
                          // a simple recursive walk against kbTree.
                          const findCoveringFolder = (
                            node: KbBindingTreeNode,
                          ): boolean => {
                            for (const c of node.children ?? []) {
                              if (
                                c.kind === "folder" &&
                                isDirectlyBound("folder", c.id) &&
                                containsNode(c, id)
                              ) {
                                return true;
                              }
                              if (c.children && findCoveringFolder(c)) {
                                return true;
                              }
                            }
                            return false;
                          };
                          if (findCoveringFolder(kb)) return true;
                        }
                      }
                    }
                    return false;
                  };
                  return (
                    docSearch
                      ? kbTree.filter((kb) =>
                          kb.name
                            .toLowerCase()
                            .includes(docSearch.toLowerCase()),
                        )
                      : kbTree
                  ).map((kb) => (
                    <KbTreeRow
                      key={kb.id}
                      node={kb}
                      depth={0}
                      expanded={kbExpanded}
                      onToggle={(id) => {
                        setKbExpanded((prev) => {
                          const n = new Set(prev);
                          if (n.has(id)) {
                            n.delete(id);
                          } else {
                            n.add(id);
                          }
                          return n;
                        });
                      }}
                      onExpand={(id) => {
                        const kb2 = kbTree.find((k) => k.id === id);
                        if (kb2 && !kb2.childrenLoaded && onExpandKbFolder) {
                          void onExpandKbFolder(kb2.id, id);
                        } else {
                          const folder = findNodeInTree(kbTree, id);
                          if (
                            folder &&
                            !folder.childrenLoaded &&
                            onExpandKbFolder
                          ) {
                            const parentKb = kbTree.find((k) =>
                              containsNode(k, id),
                            );
                            if (parentKb)
                              void onExpandKbFolder(parentKb.id, id);
                          }
                        }
                      }}
                      isDirectlyBound={isDirectlyBound}
                      isCoveredByParent={isCoveredByParent}
                      onToggleBinding={onToggleBinding}
                      onRemoveKb={onRemoveKb}
                      onSelectAllInKb={onSelectAllInKb}
                    />
                  ));
                })()}
              </div>
            </div>
          ) : kbTree && kbTree.length === 0 ? (
            <p className="text-2xs text-ink-meta">{t("knowledge.noKb")}</p>
          ) : (
            <>
              {docs.length > 2 && (
                <div className="relative mb-3">
                  <Search className="absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-ink-muted" />
                  <input
                    type="text"
                    value={docSearch}
                    onChange={(e) => setDocSearch(e.target.value)}
                    placeholder={t("knowledge.searchDocPlaceholder")}
                    className="h-7 w-full rounded-md border border-surface-border bg-card pl-7 pr-2.5 text-2xs text-ink-label placeholder:text-ink-muted/60 transition-colors focus:border-brand/40 focus:outline-none"
                  />
                </div>
              )}
              {docs.filter((d) => d.status === "ready").length > 0 ? (
                <div className="space-y-3">
                  {filteredReferencedDocs.length > 0 && (
                    <div>
                      <div className="mb-1.5 text-2xs font-medium tracking-wide text-ink-meta">
                        {t("knowledge.referenced")}
                      </div>
                      <div className="space-y-0.5">
                        {filteredReferencedDocs.map((doc) => (
                          <div
                            key={doc.id}
                            className="group flex items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-surface-muted/60"
                          >
                            <Checkbox
                              checked
                              onCheckedChange={() => onToggleDoc?.(doc.id)}
                              className="h-3.5 w-3.5 rounded-sm"
                            />
                            <span className="flex-1 truncate text-ink-label">
                              {doc.name}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {filteredReadyDocs.length > 0 && (
                    <div>
                      <div className="mb-1.5 text-2xs font-medium tracking-wide text-ink-meta">
                        {t("knowledge.available")}
                      </div>
                      <div className="space-y-0.5">
                        {filteredReadyDocs.map((doc) => (
                          <label
                            key={doc.id}
                            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-surface-muted/60"
                          >
                            <Checkbox
                              onCheckedChange={() => onToggleDoc?.(doc.id)}
                              className="h-3.5 w-3.5 rounded-sm"
                            />
                            <span className="flex-1 truncate text-ink-label">
                              {doc.name}
                            </span>
                          </label>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-2xs text-ink-meta">
                  {t("knowledge.noDocs")}
                </p>
              )}
            </>
          )}
        </AccordionSection>
      )}
      {/* Everything the project holds, whoever produced it. The 生成文件 section
          above is one conversation's output; this is the workspace, which is
          what a session that has delivered nothing has to read from. */}
      {projectArtifacts !== undefined && (
        <AccordionSection
          {...sectionState("projectArtifacts", projectArtifacts.length > 0)}
          title={t("project.deliverables" as Parameters<typeof t>[0])}
          icon={Sparkles}
          iconClassName="text-brand"
          contentClassName="px-5 py-2"
          count={projectArtifacts.length || undefined}
        >
          {projectArtifacts.length > 0 ? (
            <div className="space-y-1">
              {projectArtifacts.map(renderArtifactRow)}
            </div>
          ) : (
            <p className="text-2xs text-ink-meta">
              {t("project.noDeliverables" as Parameters<typeof t>[0])}
            </p>
          )}
        </AccordionSection>
      )}
      {/* IM groups bound to this project. A group standing for a project is
          how channel conversations get a project without anyone typing its
          name; see docs/design/channel-project-binding-and-default-lead.md. */}
      {chatBindings !== undefined && onBindChat && (
        <AccordionSection
          {...sectionState("chatBindings")}
          title={t("project.chatBindingsTitle" as Parameters<typeof t>[0])}
          icon={MessageSquare}
          iconClassName="text-context-icon"
          count={chatBindings.length || undefined}
          action={
            <button
              type="button"
              onClick={(event) => {
                event.currentTarget.blur();
                onBindChat();
              }}
              className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
              title={t("project.bindChat" as Parameters<typeof t>[0])}
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          }
        >
          {chatBindings.length > 0 ? (
            <div className="flex flex-col">
              {chatBindings.map((chat) => (
                <div
                  key={chat.id}
                  className="group relative flex items-center gap-2.5 rounded-lg px-2 py-2 hover:bg-[#f7f8fa]"
                >
                  <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand/8 text-brand">
                    <MessageSquare className="h-3 w-3" />
                  </div>
                  <div className="min-w-0 flex-1 truncate text-xs text-ink-heading">
                    {chat.platformLabel ? (
                      <span className="text-ink-meta">
                        {chat.platformLabel} ·{" "}
                      </span>
                    ) : null}
                    {chat.name}
                  </div>
                  {/* Same actions the picker offers, in the same order —
                      whichever surface you reach a group from behaves alike. */}
                  <TooltipProvider delayDuration={0}>
                    <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                      {onJoinChat && chat.needsJoin && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              onClick={(event) => {
                                event.currentTarget.blur();
                                onJoinChat(chat.id);
                              }}
                              className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
                              aria-label={t(
                                "project.createChatJoin" as Parameters<
                                  typeof t
                                >[0],
                              )}
                            >
                              <LogIn className="h-3.5 w-3.5" />
                            </button>
                          </TooltipTrigger>
                          <TooltipContent>
                            {t(
                              "project.createChatJoin" as Parameters<
                                typeof t
                              >[0],
                            )}
                          </TooltipContent>
                        </Tooltip>
                      )}
                      {onUnbindChat && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              onClick={(event) => {
                                event.currentTarget.blur();
                                onUnbindChat(chat.id);
                              }}
                              className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
                              aria-label={t(
                                "project.unbindChat" as Parameters<typeof t>[0],
                              )}
                            >
                              <Unlink className="h-3.5 w-3.5" />
                            </button>
                          </TooltipTrigger>
                          <TooltipContent>
                            {t("project.unbindChat" as Parameters<typeof t>[0])}
                          </TooltipContent>
                        </Tooltip>
                      )}
                      {onDeleteChat && chat.createdByValuz && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              onClick={(event) => {
                                event.currentTarget.blur();
                                onDeleteChat(chat.id);
                              }}
                              className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-red-50 hover:text-red-600"
                              aria-label={t(
                                "project.deleteChat" as Parameters<typeof t>[0],
                              )}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </TooltipTrigger>
                          <TooltipContent>
                            {t("project.deleteChat" as Parameters<typeof t>[0])}
                          </TooltipContent>
                        </Tooltip>
                      )}
                    </div>
                  </TooltipProvider>
                </div>
              ))}
            </div>
          ) : (
            // Matches the knowledge-base empty state: same type scale, same
            // flush alignment. Two empty states in one panel should not read
            // as two different components.
            <p className="text-2xs text-ink-meta">
              {t("project.chatBindingsEmpty" as Parameters<typeof t>[0])}
            </p>
          )}
        </AccordionSection>
      )}
      {fileTreeInTab ? null : filesAccordion}
    </div>
  );

  return (
    <div
      className="flex h-full w-full flex-col bg-surface-base"
      style={width !== undefined ? { width } : undefined}
    >
      {/* One tab shell everywhere — the assistant conversation has only the
          "workspace" tab (no project files) but keeps the same line-tab styling
          for consistency. The "project files" tab is added only when files exist. */}
      <Tabs
        defaultValue="context"
        className="flex h-full min-h-0 flex-col gap-0"
      >
        <header className="flex h-12 shrink-0 items-end px-3">
          <TabsList
            variant="line"
            className="group-data-[orientation=horizontal]/tabs:h-full justify-start gap-1 border-0 p-0 [&_[data-slot=tabs-trigger]]:pt-2.5"
          >
            <TabsTrigger value="context" className="after:!opacity-0">
              {resolvedTitle}
            </TabsTrigger>
            {showFiles && fileTreeInTab && (
              <TabsTrigger value="files" className="after:!opacity-0">
                {t("project.projectFiles")}
              </TabsTrigger>
            )}
            {showMemory && (
              <TabsTrigger value="memory" className="after:!opacity-0">
                {t("project.projectMemory")}
              </TabsTrigger>
            )}
          </TabsList>
        </header>
        <TabsContent value="context" className="min-h-0 overflow-y-auto px-2">
          {contextSections}
        </TabsContent>
        {showFiles && fileTreeInTab && (
          <TabsContent
            value="files"
            className="min-h-0 overflow-hidden px-2 pb-2"
          >
            {fileTreePanel}
          </TabsContent>
        )}
        {showMemory && (
          <TabsContent
            value="memory"
            className="min-h-0 overflow-y-auto px-2 pb-2"
          >
            <MemoryTab
              entries={projectMemory ?? []}
              onDeleteEntry={onMemoryDeleteEntry}
              onClear={onMemoryClear}
            />
          </TabsContent>
        )}
      </Tabs>
      {mcpServers !== undefined && onToggleMcpServer && (
        <ConnectorPickerDialog
          open={connectorPickerOpen}
          onOpenChange={setConnectorPickerOpen}
          connectors={mcpServers}
          onToggle={onToggleMcpServer}
        />
      )}
    </div>
  );
};
