import { useState, useRef, useEffect, useCallback } from "react";
import {
  AlertCircle,
  ArrowUp,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Database,
  Flame,
  FolderClosed,
  Gauge,
  Hand,
  Loader2,
  Lock,
  Paperclip,
  Plus,
  Square,
  X,
  Zap,
} from "lucide-react";

// Per-mode metadata for the approval-mode selector. The icon + colour
// classes drive both the trigger and the dropdown items so the active
// mode reads as a status pill (orange = "full access" risk indicator,
// blue = "auto review" assisted-by-LLM tint, neutral = default).
//
// ``visible`` controls which modes show up in the dropdown. The
// ``auto_review`` mode is hidden in this iteration (no Claude classifier
// shipped yet) but the entry stays in the map so sessions already
// persisted with ``permission_mode="auto_review"`` still render
// correctly in the trigger.
type PermissionMode = "default" | "auto_review" | "full_access";

const PERMISSION_META: Record<
  PermissionMode,
  {
    icon: typeof Hand;
    visible: boolean;
    triggerClass: string;
    iconClass: string;
  }
> = {
  default: {
    icon: Hand,
    visible: true,
    triggerClass: "text-ink-body hover:bg-surface-soft hover:text-ink-heading",
    iconClass: "text-ink-body",
  },
  auto_review: {
    icon: Bot,
    visible: false,
    triggerClass:
      "text-blue-600 hover:bg-blue-50 dark:text-blue-300 dark:hover:bg-blue-500/10 dark:hover:text-blue-200",
    iconClass: "text-blue-600 dark:text-blue-300",
  },
  full_access: {
    icon: AlertCircle,
    visible: true,
    triggerClass:
      "text-orange-600 hover:bg-surface-soft dark:text-orange-300 dark:hover:bg-surface-soft dark:hover:text-orange-200",
    iconClass: "text-orange-600 dark:text-orange-300",
  },
};

// Per-effort metadata for the reasoning-budget selector (kernel V5+bba3014
// ``ModelSettings.effort``). Icons step up alongside the budget:
// Gauge for low/medium/high (mechanical metaphor), Flame for xhigh/max
// (heavy reasoning). ``null`` reuses the trigger to render "Default"
// (SDK default — let the runtime pick).
type EffortLevel = "low" | "medium" | "high" | "xhigh" | "max";

const EFFORT_META: Record<
  EffortLevel,
  {
    icon: typeof Gauge;
    triggerClass: string;
    iconClass: string;
  }
> = {
  low: {
    icon: Gauge,
    triggerClass: "text-ink-body hover:bg-surface-soft hover:text-ink-heading",
    iconClass: "text-ink-body",
  },
  medium: {
    icon: Gauge,
    triggerClass: "text-ink-body hover:bg-surface-soft hover:text-ink-heading",
    iconClass: "text-ink-body",
  },
  high: {
    icon: Gauge,
    triggerClass: "text-violet-600 hover:bg-violet-50",
    iconClass: "text-violet-600",
  },
  xhigh: {
    icon: Flame,
    triggerClass: "text-rose-600 hover:bg-rose-50",
    iconClass: "text-rose-600",
  },
  max: {
    icon: Flame,
    triggerClass: "text-rose-700 hover:bg-rose-50",
    iconClass: "text-rose-700",
  },
};

// Effort dropdown options — no ``null``/"Default" slot. A null prop is
// coerced to ``EFFORT_FALLBACK`` for display so legacy sessions with
// ``model_settings.effort = null`` still render a concrete level.
const EFFORT_ORDER: readonly EffortLevel[] = [
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
] as const;

const EFFORT_FALLBACK: EffortLevel = "high";
import { MAX_SESSION_ATTACHMENTS, modelLabel } from "@valuz/shared";
import { AttachmentMenu } from "./conversation/AttachmentMenu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "./ui/tooltip";
import {
  SkillSearchMenu,
  type SkillSearchItem,
} from "./conversation/SkillSearchMenu";
import { cn } from "../lib/cn";
import { useI18n } from "../hooks/use-i18n";

const SKILL_CHIP_ATTR = "data-skill-slug";

const isImeCompositionEvent = (e: React.KeyboardEvent<HTMLElement>): boolean =>
  e.nativeEvent.isComposing || e.keyCode === 229;

/** Walk the contenteditable's children and serialise to a plain string,
 * mapping each chip back to its ``/slug`` token. The serialised form is
 * what the parent gets via ``onChange`` and ultimately sends to the
 * backend, so the model sees normal slash mentions interleaved with
 * the user's prose. */
const serializeEditor = (root: HTMLElement): string => {
  let out = "";
  root.childNodes.forEach((node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent ?? "";
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const el = node as HTMLElement;
      const slug = el.getAttribute(SKILL_CHIP_ATTR);
      if (slug) {
        out += `/${slug}`;
      } else if (el.tagName === "BR") {
        out += "\n";
      } else {
        out += el.textContent ?? "";
      }
    }
  });
  return out;
};

/** Build the DOM node for a single inline skill chip. Uses
 * ``contenteditable=false`` so the cursor treats it as an atomic unit
 * (Backspace at the chip's right edge deletes the whole chip in one
 * keystroke). The styling mirrors the previous above-the-input chip. */
const buildChipNode = (skill: SkillSearchItem): HTMLElement => {
  const slug = (
    skill.slug || skill.name.toLowerCase().replace(/\s+/g, "-")
  ).trim();
  const chip = document.createElement("span");
  chip.setAttribute(SKILL_CHIP_ATTR, slug);
  chip.contentEditable = "false";
  chip.dataset.skillName = skill.name;
  chip.className =
    "mr-0.5 inline-flex items-center gap-1 rounded-full bg-[#f3f2ff] px-2 py-0.5 text-2xs text-brand align-middle select-none";
  chip.innerHTML = `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg><span>${skill.name}</span>`;
  return chip;
};

export interface ModelSelectorItem {
  providerId: string;
  providerName: string;
  modelId: string;
  isDefault: boolean;
  source?: string;
}

/**
 * Runtime Agent option shown in the picker (REP-107). Mirrors the
 * relevant subset of ``RuntimeListItem`` from
 * ``@valuz/core/runtimes-api`` — duplicated here so ``@valuz/ui``
 * stays free of cross-package runtime imports.
 */
export interface RuntimeSelectorItem {
  /** Stable wire id (``claude_agent`` / ``codex`` / ``deepagents``). */
  id: string;
  /** User-facing label (e.g. "Claude Agent"). */
  displayName: string;
  /** False = grey out + show ``unavailableReason`` on hover. */
  available: boolean;
  /** Tooltip text when ``available`` is false. */
  unavailableReason?: string | null;
}

export interface ComposerAgentItem {
  /** Workspace-local agent handle (the ``agent_slug``). */
  slug: string;
  /** Display name. */
  name: string;
  /** Runtime display label (e.g. "Claude Agent"). */
  runtimeLabel: string;
  /** Model id / label shown as subtext. */
  modelLabel: string;
}

export interface ComposerProjectItem {
  /** Workspace id (project). */
  id: string;
  /** Display name. */
  name: string;
  /** 派驻 member count, shown as dropdown subtext. */
  memberCount?: number;
  /** Optional one-line description. */
  description?: string;
}

export interface ComposerProps {
  value?: string;
  onChange?: (value: string) => void;
  onSend?: () => void;
  /** Skills available for / and @ triggers */
  skills?: SkillSearchItem[];
  /** Called when user selects a skill from the popup */
  onSkillSelect?: (skill: SkillSearchItem) => void;
  /** Called when user picks local files. Receives the accepted File list
   *  (clamped to the remaining attachment slots). */
  onLocalUpload?: (files: File[]) => void;
  /** Called when user picks knowledge base file */
  onKBPick?: () => void;
  /**
   * Upload-on-attach mode. When true the composer does NOT keep its own
   * not-yet-uploaded ``File[]`` queue: picking / dropping a file fires
   * ``onLocalUpload`` / ``onFileDrop`` and the host uploads it immediately
   * (showing parse progress via ``pinnedAttachments``). When false (default)
   * the composer queues files locally and the host uploads them at send time.
   */
  uploadOnAttach?: boolean;
  /**
   * Count of attachments already persisted on the session (local
   * uploads + KB-sourced references). Added to the composer's own
   * not-yet-uploaded queue to enforce the session-wide
   * ``MAX_SESSION_ATTACHMENTS`` cap: once the combined count hits
   * the cap the attachment menu greys out, the file-picker click is
   * a no-op, and drag-drop clamps to the remaining slots. Defaults
   * to 0 for callers (e.g. the project-home composer) that attach
   * before a session exists.
   */
  existingAttachmentCount?: number;
  /**
   * Already-persisted session attachments to surface as chips in the
   * composer's attachment row — same visual slot as not-yet-uploaded
   * local files. Used for KB-sourced picks so the user sees the file
   * they attached right in the input box (and can remove it there),
   * not just in the side panel. ``onRemovePinnedAttachment`` deletes
   * the underlying attachment row.
   */
  pinnedAttachments?: {
    id: string;
    name: string;
    /** Async parse status — drives the inline progress indicator on the
     *  chip ("解析中" spinner while ``parsing``, error tint on ``failed``). */
    parseStatus?: "parsing" | "ready" | "failed";
    /** ``local`` upload vs ``kb_doc`` live reference — drives the chip icon. */
    sourceKind?: "local" | "kb_doc";
  }[];
  onRemovePinnedAttachment?: (id: string) => void;
  /** Called when files are dropped onto the composer */
  onFileDrop?: (files: File[]) => void;
  /** Called when attachments list changes (added/removed) */
  onAttachmentsChange?: (files: File[]) => void;
  /** Available model options for selection */
  providers?: ModelSelectorItem[];
  /** Currently selected provider id (null = default) */
  selectedProviderId?: string | null;
  /** Currently selected model id (null = provider default) */
  selectedModelId?: string | null;
  /** Called when user picks a different model */
  onModelChange?: (providerId: string | null, modelId: string | null) => void;
  /**
   * Available Runtime Agents for the picker. Empty / undefined hides
   * the runtime selector (back-compat for callers that haven't wired
   * up ``useRuntimes`` yet).
   */
  runtimes?: RuntimeSelectorItem[];
  /** Currently selected runtime id (null = let backend auto-derive). */
  selectedRuntimeId?: string | null;
  /** Called when user picks a different runtime. */
  onRuntimeChange?: (runtimeId: string | null) => void;
  /**
   * Cross-runtime approval mode (kernel V5+d008b53). Live-reconcile
   * since V5+bba3014 — changing this mid-session applies on the next
   * Send (Claude live ``set_permission_mode`` mutator + fork-on-bypass;
   * Codex per-turn approval/sandbox kwargs; DeepAgents graph rebuild).
   * Hide the picker when ``permissionMode`` is undefined (back-compat).
   * DeepAgents runtime greys out ``auto_review`` — only the Claude
   * tier ships the LLM classifier.
   */
  permissionMode?: "default" | "auto_review" | "full_access" | null;
  /** Called when the user picks a different permission mode. */
  onPermissionModeChange?: (
    mode: "default" | "auto_review" | "full_access",
  ) => void;
  /**
   * When ``true``, the picker renders the current value but is
   * read-only. Pre-bba3014 this was set on live sessions because the
   * Claude runtime captured ``permission_mode`` at ``_build_options``
   * time and never re-read it. The kernel V5+bba3014 upgrade
   * live-reconciles the mode on next Send, so callers can leave this
   * ``false`` for live sessions too — but the prop is kept for legacy
   * UI surfaces that still need a read-only display.
   */
  permissionModeLocked?: boolean;
  /**
   * Reasoning-effort budget (kernel V5+bba3014 ``ModelSettings.effort``).
   * Live-reconcile: PATCH applies on next Send. Hide the picker when
   * ``effort`` is undefined (back-compat).
   */
  effort?: "low" | "medium" | "high" | "xhigh" | "max" | null;
  /** Called when the user picks a different effort value. ``null`` resets
   *  to the SDK default. */
  onEffortChange?: (
    effort: "low" | "medium" | "high" | "xhigh" | "max" | null,
  ) => void;
  /** Lock model + runtime selectors after first message (unlocked after 3 consecutive failures) */
  modelLocked?: boolean;
  /**
   * Agent-selector mode (project conversations). When provided (even as
   * an empty array) the runtime/model/effort controls are replaced by a
   * single Agent dropdown — the session inherits runtime/model/provider/
   * effort/skills/connectors from the chosen project agent. ``undefined``
   * keeps the classic model picker (quick chats).
   */
  agents?: ComposerAgentItem[];
  /** Currently selected project agent slug (null = none picked yet). */
  selectedAgentSlug?: string | null;
  /** Called when the user picks a different project agent. */
  onAgentChange?: (slug: string) => void;
  /** Read-only display once a session exists (agent frozen at creation). */
  agentLocked?: boolean;
  /** Project options for the 📁 chip. When provided (even empty) the chip
   *  renders. ``undefined`` hides it (callers not yet wired). */
  projects?: ComposerProjectItem[];
  /** Currently selected workspace. ``null`` = 临时对话 (chat-default); a
   *  project id otherwise. */
  selectedWorkspaceId?: string | null;
  /** Called when the user switches project/临时. ``null`` => 临时. */
  onWorkspaceChange?: (id: string | null) => void;
  /** Read-only display once a session exists (workspace frozen at creation,
   *  ADR-006). */
  workspaceLocked?: boolean;
  /** Entry point to create/add an agent to the project. */
  onAddAgent?: () => void;
  /** Disable the send button regardless of content (e.g. no agent picked). */
  sendDisabled?: boolean;
  /**
   * Mode toggle for the unified composer (PRD-PAAT §3.2).
   *
   * ``chat`` (default) — submit creates a regular Session and routes the
   *    text to the orchestrator.
   * ``task`` — submit kicks off a Task: the text becomes the goal, the
   *    selected agent becomes the lead, the lead runs in the background
   *    and the page navigates to the task detail view.
   *
   * When ``onModeChange`` is omitted the toggle is hidden (callers that
   * don't host both flows — like the conversation page — leave it off).
   */
  mode?: "chat" | "task";
  onModeChange?: (mode: "chat" | "task") => void;
  /** Auto-focus the textarea on mount */
  autoFocus?: boolean;
  /**
   * True while a turn is in flight. Flips the send button into a
   * stop button — clicking it calls ``onStop`` instead of ``onSend``.
   */
  sending?: boolean;
  /** Called when the user clicks the stop button (only while sending). */
  onStop?: () => void;
  /**
   * Show the inline skill (``/``) picker button. Hidden in project composers
   * where skills are configured per-agent, not picked inline. Default true.
   */
  showSkillButton?: boolean;
  /** Optional class override for the outer composer wrapper. */
  wrapperClassName?: string;
}

export const Composer = ({
  value: controlledValue,
  onChange,
  onSend,
  skills = [],
  onSkillSelect,
  onLocalUpload,
  onKBPick,
  uploadOnAttach = false,
  existingAttachmentCount = 0,
  pinnedAttachments = [],
  onRemovePinnedAttachment,
  onFileDrop,
  onAttachmentsChange,
  providers = [],
  selectedProviderId,
  selectedModelId,
  onModelChange,
  runtimes = [],
  selectedRuntimeId,
  onRuntimeChange,
  permissionMode,
  onPermissionModeChange,
  permissionModeLocked = false,
  effort,
  onEffortChange,
  modelLocked = false,
  agents,
  selectedAgentSlug,
  onAgentChange,
  agentLocked = false,
  projects,
  selectedWorkspaceId,
  onWorkspaceChange,
  workspaceLocked = false,
  onAddAgent,
  sendDisabled = false,
  mode = "chat",
  onModeChange,
  autoFocus = false,
  sending = false,
  onStop,
  showSkillButton = true,
  wrapperClassName,
}: ComposerProps) => {
  // Toolbar dropdowns flip direction based on where the composer sits in the
  // viewport: top half → open downward (room below), bottom half → open
  // upward. Recomputed on resize/scroll via rAF; ``setMenuDir`` bails when the
  // direction is unchanged, so scrolling doesn't thrash renders.
  const composerBoxRef = useRef<HTMLDivElement>(null);
  const [menuDir, setMenuDir] = useState<"up" | "down">("up");
  useEffect(() => {
    let raf = 0;
    const update = () => {
      const el = composerBoxRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setMenuDir(r.top + r.height / 2 < window.innerHeight / 2 ? "down" : "up");
    };
    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        update();
      });
    };
    schedule();
    window.addEventListener("resize", schedule);
    window.addEventListener("scroll", schedule, true);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener("resize", schedule);
      window.removeEventListener("scroll", schedule, true);
    };
  }, []);
  const menuVClass = menuDir === "down" ? "top-full mt-1" : "bottom-full mb-1";
  const { t } = useI18n();
  const [internalValue, setInternalValue] = useState("");
  const isControlled = controlledValue !== undefined;
  const currentValue = isControlled ? controlledValue : internalValue;

  const [attachOpen, setAttachOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [agentOpen, setAgentOpen] = useState(false);
  const agentRef = useRef<HTMLDivElement>(null);
  const [projectOpen, setProjectOpen] = useState(false);
  const projectRef = useRef<HTMLDivElement>(null);
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const [permissionOpen, setPermissionOpen] = useState(false);
  // Effort flyout submenu inside the merged model+effort popover.
  const [effortSubOpen, setEffortSubOpen] = useState(false);
  const runtimeRef = useRef<HTMLDivElement>(null);
  const modelRef = useRef<HTMLDivElement>(null);
  const permissionRef = useRef<HTMLDivElement>(null);
  const [skillSearch, setSkillSearch] = useState<{
    active: boolean;
    query: string;
  }>({ active: false, query: "" });
  const [attachments, setAttachments] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);

  const editorRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Tracks the most recent serialised value emitted by the editor's
  // own input event. When the controlled ``value`` prop matches it, we
  // know the editor's DOM is already in sync and skip the destructive
  // ``innerHTML = ...`` reseed (which would blow away cursor + chips).
  const lastEmittedRef = useRef<string>("");

  const setValue = (next: string) => {
    lastEmittedRef.current = next;
    if (isControlled) onChange?.(next);
    else setInternalValue(next);
  };

  const updateAttachments = useCallback(
    (files: File[]) => {
      setAttachments(files);
      onAttachmentsChange?.(files);
    },
    [onAttachmentsChange],
  );

  // Editor input handler — runs on every keystroke / IME composition
  // / paste. Re-serialises the contenteditable into the value string
  // and re-evaluates the ``/`` skill trigger and ``@`` KB trigger on
  // the trailing text. Triggers are only honoured when they sit
  // standalone (start of line or after whitespace) to avoid eating
  // characters in the middle of words / email addresses.
  const handleEditorInput = () => {
    const root = editorRef.current;
    if (!root) return;
    const next = serializeEditor(root);

    // ``@`` standalone ⇒ KB picker. Consume the @ before pushing
    // value upward.
    if (next.length > currentValue.length && next.endsWith("@") && onKBPick) {
      const before = next.charAt(next.length - 2);
      if (before === "" || /\s/.test(before)) {
        // Strip the @ from the editor DOM (delete the last text char).
        const sel = window.getSelection();
        if (sel && sel.rangeCount > 0) {
          const range = sel.getRangeAt(0);
          // The @ is the character immediately before the caret.
          range.setStart(
            range.startContainer,
            Math.max(0, range.startOffset - 1),
          );
          range.deleteContents();
        }
        setValue(serializeEditor(root));
        setSkillSearch({ active: false, query: "" });
        onKBPick();
        return;
      }
    }

    setValue(next);

    // ``/`` standalone (start / after whitespace) opens skill picker.
    // Inline ``/`` (like ``http://`` or ``a/b``) is ignored.
    //
    // ``、`` (U+3001, Chinese enumeration mark) is treated as an
    // alias for ``/`` because Chinese IMEs default a slash keystroke
    // to this character and it's a constant friction point.
    const tail = next.slice(-1);
    if (tail === "/" || tail === "、") {
      const before = next.charAt(next.length - 2);
      if (before === "" || /\s/.test(before)) {
        setSkillSearch({ active: true, query: "" });
        return;
      }
    }
    if (skillSearch.active) {
      const triggerIdx = Math.max(
        next.lastIndexOf("/"),
        next.lastIndexOf("、"),
      );
      if (triggerIdx === -1 || triggerIdx < next.length - 20) {
        setSkillSearch({ active: false, query: "" });
      } else {
        setSkillSearch({ active: true, query: next.slice(triggerIdx + 1) });
      }
    }
  };

  // Paste as plain text. A contenteditable otherwise keeps the source
  // page's inline styles (colors, fonts, bold). Insert the clipboard's
  // text/plain at the caret as a single text node so ``white-space:
  // pre-wrap`` preserves newlines and ``serializeEditor`` round-trips
  // them; then re-run the input handler to sync value + ``/``/``@`` triggers.
  const handlePaste = (e: React.ClipboardEvent<HTMLDivElement>) => {
    e.preventDefault();
    const text = e.clipboardData.getData("text/plain");
    if (!text) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    range.deleteContents();
    const node = document.createTextNode(text);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
    handleEditorInput();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (skillSearch.active) return;
    if (e.key === "Enter" && !e.shiftKey && !isImeCompositionEvent(e)) {
      e.preventDefault();
      handleSend();
    }
    // Backspace-to-delete-chip: when the caret is collapsed at the
    // start of a text node OR at the very start of the editor and the
    // previous sibling is a chip span, remove the chip in one
    // keystroke. Default browser behaviour selects the chip on the
    // first Backspace and only deletes it on the second.
    //
    // We also collapse "chip + trailing whitespace" into one Backspace
    // — the trailing space is one we inserted ourselves at chip
    // creation time for caret rhythm, and dragging it out as a
    // separate keystroke surprises users who expect deleting the chip
    // to clear the slot entirely (and have the placeholder come back).
    if (e.key === "Backspace") {
      const root = editorRef.current;
      if (!root) return;
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || !sel.isCollapsed) return;
      const range = sel.getRangeAt(0);
      const { startContainer, startOffset } = range;
      // Walk leftward from the caret. We're hunting for a chip — and
      // we want to drag any whitespace sitting between the caret and
      // that chip along with it, so deleting the chip clears the whole
      // slot in one keystroke (and the placeholder can come back).
      //
      // The whitespace we erase comes from two places:
      //   1. ``leadingWhitespaceLen`` — whitespace inside the caret's
      //      own text node (caret at end of " " right after chip).
      //   2. ``whitespaceNodes`` — whole sibling text nodes that are
      //      pure whitespace (caret at root after ``[chip][" "]``,
      //      which is exactly where ``setStartAfter(space)`` parks
      //      the caret on insert).
      let prev: ChildNode | null = null;
      let leadingWhitespaceLen = 0;
      let scanForChip = true;
      if (startContainer === root) {
        prev = root.childNodes[startOffset - 1] ?? null;
      } else if (startContainer.nodeType === Node.TEXT_NODE) {
        const left = (startContainer.textContent ?? "").slice(0, startOffset);
        if (startOffset === 0) {
          let node: Node | null = startContainer;
          while (node && node !== root && !node.previousSibling) {
            node = node.parentNode;
          }
          prev = node && node !== root ? node.previousSibling : null;
        } else if (/^\s+$/.test(left)) {
          leadingWhitespaceLen = startOffset;
          prev = startContainer.previousSibling;
        } else {
          scanForChip = false;
        }
      } else {
        scanForChip = false;
      }
      if (scanForChip) {
        const whitespaceNodes: ChildNode[] = [];
        while (
          prev &&
          prev.nodeType === Node.TEXT_NODE &&
          /^\s*$/.test(prev.textContent ?? "")
        ) {
          whitespaceNodes.push(prev);
          prev = prev.previousSibling;
        }
        if (
          prev &&
          prev.nodeType === Node.ELEMENT_NODE &&
          (prev as HTMLElement).hasAttribute(SKILL_CHIP_ATTR)
        ) {
          e.preventDefault();
          if (
            leadingWhitespaceLen > 0 &&
            startContainer.nodeType === Node.TEXT_NODE
          ) {
            const r = document.createRange();
            r.setStart(startContainer, 0);
            r.setEnd(startContainer, leadingWhitespaceLen);
            r.deleteContents();
          }
          for (const n of whitespaceNodes) n.parentNode?.removeChild(n);
          prev.parentNode?.removeChild(prev);
          setValue(serializeEditor(root));
        }
      }
    }
  };

  const handleSend = () => {
    onSend?.();
    setAttachments([]);
    onAttachmentsChange?.([]);
  };

  // Insert a chip at the caret. If the caret immediately follows a
  // ``/`` trigger character (the user typed ``/`` to open the picker),
  // delete that ``/`` first so the chip cleanly replaces it. Add a
  // single trailing non-breaking space so the caret lands a real
  // typing position to the right of the chip — without it the caret
  // sits inside the chip's contenteditable=false zone and the next
  // keystroke does nothing visible.
  const handleSkillSelect = useCallback(
    (skill: SkillSearchItem) => {
      const root = editorRef.current;
      setSkillSearch({ active: false, query: "" });
      if (!root) {
        onSkillSelect?.(skill);
        return;
      }
      root.focus();
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) {
        // No caret — append at the end.
        root.appendChild(buildChipNode(skill));
        root.appendChild(document.createTextNode(" "));
      } else {
        const range = sel.getRangeAt(0);
        // Strip the trigger (``/`` or its CN-IME equivalent ``、``) AND
        // any partial query the user typed to filter the picker
        // (``/stock-``, ``、find``, etc.). Match the trigger followed
        // by zero or more slug-character chars at the very end of the
        // text up to the caret; that's exactly the window the picker
        // was filtering.
        if (range.startContainer.nodeType === Node.TEXT_NODE) {
          const text = range.startContainer.textContent ?? "";
          const sliced = text.slice(0, range.startOffset);
          const m = sliced.match(/[/、][a-zA-Z0-9_-]*$/);
          if (m) {
            range.setStart(
              range.startContainer,
              range.startOffset - m[0].length,
            );
            range.deleteContents();
          }
        }
        const chip = buildChipNode(skill);
        range.insertNode(chip);
        const space = document.createTextNode(" ");
        chip.parentNode?.insertBefore(space, chip.nextSibling);
        // Place caret right after the trailing space.
        range.setStartAfter(space);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
      }
      setValue(serializeEditor(root));
      onSkillSelect?.(skill);
    },
    [onSkillSelect],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer.types.includes("Files")) {
      setDragOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (
      containerRef.current &&
      !containerRef.current.contains(e.relatedTarget as Node)
    ) {
      setDragOver(false);
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
      const files = Array.from(e.dataTransfer.files);
      if (files.length === 0) return;
      // Clamp to the session-wide cap. ``existingAttachmentCount`` is
      // the server-persisted count; ``attachments`` is this composer's
      // not-yet-uploaded queue. Anything past the cap is silently
      // dropped — the menu is already greyed in this state, drag-drop
      // is the one path that can still over-fill.
      const slotsLeft =
        MAX_SESSION_ATTACHMENTS - existingAttachmentCount - attachments.length;
      if (slotsLeft <= 0) return;
      const accepted = files.slice(0, slotsLeft);
      // Upload-on-attach: hand the host the File list and let it upload +
      // track parse progress; don't keep a local queue (would double-render).
      if (uploadOnAttach) {
        onFileDrop?.(accepted);
        return;
      }
      updateAttachments([...attachments, ...accepted]);
      onFileDrop?.(accepted);
    },
    [
      attachments,
      existingAttachmentCount,
      onFileDrop,
      updateAttachments,
      uploadOnAttach,
    ],
  );

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;
      const slotsLeft =
        MAX_SESSION_ATTACHMENTS - existingAttachmentCount - attachments.length;
      if (slotsLeft > 0) {
        const accepted = Array.from(files).slice(0, slotsLeft);
        if (uploadOnAttach) {
          onLocalUpload?.(accepted);
        } else {
          updateAttachments([...attachments, ...accepted]);
          onLocalUpload?.(accepted);
        }
      }
      e.target.value = "";
    },
    [
      attachments,
      existingAttachmentCount,
      onLocalUpload,
      updateAttachments,
      uploadOnAttach,
    ],
  );

  const handleRemoveAttachment = useCallback(
    (index: number) => {
      const next = attachments.filter((_, i) => i !== index);
      updateAttachments(next);
    },
    [attachments, updateAttachments],
  );

  // ``autoFocus`` for contenteditable — the attribute alone doesn't
  // work, so we focus on mount when the prop is set. Only runs once.
  useEffect(() => {
    if (autoFocus) editorRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync the contenteditable's DOM with the controlled ``value`` prop
  // when the parent reseeds it (e.g. ``setDraft("")`` after send) — but
  // only when the prop actually diverges from the editor's own last
  // emit, so we don't blow away the live cursor + chip state on every
  // keystroke. Chips embedded in the value string are re-materialised
  // by splitting on ``/slug`` tokens; each token between two slashes
  // becomes a chip whose name we recover from the active skill list.
  useEffect(() => {
    const root = editorRef.current;
    if (!root) return;
    if (controlledValue === undefined) return;
    if (controlledValue === lastEmittedRef.current) return;
    root.innerHTML = "";
    if (controlledValue) {
      // Greedy parse: match either a slash-token or a run of plain text.
      const re = /\/([a-zA-Z0-9_-]+)|([^/]+|\/)/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(controlledValue)) !== null) {
        if (m[1]) {
          const slug = m[1];
          const matched = skills.find(
            (s) => (s.slug || s.name.toLowerCase()) === slug,
          );
          root.appendChild(
            buildChipNode(
              matched ?? {
                id: slug,
                name: slug,
                slug,
                description: "",
              },
            ),
          );
        } else if (m[2]) {
          root.appendChild(document.createTextNode(m[2]));
        }
      }
    }
    lastEmittedRef.current = controlledValue;
  }, [controlledValue, skills]);

  useEffect(() => {
    if (!modelOpen) return;
    const handler = (e: MouseEvent) => {
      if (modelRef.current && !modelRef.current.contains(e.target as Node)) {
        setModelOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [modelOpen]);

  useEffect(() => {
    if (!agentOpen) return;
    const handler = (e: MouseEvent) => {
      if (agentRef.current && !agentRef.current.contains(e.target as Node)) {
        setAgentOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [agentOpen]);

  useEffect(() => {
    if (!projectOpen) return;
    const handler = (e: MouseEvent) => {
      if (
        projectRef.current &&
        !projectRef.current.contains(e.target as Node)
      ) {
        setProjectOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [projectOpen]);

  useEffect(() => {
    if (!runtimeOpen) return;
    const handler = (e: MouseEvent) => {
      if (
        runtimeRef.current &&
        !runtimeRef.current.contains(e.target as Node)
      ) {
        setRuntimeOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [runtimeOpen]);

  useEffect(() => {
    if (!permissionOpen) return;
    const handler = (e: MouseEvent) => {
      if (
        permissionRef.current &&
        !permissionRef.current.contains(e.target as Node)
      ) {
        setPermissionOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [permissionOpen]);

  // Collapse the effort flyout whenever the model popover closes so it
  // doesn't reopen pre-expanded next time.
  useEffect(() => {
    if (!modelOpen) setEffortSubOpen(false);
  }, [modelOpen]);

  // Runtime is a required choice — the picker doesn't expose a "default"
  // placeholder. When ``selectedRuntimeId`` is null the host hasn't
  // settled on one yet (e.g. before ``useRuntimes`` resolves); fall back
  // to the first available runtime's label so the trigger still reads
  // sensibly.
  const selectedRuntimeLabel =
    (selectedRuntimeId
      ? runtimes.find((r) => r.id === selectedRuntimeId)?.displayName
      : null) ??
    runtimes.find((r) => r.available)?.displayName ??
    runtimes[0]?.displayName ??
    "Runtime";

  // ADR-013 — visible labels for the 3-state permission selector.
  // ``auto_review`` is greyed out when the host has selected the
  // ``deepagents`` runtime (only Claude tier ships the LLM classifier).
  const PERMISSION_LABELS: Record<
    "default" | "auto_review" | "full_access",
    { label: string; hint: string }
  > = {
    default: {
      label: t("conversation.permissionReview"),
      hint: t("conversation.permissionReviewHint"),
    },
    auto_review: {
      label: t("conversation.permissionAuto"),
      hint: t("conversation.permissionAutoHint"),
    },
    full_access: {
      label: t("conversation.permissionFull"),
      hint: t("conversation.permissionFullHint"),
    },
  };
  const effectivePermissionMode: "default" | "auto_review" | "full_access" =
    permissionMode ?? "full_access";
  const selectedPermissionLabel =
    PERMISSION_LABELS[effectivePermissionMode].label;
  const isDeepAgents = selectedRuntimeId === "deepagents";

  // EFFORT_LABELS — visible labels for the reasoning-budget selector
  // (kernel V5+bba3014 ``ModelSettings.effort``). No "Default" slot:
  // a ``null`` prop (legacy session row) coerces to ``EFFORT_FALLBACK``
  // so the trigger always shows a concrete level. ``effort.hint``
  // ("下次发送生效") is now rendered once as the popover's footer
  // instead of repeating under every item.
  const EFFORT_LABELS: Record<EffortLevel, { label: string }> = {
    low: { label: t("effort.low" as Parameters<typeof t>[0]) },
    medium: { label: t("effort.medium" as Parameters<typeof t>[0]) },
    high: { label: t("effort.high" as Parameters<typeof t>[0]) },
    xhigh: { label: t("effort.xhigh" as Parameters<typeof t>[0]) },
    max: { label: t("effort.max" as Parameters<typeof t>[0]) },
  };
  const effortKey: EffortLevel = effort ?? EFFORT_FALLBACK;
  const selectedEffortLabel = EFFORT_LABELS[effortKey].label;

  // Agent-selector mode (project conversations). When ``agents`` is
  // provided the runtime/model/effort controls collapse into one Agent
  // dropdown — the session inherits its model identity from the agent.
  const agentMode = agents !== undefined;
  const selectedAgent =
    agents?.find((a) => a.slug === selectedAgentSlug) ?? null;

  // Project-selector mode (📁 chip). When ``projects`` is provided the chip
  // renders; ``selectedWorkspaceId == null`` means 临时对话 (chat-default).
  const projectMode = projects !== undefined;
  const selectedProject =
    selectedWorkspaceId == null
      ? null
      : (projects?.find((p) => p.id === selectedWorkspaceId) ?? null);
  const projectTriggerLabel = selectedProject
    ? selectedProject.name
    : t("conversation.tempChat" as Parameters<typeof t>[0]);

  // Model is a required choice — the picker doesn't expose a "default
  // model" placeholder. When ``selectedModelId`` is null (e.g. before
  // the host has resolved its first auto-pick), fall back to the
  // dropdown's own default-marked entry, then to the first provider/model
  // pair so the trigger reads sensibly. The host is expected to settle
  // selectedProviderId/selectedModelId to a real value before the user
  // sends a turn — there is no "send with no model" path anymore.
  const selectedModelLabel =
    (selectedModelId
      ? (() => {
          const m = providers.find(
            (c) =>
              c.providerId === selectedProviderId &&
              c.modelId === selectedModelId,
          );
          if (!m) return null;
          return m.source === "managed"
            ? m.providerName
            : modelLabel(m.modelId);
        })()
      : null) ??
    (() => {
      const d = providers.find((c) => c.isDefault);
      return d
        ? d.source === "managed"
          ? d.providerName
          : modelLabel(d.modelId)
        : null;
    })() ??
    (() => {
      const f = providers[0];
      return f
        ? f.source === "managed"
          ? f.providerName
          : modelLabel(f.modelId)
        : null;
    })() ??
    "Model";

  const hasContent =
    currentValue || attachments.length > 0 || pinnedAttachments.length > 0;

  // Session-wide attachment budget. ``existingAttachmentCount`` is the
  // server-persisted count (local + KB rows); ``attachments`` is this
  // composer's not-yet-uploaded queue. When the combined count hits
  // the cap, the attachment menu greys both entries out.
  const atAttachmentLimit =
    existingAttachmentCount + attachments.length >= MAX_SESSION_ATTACHMENTS;

  return (
    <div
      ref={containerRef}
      // Spec 5.6 外层 padding 10px 20px 16px
      className={cn("relative shrink-0 px-5 pt-2.5 pb-4", wrapperClassName)}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleFileInputChange}
      />

      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center rounded-xl bg-surface/80 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-brand/40 bg-brand-light/30 px-10 py-8">
            <div className="text-sm font-medium text-brand">
              {t("conversation.dragToUpload")}
            </div>
            <div className="text-2xs text-ink-meta">
              {t("conversation.supportedFormats")}
            </div>
          </div>
        </div>
      )}

      {/* Spec 5.6 内层容器：1px border + radius 10px + padding 12px 8px 8px
          PRD-PAAT §3.2 task mode: switch to accent tinting (border + soft
          gradient bg) so the user gets an obvious visual cue that "send"
          here will spawn a background task, not a chat message. */}
      <div
        ref={composerBoxRef}
        className={cn(
          "@container/composer mx-auto max-w-[760px] rounded-xl border px-2 pt-3 pb-2 transition-colors duration-[120ms]",
          dragOver
            ? "border-brand/50"
            : mode === "task"
              ? "border-[#725cf9] bg-surface"
              : "border-surface-border bg-surface",
        )}
        style={
          mode === "task" && !dragOver
            ? {
                backgroundImage:
                  "linear-gradient(to bottom, rgba(114, 92, 249, 0.08) 0%, rgba(114, 92, 249, 0.03) 42%, rgba(114, 92, 249, 0) 68%)",
              }
            : undefined
        }
      >
        <div>
          {/* Skill tag — the chip itself is the remove affordance.
              The previous above-the-input chip is gone — chips now
              live INLINE inside the contenteditable below, so the
              user can type around them ("/research-skill 帮我分析
              ..."), insert multiple chips, and delete them with a
              single Backspace. */}

          {/* File attachments — one row mixing two sources:
              ``pinnedAttachments`` are already-persisted session
              attachments (KB picks; Database glyph), ``attachments``
              are the not-yet-uploaded local files (Paperclip glyph).
              Both render as the same chip so a KB pick reads the
              same way a local upload does in the input box. */}
          {(pinnedAttachments.length > 0 || attachments.length > 0) && (
            <div className="mb-3 flex flex-wrap items-center gap-1.5">
              {pinnedAttachments.map((doc) => {
                const isKb = doc.sourceKind === "kb_doc";
                const isParsing = doc.parseStatus === "parsing";
                const isFailed = doc.parseStatus === "failed";
                const ChipIcon = isParsing
                  ? Loader2
                  : isKb
                    ? Database
                    : Paperclip;
                return (
                  <span
                    key={`pinned-${doc.id}`}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-2xs",
                      isFailed
                        ? "border-error/30 bg-error-light text-error-text"
                        : isKb
                          ? "border-brand/25 bg-brand-light text-brand"
                          : "border-surface-border bg-surface-soft text-ink-body",
                    )}
                  >
                    <ChipIcon
                      className={cn(
                        "h-3 w-3 shrink-0",
                        isParsing && "animate-spin",
                      )}
                    />
                    <span className="max-w-[140px] truncate">{doc.name}</span>
                    {isParsing ? (
                      <span className="shrink-0 text-ink-meta">
                        {t("conversation.attachmentParsing")}
                      </span>
                    ) : isFailed ? (
                      <span className="shrink-0">{t("common.failed")}</span>
                    ) : null}
                    {onRemovePinnedAttachment ? (
                      <button
                        type="button"
                        onClick={() => onRemovePinnedAttachment(doc.id)}
                        className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full text-ink-meta transition-colors hover:bg-surface-border hover:text-ink-body"
                      >
                        <X className="h-2.5 w-2.5" />
                      </button>
                    ) : null}
                  </span>
                );
              })}
              {attachments.map((file, index) => (
                <span
                  key={`${file.name}-${index}`}
                  className="inline-flex items-center gap-1.5 rounded-full border border-surface-border bg-surface-soft px-2.5 py-1 text-2xs text-ink-body"
                >
                  <Paperclip className="h-3 w-3 shrink-0" />
                  <span className="max-w-[140px] truncate">{file.name}</span>
                  <button
                    type="button"
                    onClick={() => handleRemoveAttachment(index)}
                    className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full text-ink-meta transition-colors hover:bg-surface-border hover:text-ink-body"
                  >
                    <X className="h-2.5 w-2.5" />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="relative">
            {/* Contenteditable replaces the old textarea so skill
                tokens can render as inline chips next to the user's
                prose. ``data-empty`` is toggled from React based on
                the serialised value (NOT the CSS ``:empty`` selector)
                because contenteditable usually leaves a stray ``<br>``
                after the user deletes everything via Backspace —
                which makes the element technically non-empty but
                visually blank. Driving the placeholder off the
                serialised string handles that case. */}
            <div
              ref={editorRef}
              role="textbox"
              contentEditable
              suppressContentEditableWarning
              data-placeholder={t(
                (mode === "task"
                  ? "composer.taskPlaceholder"
                  : "conversation.inputPlaceholder") as Parameters<typeof t>[0],
              )}
              data-empty={currentValue ? undefined : ""}
              className={cn(
                "min-h-[44px] max-h-[180px] w-full overflow-y-auto whitespace-pre-wrap break-words bg-transparent px-2 text-[13px] leading-[1.55] text-ink-heading focus:outline-none",
                "data-[empty]:before:pointer-events-none data-[empty]:before:text-ink-body data-[empty]:before:content-[attr(data-placeholder)]",
              )}
              onInput={handleEditorInput}
              onPaste={handlePaste}
              onKeyDown={handleKeyDown}
            />
            {showSkillButton && skillSearch.active && skills.length > 0 && (
              <SkillSearchMenu
                skills={skills}
                query={skillSearch.query}
                onSelect={handleSkillSelect}
                onClose={() => setSkillSearch({ active: false, query: "" })}
              />
            )}
          </div>
        </div>

        {/* Spec 5.6 action bar: icon buttons 28×28, rounded-8 hover #F7F8FA.
            No own ``px-2`` — the outer composer card already has ``px-2``;
            stacking another layer pushed the leftmost icon further from
            the card frame than the send button on the right. Dropping
            the duplicate keeps both icons at the same 8 px inset from
            the card frame. */}
        <div className="flex items-center justify-between pt-1">
          <div className="flex items-center gap-1.5">
            {/* PRD-PAAT §3.2 unified composer: ``[Chat | Task]`` mode
                toggle. Only rendered when the host wires ``onModeChange``
                so the conversation page (chat-only) stays unaffected. */}
            {onModeChange && (
              <div className="flex h-7 items-center rounded-lg bg-surface-soft p-0.5 text-xs">
                {(["chat", "task"] as const).map((m) => {
                  const active = mode === m;
                  return (
                    <button
                      key={m}
                      type="button"
                      onClick={() => {
                        if (!active) onModeChange(m);
                      }}
                      className={cn(
                        "flex h-6 items-center rounded-md px-2 font-medium transition-colors duration-[120ms]",
                        active
                          ? m === "task"
                            ? "bg-[#725cf9] text-white shadow-sm"
                            : "bg-card text-ink-heading shadow-sm"
                          : "text-ink-body hover:text-ink-heading",
                      )}
                    >
                      {t(
                        (m === "task"
                          ? "composer.modeTask"
                          : "composer.modeChat") as Parameters<typeof t>[0],
                      )}
                    </button>
                  );
                })}
              </div>
            )}
            <div className="relative">
              <TooltipProvider delayDuration={150}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      className="flex h-7 w-7 items-center justify-center rounded-lg text-ink-body transition-colors duration-[120ms] hover:bg-surface-soft hover:text-ink-heading"
                      onClick={() => setAttachOpen(!attachOpen)}
                    >
                      <Paperclip
                        className="h-[15px] w-[15px]"
                        strokeWidth={1.9}
                      />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    {t("conversation.addAttachment")}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              {attachOpen && (
                <AttachmentMenu
                  onLocalUpload={() => {
                    setAttachOpen(false);
                    fileInputRef.current?.click();
                  }}
                  onKnowledgeBasePick={onKBPick}
                  onClose={() => setAttachOpen(false)}
                  disabled={atAttachmentLimit}
                  disabledHint={t("conversation.attachmentLimitReached", {
                    max: String(MAX_SESSION_ATTACHMENTS),
                  })}
                />
              )}
            </div>
            {/* Skill picker — hidden in project composers, where skills are
                configured per-agent rather than picked inline. Surfaces the
                ``/`` slash menu as a button when shown. */}
            {showSkillButton && (
              <TooltipProvider delayDuration={150}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-label="Skill"
                      className={cn(
                        "flex h-7 w-7 items-center justify-center rounded-lg transition-colors duration-[120ms]",
                        skillSearch.active
                          ? "bg-surface-soft text-ink-heading"
                          : "text-ink-body hover:bg-surface-soft hover:text-ink-heading",
                      )}
                      onClick={() => {
                        setSkillSearch((prev) =>
                          prev.active
                            ? { active: false, query: "" }
                            : { active: true, query: "" },
                        );
                        editorRef.current?.focus();
                      }}
                    >
                      <Zap className="h-[15px] w-[15px]" strokeWidth={1.9} />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">Skill</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
            {/* ADR-013/014 cross-runtime approval mode picker. Visible
                when the host passes ``permissionMode`` (back-compat: when
                undefined the picker is hidden). Mode is frozen at session
                creation per ADR-006 — once the session is live the kernel
                runtime caches ``permission_mode`` at ``_build_options``
                time and never re-reads it, so the picker on live sessions
                is rendered read-only via ``permissionModeLocked``.
                ``auto_review`` greys out on DeepAgents (no LLM
                classifier ships there). Rendered on the left cluster
                (next to attachment + skill icons) so the right cluster
                stays a clean runtime/model/send strip. */}
            {permissionMode !== undefined && permissionMode !== null && (
              <div className="relative" ref={permissionRef}>
                {(() => {
                  const meta = PERMISSION_META[effectivePermissionMode];
                  const TriggerIcon = meta.icon;
                  const trigger = (
                    <button
                      type="button"
                      className={cn(
                        "inline-flex h-7 items-center gap-1 rounded-lg px-2 text-xs leading-none transition-colors duration-[120ms]",
                        meta.triggerClass,
                        permissionOpen && "bg-surface-soft",
                        permissionModeLocked &&
                          "cursor-not-allowed opacity-60 hover:bg-transparent",
                      )}
                      onClick={() => {
                        if (permissionModeLocked) return;
                        setPermissionOpen((v) => !v);
                      }}
                      aria-disabled={permissionModeLocked}
                    >
                      <TriggerIcon className="block h-3 w-3 shrink-0" />
                      <span className="max-w-[140px] truncate leading-none">
                        {selectedPermissionLabel}
                      </span>
                      {permissionModeLocked ? (
                        <Lock className="block h-3 w-3 shrink-0 opacity-70" />
                      ) : (
                        <ChevronDown className="block h-3 w-3 shrink-0" />
                      )}
                    </button>
                  );
                  if (!permissionModeLocked) return trigger;
                  return (
                    <TooltipProvider delayDuration={150}>
                      <Tooltip>
                        <TooltipTrigger asChild>{trigger}</TooltipTrigger>
                        <TooltipContent side="top">
                          {t("conversation.permissionLockedHint")}
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  );
                })()}
                {permissionOpen && !permissionModeLocked && (
                  <div
                    className={cn(
                      "group/permission-menu absolute left-0 z-50 max-h-[320px] min-w-[260px] overflow-y-auto rounded-lg border border-surface-border bg-surface p-1 shadow-lg",
                      menuVClass,
                    )}
                  >
                    {(["default", "auto_review", "full_access"] as const)
                      // ``auto_review`` is intentionally hidden in this
                      // iteration (Claude classifier not shipped yet).
                      // Keep the array typed as the full 3-state union
                      // so the active session — if persisted with
                      // ``auto_review`` — still renders in the trigger.
                      .filter((mode) => PERMISSION_META[mode].visible)
                      .map((mode) => {
                        const selected = effectivePermissionMode === mode;
                        const disabled = mode === "auto_review" && isDeepAgents;
                        const item = PERMISSION_LABELS[mode];
                        const meta = PERMISSION_META[mode];
                        const ItemIcon = meta.icon;
                        const button = (
                          <button
                            key={mode}
                            type="button"
                            disabled={disabled}
                            className={cn(
                              "flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors",
                              disabled
                                ? "cursor-not-allowed text-ink-muted"
                                : "text-ink-heading hover:bg-surface-muted",
                              selected &&
                                !disabled &&
                                "bg-surface-muted group-hover/permission-menu:bg-transparent hover:!bg-surface-muted",
                            )}
                            onClick={() => {
                              if (disabled) return;
                              onPermissionModeChange?.(mode);
                              setPermissionOpen(false);
                            }}
                          >
                            <ItemIcon
                              className={cn(
                                "mt-0.5 block h-3.5 w-3.5 shrink-0",
                                meta.iconClass,
                              )}
                            />
                            <span className="flex min-w-0 flex-1 flex-col">
                              <span className="truncate text-[14px] leading-[18px]">
                                {item.label}
                              </span>
                              <span className="truncate text-[11px] leading-[15px] text-ink-meta">
                                {item.hint}
                              </span>
                            </span>
                            {selected && !disabled && (
                              <Check className="mt-0.5 block h-3.5 w-3.5 shrink-0 text-ink-heading" />
                            )}
                          </button>
                        );
                        if (disabled) {
                          return (
                            <TooltipProvider key={mode}>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <span className="block">{button}</span>
                                </TooltipTrigger>
                                <TooltipContent side="right">
                                  {t("conversation.deepAgentsNoAutoReview")}
                                </TooltipContent>
                              </Tooltip>
                            </TooltipProvider>
                          );
                        }
                        return button;
                      })}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            {/* 09-assistant 📁 project chip — switches the conversation
                between 临时对话 (chat-default) and a project workspace.
                Sits immediately before the 🤖 agent chip so the two read
                "📁 🤖" left-to-right. Frozen once a session exists
                (``workspaceLocked``, ADR-006). */}
            {projectMode && (
              <div className="relative" ref={projectRef}>
                <button
                  type="button"
                  className={cn(
                    "flex h-7 items-center gap-1 rounded-lg px-2 text-xs transition-colors duration-[120ms]",
                    workspaceLocked
                      ? "cursor-not-allowed text-ink-muted"
                      : "text-ink-body hover:bg-surface-soft hover:text-ink-heading",
                    projectOpen &&
                      !workspaceLocked &&
                      "bg-surface-soft text-ink-heading",
                  )}
                  onClick={() => {
                    if (!workspaceLocked) setProjectOpen((v) => !v);
                  }}
                >
                  <FolderClosed className="h-3 w-3 shrink-0" />
                  <span className="max-w-[160px] truncate">
                    {projectTriggerLabel}
                  </span>
                  {workspaceLocked ? (
                    <Lock className="h-3 w-3 shrink-0 opacity-70" />
                  ) : (
                    <ChevronDown className="h-3 w-3 shrink-0" />
                  )}
                </button>
                {projectOpen && !workspaceLocked && (
                  <div
                    className={cn(
                      "absolute left-0 z-50 min-w-[260px] rounded-lg border border-surface-border bg-surface shadow-lg",
                      menuVClass,
                    )}
                  >
                    <div className="max-h-[320px] overflow-y-auto p-1">
                      {/* 临时对话 row — selected when selectedWorkspaceId == null */}
                      <button
                        type="button"
                        onClick={() => {
                          onWorkspaceChange?.(null);
                          setProjectOpen(false);
                        }}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-surface-muted",
                          selectedWorkspaceId == null && "bg-surface-muted",
                        )}
                      >
                        <span className="flex min-w-0 flex-1 flex-col">
                          <span className="truncate text-[14px] text-ink-heading">
                            {t(
                              "conversation.tempChat" as Parameters<
                                typeof t
                              >[0],
                            )}
                          </span>
                          <span className="truncate text-2xs text-ink-meta">
                            {t(
                              "conversation.tempChatHint" as Parameters<
                                typeof t
                              >[0],
                            )}
                          </span>
                        </span>
                        {selectedWorkspaceId == null && (
                          <Check className="h-3.5 w-3.5 shrink-0 text-ink-heading" />
                        )}
                      </button>
                      {projects && projects.length > 0 && (
                        <div className="my-1 h-px bg-surface-border" />
                      )}
                      {projects?.map((p) => {
                        const sel = p.id === selectedWorkspaceId;
                        return (
                          <button
                            key={p.id}
                            type="button"
                            onClick={() => {
                              onWorkspaceChange?.(p.id);
                              setProjectOpen(false);
                            }}
                            className={cn(
                              "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-surface-muted",
                              sel && "bg-surface-muted",
                            )}
                          >
                            <span className="flex min-w-0 flex-1 flex-col">
                              <span className="truncate text-[14px] text-ink-heading">
                                {p.name}
                              </span>
                              {(p.memberCount != null || p.description) && (
                                <span className="truncate text-2xs text-ink-meta">
                                  {p.description ??
                                    t(
                                      "conversation.projectMemberCount" as Parameters<
                                        typeof t
                                      >[0],
                                      { count: String(p.memberCount) },
                                    )}
                                </span>
                              )}
                            </span>
                            {sel && (
                              <Check className="h-3.5 w-3.5 shrink-0 text-ink-heading" />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
            {/* Project conversations: a single Agent selector replaces the
                runtime/model/effort controls. The session inherits its
                model identity from the chosen project agent. */}
            {agentMode && (
              <div className="relative" ref={agentRef}>
                {(() => {
                  const canOpen = !agentLocked;
                  // When locked to a session whose bound agent is no longer in
                  // the candidate list (e.g. the library agent was deleted
                  // after the session was created), fall back to the raw slug
                  // rather than the "select an agent" placeholder.
                  const triggerLabel = selectedAgent
                    ? selectedAgent.name
                    : agentLocked && selectedAgentSlug
                      ? selectedAgentSlug
                      : t(
                          "conversation.selectAgent" as Parameters<typeof t>[0],
                        );
                  return (
                    <>
                      <button
                        type="button"
                        className={cn(
                          "flex h-7 items-center gap-1 rounded-lg px-2 text-xs transition-colors duration-[120ms]",
                          canOpen
                            ? "text-ink-body hover:bg-surface-soft hover:text-ink-heading"
                            : "cursor-not-allowed text-ink-muted",
                          agentOpen &&
                            canOpen &&
                            "bg-surface-soft text-ink-heading",
                        )}
                        onClick={() => {
                          if (canOpen) setAgentOpen((v) => !v);
                        }}
                      >
                        <span className="inline-flex min-w-0 items-center gap-1">
                          {/* PRD-PAAT §3.2 task mode: surface the chosen
                              agent as the task's lead with an inline
                              accent "Lead / 主 Agent" badge — same vocab
                              the task detail page uses (TaskContextPanel
                              + sub-sidebar). */}
                          {mode === "task" && (
                            <span className="inline-flex h-4 shrink-0 items-center rounded-[4px] bg-[#725cf9]/10 px-1 text-[10px] leading-none font-normal text-[#725cf9]">
                              {t("task.runLead" as Parameters<typeof t>[0])}
                            </span>
                          )}
                          <span className="max-w-[240px] truncate text-ink-heading leading-none">
                            {triggerLabel}
                          </span>
                          {!agentLocked && (
                            <ChevronDown className="h-3 w-3 shrink-0" />
                          )}
                        </span>
                      </button>
                      {agentOpen && !agentLocked && (
                        <div
                          className={cn(
                            "absolute right-0 z-50 min-w-[260px] rounded-lg border border-surface-border bg-surface shadow-lg",
                            menuVClass,
                          )}
                        >
                          <div className="max-h-[300px] overflow-y-auto p-1">
                            {agents && agents.length > 0 ? (
                              agents.map((a) => {
                                const selected = a.slug === selectedAgentSlug;
                                return (
                                  <button
                                    key={a.slug}
                                    type="button"
                                    className={cn(
                                      "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-surface-muted",
                                      selected && "bg-surface-muted",
                                    )}
                                    onClick={() => {
                                      onAgentChange?.(a.slug);
                                      setAgentOpen(false);
                                    }}
                                  >
                                    <span className="flex min-w-0 flex-1 flex-col">
                                      <span className="truncate text-[14px] text-ink-heading">
                                        {a.name}
                                      </span>
                                      <span className="truncate text-2xs text-ink-meta">
                                        {a.runtimeLabel} · {a.modelLabel}
                                      </span>
                                    </span>
                                    {selected && (
                                      <Check className="h-3.5 w-3.5 shrink-0 text-ink-heading" />
                                    )}
                                  </button>
                                );
                              })
                            ) : (
                              <div className="px-2 py-3 text-center text-xs text-ink-meta">
                                {t(
                                  "conversation.noAgents" as Parameters<
                                    typeof t
                                  >[0],
                                )}
                              </div>
                            )}
                          </div>
                          {onAddAgent && (
                            <div className="border-t border-surface-border p-1">
                              <button
                                type="button"
                                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[14px] text-brand transition-colors hover:bg-surface-muted"
                                onClick={() => {
                                  setAgentOpen(false);
                                  onAddAgent();
                                }}
                              >
                                <Plus className="h-3.5 w-3.5 shrink-0" />
                                <span className="truncate">
                                  {t(
                                    "conversation.addAgent" as Parameters<
                                      typeof t
                                    >[0],
                                  )}
                                </span>
                              </button>
                            </div>
                          )}
                        </div>
                      )}
                    </>
                  );
                })()}
              </div>
            )}
            {/* REP-107 Runtime selector — sits ahead of the model picker.
                Hidden when the host hasn't passed a runtime list (callers
                that haven't wired up ``useRuntimes`` yet). */}
            {!agentMode && runtimes.length > 0 && (
              <div className="relative" ref={runtimeRef}>
                {(() => {
                  const trigger = (
                    <button
                      type="button"
                      className={cn(
                        "flex h-7 items-center gap-1 rounded-lg px-2 text-xs transition-colors duration-[120ms]",
                        modelLocked
                          ? "cursor-not-allowed text-ink-muted"
                          : "text-ink-body hover:bg-surface-soft hover:text-ink-heading",
                        runtimeOpen &&
                          !modelLocked &&
                          "bg-surface-soft text-ink-heading",
                      )}
                      onClick={() => {
                        if (!modelLocked) setRuntimeOpen((v) => !v);
                      }}
                    >
                      <span className="max-w-[140px] truncate">
                        {selectedRuntimeLabel}
                      </span>
                      <ChevronDown className="h-3 w-3 shrink-0" />
                    </button>
                  );
                  if (modelLocked) {
                    return (
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="inline-flex">{trigger}</span>
                          </TooltipTrigger>
                          <TooltipContent side="top">
                            {t("conversation.runtimeLocked")}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    );
                  }
                  return trigger;
                })()}
                {runtimeOpen && (
                  <div
                    className={cn(
                      "group/runtime-menu absolute right-0 z-50 max-h-[320px] min-w-[220px] overflow-y-auto rounded-lg border border-surface-border bg-surface p-1 shadow-lg",
                      menuVClass,
                    )}
                  >
                    <div className="px-2 py-0.5 text-2xs text-ink-meta">
                      {t("cron.runtime" as Parameters<typeof t>[0])}
                    </div>
                    {runtimes.map((rt) => {
                      const selected = selectedRuntimeId === rt.id;
                      const disabled = !rt.available;
                      const button = (
                        <button
                          key={rt.id}
                          type="button"
                          disabled={disabled}
                          className={cn(
                            "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[14px] transition-colors",
                            disabled
                              ? "cursor-not-allowed text-ink-muted"
                              : "text-ink-heading hover:bg-surface-muted",
                            selected &&
                              !disabled &&
                              "bg-surface-muted group-hover/runtime-menu:bg-transparent hover:!bg-surface-muted",
                          )}
                          onClick={() => {
                            if (disabled) return;
                            // Switching runtime invalidates the previous
                            // model choice (different provider pool); clear
                            // it so the host can re-pick a default.
                            onRuntimeChange?.(rt.id);
                            onModelChange?.(null, null);
                            setRuntimeOpen(false);
                          }}
                        >
                          <span className="flex min-w-0 flex-1 items-center">
                            <span className="truncate">{rt.displayName}</span>
                          </span>
                          {selected && !disabled && (
                            <Check className="h-3.5 w-3.5 shrink-0 text-ink-heading" />
                          )}
                        </button>
                      );
                      if (disabled && rt.unavailableReason) {
                        return (
                          <TooltipProvider key={rt.id}>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="block">{button}</span>
                              </TooltipTrigger>
                              <TooltipContent side="left">
                                {rt.unavailableReason}
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        );
                      }
                      return button;
                    })}
                  </div>
                )}
              </div>
            )}
            {/* Spec 5.6 模型切换：h-28 / px-8 / radius 8 / 12px */}
            {!agentMode && providers.length > 0 && (
              <div className="relative" ref={modelRef}>
                {(() => {
                  // Merged model + effort control. The popover holds the
                  // model list AND an effort segmented row, so the trigger
                  // shows ``model · effort``. When the session model is
                  // locked (ADR-006) the popover can still open — effort is
                  // live-reconciled, not creation-frozen, so it stays
                  // editable; only the model list is disabled inside.
                  const canOpen = !modelLocked || effort !== undefined;
                  const trigger = (
                    <button
                      type="button"
                      className={cn(
                        "flex h-7 items-center gap-1 rounded-lg px-2 text-xs transition-colors duration-[120ms]",
                        canOpen
                          ? "text-ink-body hover:bg-surface-soft hover:text-ink-heading"
                          : "cursor-not-allowed text-ink-muted",
                        modelOpen &&
                          canOpen &&
                          "bg-surface-soft text-ink-heading",
                      )}
                      onClick={() => {
                        if (canOpen) setModelOpen((v) => !v);
                      }}
                    >
                      <span className="max-w-[220px] truncate">
                        {/* Model name always uses the heading (hover)
                            color for emphasis; the ``· effort`` part
                            keeps the trigger's default body color. */}
                        <span className="text-ink-heading">
                          {selectedModelLabel}
                        </span>
                        {effort !== undefined && ` · ${selectedEffortLabel}`}
                      </span>
                      <ChevronDown className="h-3 w-3 shrink-0" />
                    </button>
                  );
                  // Wrap in a tooltip when locked so users discover why the
                  // model row is disabled — V5 freezes session.model at
                  // creation (ADR-006), so any change there would be a no-op.
                  if (modelLocked) {
                    return (
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="inline-flex">{trigger}</span>
                          </TooltipTrigger>
                          <TooltipContent side="top">
                            {t("conversation.modelLocked")}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    );
                  }
                  return trigger;
                })()}
                {modelOpen && (
                  // Outer shell has NO overflow so the effort flyout
                  // (positioned left of the popover) isn't clipped. Only
                  // the model list scrolls.
                  <div
                    className={cn(
                      "group/model-menu absolute right-0 z-50 min-w-[220px] rounded-lg border border-surface-border bg-surface shadow-lg",
                      menuVClass,
                    )}
                  >
                    <div className="max-h-[300px] overflow-y-auto p-1">
                      {(() => {
                        const groups = new Map<string, ModelSelectorItem[]>();
                        for (const ch of providers) {
                          const list = groups.get(ch.providerName) ?? [];
                          list.push(ch);
                          groups.set(ch.providerName, list);
                        }
                        return Array.from(groups.entries()).map(
                          ([groupName, items], groupIdx) => (
                            // Symmetric ``py-1.5`` on the title itself
                            // (same rhythm as the item rows). Group
                            // separation comes from ``mt-1`` on the
                            // group wrapper — skipped for the first
                            // group so the popover top isn't padded.
                            <div
                              key={groupName}
                              className={cn(groupIdx > 0 && "mt-1")}
                            >
                              <div className="px-2 py-0.5 text-2xs text-ink-meta">
                                {groupName}
                              </div>
                              {items.map((item) =>
                                (() => {
                                  const selected =
                                    selectedProviderId === item.providerId &&
                                    selectedModelId === item.modelId;

                                  return (
                                    <button
                                      key={`${item.providerId}-${item.modelId}`}
                                      type="button"
                                      disabled={modelLocked}
                                      className={cn(
                                        "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[14px] text-ink-heading transition-colors hover:bg-surface-muted",
                                        selected &&
                                          "bg-surface-muted group-hover/model-menu:bg-transparent hover:!bg-surface-muted",
                                        modelLocked &&
                                          "cursor-not-allowed opacity-50 hover:bg-transparent",
                                      )}
                                      onClick={() => {
                                        if (modelLocked) return;
                                        onModelChange?.(
                                          item.providerId,
                                          item.modelId,
                                        );
                                        setModelOpen(false);
                                      }}
                                    >
                                      <span className="flex min-w-0 flex-1 items-center">
                                        <span className="truncate">
                                          {item.source === "managed"
                                            ? item.providerName
                                            : modelLabel(item.modelId)}
                                        </span>
                                        {item.isDefault && (
                                          <span className="ml-1 shrink-0 rounded bg-brand/10 px-1 text-2xs text-brand">
                                            {t("conversation.modelDefault")}
                                          </span>
                                        )}
                                      </span>
                                      {selected && (
                                        <Check className="h-3.5 w-3.5 shrink-0 text-ink-heading" />
                                      )}
                                    </button>
                                  );
                                })(),
                              )}
                            </div>
                          ),
                        );
                      })()}
                    </div>
                    {/* Effort → flyout submenu. 5 levels don't fit
                        horizontally, so it's a nested menu (ChatGPT-
                        style, but with effort in the submenu instead of
                        the model). The trigger still shows
                        ``model · effort``. Effort isn't creation-locked
                        so this stays usable even when the model list
                        above is disabled. Outside the scroll area so the
                        flyout isn't clipped. */}
                    {effort !== undefined && (
                      <div
                        className="relative border-t border-surface-border p-1"
                        onMouseEnter={() => setEffortSubOpen(true)}
                        onMouseLeave={() => setEffortSubOpen(false)}
                      >
                        <button
                          type="button"
                          className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[14px] text-ink-heading transition-colors hover:bg-surface-muted"
                          onClick={() => setEffortSubOpen(true)}
                        >
                          <span className="min-w-0 flex-1 truncate">
                            {t("effort.label" as Parameters<typeof t>[0])}
                          </span>
                          <span className="shrink-0 text-2xs text-ink-meta">
                            {selectedEffortLabel}
                          </span>
                          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
                        </button>
                        {effortSubOpen && (
                          <div className="absolute bottom-0 right-full z-50 max-h-[320px] min-w-[180px] overflow-y-auto rounded-lg border border-surface-border bg-surface p-1 shadow-lg">
                            {EFFORT_ORDER.map((level) => {
                              const sel = effortKey === level;
                              const ItemIcon = EFFORT_META[level].icon;
                              const itemIconClass =
                                EFFORT_META[level].iconClass;
                              return (
                                <button
                                  key={level}
                                  type="button"
                                  className={cn(
                                    "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[14px] text-ink-heading transition-colors hover:bg-surface-muted",
                                    sel && "bg-surface-muted",
                                  )}
                                  onClick={() => {
                                    onEffortChange?.(level);
                                    setEffortSubOpen(false);
                                  }}
                                >
                                  <ItemIcon
                                    className={cn(
                                      "h-3.5 w-3.5 shrink-0",
                                      itemIconClass,
                                    )}
                                  />
                                  <span className="min-w-0 flex-1 truncate leading-[18px]">
                                    {EFFORT_LABELS[level].label}
                                  </span>
                                  {sel && (
                                    <Check className="h-3.5 w-3.5 shrink-0 text-ink-heading" />
                                  )}
                                </button>
                              );
                            })}
                            <div className="mt-1 border-t border-surface-border px-2 pt-1.5 pb-0.5 text-[11px] text-ink-meta">
                              {t("effort.hint" as Parameters<typeof t>[0])}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
            {/* Spec 5.6 发送按钮: 28×28 / radius 8 / accent bg / arrow 13px.
                Doubles as the stop button while a turn is in flight —
                clicking it routes to ``onStop`` (the page maps to its
                interrupt handler). */}
            {sending ? (
              <button
                type="button"
                className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand text-white transition-opacity duration-[120ms] hover:opacity-90"
                onClick={() => onStop?.()}
                title={t("conversation.stop")}
                aria-label={t("conversation.stop")}
              >
                <Square
                  className="h-[11px] w-[11px] fill-current"
                  strokeWidth={0}
                />
              </button>
            ) : mode === "task" ? (
              // Task mode submit. Labelled accent pill ``⚡ Launch task`` when
              // the composer is wide (≥500px); collapses to the compact 28×28
              // ``⚡`` icon button when narrower so the toolbar never overflows.
              // Always the ``⚡`` icon + accent colour so it reads as "spawn a
              // background task" rather than a plain chat send.
              <button
                type="button"
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-lg transition-opacity duration-[120ms] hover:opacity-90",
                  "@min-[500px]/composer:w-auto @min-[500px]/composer:gap-1 @min-[500px]/composer:px-2 @min-[500px]/composer:text-xs @min-[500px]/composer:font-medium",
                  hasContent && !sendDisabled
                    ? "bg-[#725cf9] text-white"
                    : "bg-[#725cf9]/40 text-white/70",
                )}
                onClick={handleSend}
                disabled={!hasContent || sendDisabled}
                title={t("composer.sendTask" as Parameters<typeof t>[0])}
                aria-label={t("composer.sendTask" as Parameters<typeof t>[0])}
              >
                <Zap className="h-[13px] w-[13px]" strokeWidth={2} />
                <span className="hidden @min-[500px]/composer:inline">
                  {t("composer.sendTask" as Parameters<typeof t>[0])}
                </span>
              </button>
            ) : (
              <button
                type="button"
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-lg transition-opacity duration-[120ms] hover:opacity-90",
                  hasContent && !sendDisabled
                    ? "bg-brand text-white"
                    : "bg-brand/40 text-white/60",
                )}
                onClick={handleSend}
                disabled={!hasContent || sendDisabled}
                title={t("conversation.send")}
                aria-label={t("conversation.send")}
              >
                <ArrowUp className="h-[13px] w-[13px]" strokeWidth={2} />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* PRD-PAAT §3.2 task-mode hint card — sits below the composer
          and explains the lead-Agent ownership model in one line so
          the user knows why ``send`` here behaves differently. */}
      {mode === "task" && (
        <div className="mx-auto mt-2 flex max-w-[760px] items-center justify-center gap-2 px-3 py-1.5 text-center text-2xs text-ink-meta">
          <Zap className="h-3 w-3 shrink-0 text-ink-muted" strokeWidth={2} />
          <span>{t("composer.taskHint" as Parameters<typeof t>[0])}</span>
        </div>
      )}
    </div>
  );
};
