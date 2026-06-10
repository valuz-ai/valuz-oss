import {
  Check,
  Code,
  Copy,
  Eye,
  FolderOpen,
  Loader2,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";
import { Badge } from "../ui/badge";
import { MarkdownContent } from "../conversation/MarkdownContent";
import { ProjectFileTree, type FileTreeNode } from "../project/ProjectFileTree";
import { getSkillIconStyle } from "./skill-icon-style";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";

export interface SkillDetailPanelFile {
  path: string;
  type: "file" | "directory";
  size?: number | null;
  /** Nested children for directory nodes — mirrors the backend tree
   * shape from ``_build_skill_file_tree``. Pass the raw response
   * directly; the panel walks it recursively to render the structure. */
  children?: SkillDetailPanelFile[];
}

export interface SkillDetailPanelProps {
  skill: {
    name: string;
    description: string;
    tags: string[];
    source: "official" | "custom";
    /**
     * Display string version. Pass "v3" for SKILL.md frontmatter version=3,
     * or "–" when unknown. Used in the header subtitle.
     */
    version: string;
    /** Human-readable origin label — "Built-in" / "~/.claude/skills/" /
     * "官方仓库". Currently unused in the rendered subtitle (``category``
     * carries the bucket label and ``path`` carries the location); kept
     * on the type so callers don't have to drop it. */
    originLabel?: string;
    /** Filesystem path (already abbreviated to ``~`` by the caller).
     * Rendered as the "位置" segment of the header subtitle. */
    path?: string;
    /** Source bucket — drives the "来源" segment. Mirrors the
     * skill management page's four-group taxonomy
     * (kernel-upgrade-cozy-rose plan, display order
     * official → agents → claude → codex):
     *   builtin  → 内置 (skill_label="Built-in" frontmatter marker)
     *   official → 官方 (scope=official)
     *   agents   → .agents (source=valuz, Valuz canonical root)
     *   claude   → .claude (source=claude, ~/.claude/skills/)
     *   codex    → .codex (source=codex, ~/.codex/skills/)
     */
    category?: "builtin" | "official" | "agents" | "claude" | "codex";
  };
  /**
   * Real file tree from `/v1/skills/{id}/files`. Empty array means "no
   * additional files beyond SKILL.md" — render a single-line tree.
   * undefined means files haven't loaded yet (skeleton state).
   */
  files?: SkillDetailPanelFile[];
  /**
   * Loads UTF-8 contents of a file path (relative to the skill dir).
   * Used to drive the inline preview pane. Required for clicking a file
   * to do anything; absent → tree is read-only.
   */
  onLoadFile?: (path: string) => Promise<string>;
  onDelete?: () => void;
  onCopy?: () => void;
  /** Reveal the skill directory in the OS file manager (Finder on macOS,
   * Explorer on Windows). When provided, a folder icon button is
   * rendered to the left of the delete icon. */
  onOpenInFinder?: () => void;
}

/** Directories before files, each group alphabetical (case-insensitive).
 * No special-casing for SKILL.md — the auto-select effect picks it up
 * by name so the rendered order stays a pure A→Z sort. */
const sortNodes = (nodes: SkillDetailPanelFile[]): SkillDetailPanelFile[] =>
  [...nodes].sort((a, b) => {
    if (a.type !== b.type) return a.type === "directory" ? -1 : 1;
    return a.path.localeCompare(b.path, undefined, { sensitivity: "base" });
  });

/** Depth-first walk yielding only file nodes — used by the auto-select
 * effect to pick the initial selection (first file, SKILL.md preferred
 * via the sort). */
const collectFiles = (
  nodes: SkillDetailPanelFile[],
): SkillDetailPanelFile[] => {
  const out: SkillDetailPanelFile[] = [];
  for (const n of sortNodes(nodes)) {
    if (n.type === "file") out.push(n);
    else if (n.children) out.push(...collectFiles(n.children));
  }
  return out;
};

/** Convert the backend skill-file-tree shape into the ``FileTreeNode``
 * shape consumed by ``ProjectFileTree``. The two trees carry the same
 * data; only the type-tag and field names differ
 * (``directory`` ↔ ``folder``; no explicit ``name`` on the source). */
const toFileTreeNodes = (nodes: SkillDetailPanelFile[]): FileTreeNode[] =>
  sortNodes(nodes).map((n) => ({
    name: n.path.split("/").pop() ?? n.path,
    path: n.path,
    type: n.type === "directory" ? "folder" : "file",
    children: n.children ? toFileTreeNodes(n.children) : undefined,
  }));

/** SKILL.md (and other authoring conventions) lead with a YAML
 * frontmatter block delimited by ``---``. Streamdown renders that as a
 * horizontal rule + setext heading, which looks broken — the keys
 * become a giant H2 and the values disappear. Detect the leading
 * frontmatter and rewrite it as a fenced ``yaml`` code block so the
 * markdown renderer's syntax highlighting handles it cleanly. */
const transformFrontmatter = (content: string): string => {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (!match) return content;
  const yaml = match[1];
  const rest = content.slice(match[0].length);
  return "```yaml\n" + yaml + "\n```\n\n" + rest;
};

/** Wrap a string in a fenced code block, choosing a backtick fence
 * length that's strictly longer than any backtick run inside the
 * content. CommonMark allows fences of arbitrary length ≥ 3 as long as
 * the closing fence matches; this lets us safely wrap markdown source
 * that itself contains ``` blocks without the inner fence prematurely
 * closing the outer one (which would re-enter markdown parsing and
 * render the rest as HTML). */
const wrapInCodeFence = (content: string, language: string): string => {
  const runs = content.match(/`+/g) ?? [];
  const longest = runs.reduce((max, r) => Math.max(max, r.length), 0);
  const fenceLen = Math.max(3, longest + 1);
  const fence = "`".repeat(fenceLen);
  return `${fence}${language}\n${content}\n${fence}`;
};

/** Render Markdown source through Shiki (so ``#`` headings, links,
 * inline code, bold etc. all keep their syntax-highlight colours), then
 * post-process the rendered DOM to force the fenced-code regions
 * (delimiters + body lines) to a single green colour.
 *
 * Shiki's markdown grammar tokenises the fence delimiters and the
 * embedded code separately; getting both to share a colour cleanly
 * requires a custom theme. Walking the rendered ``<span class='line'>``
 * elements and overriding inline ``style.color`` is much smaller than
 * shipping a one-off theme, and it leaves every other token alone. */
const FENCE_GREEN = "#22863A";

const MarkdownSourceView = ({ content }: { content: string }) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;
    // Shiki wraps each line in ``<span class="line">`` inside the pre.
    const lineSpans = root.querySelectorAll<HTMLSpanElement>("span.line");
    if (lineSpans.length === 0) return;
    let inFence = false;
    let fenceMarker = "";
    lineSpans.forEach((line) => {
      const text = line.textContent ?? "";
      const trimmed = text.replace(/^\s+/, "");
      const fenceOpen = !inFence && /^`{3,}/.test(trimmed);
      const fenceClose =
        inFence &&
        fenceMarker !== "" &&
        trimmed.startsWith(fenceMarker) &&
        /^`+\s*$/.test(trimmed);
      const recolour = inFence || fenceOpen;
      if (fenceOpen) {
        fenceMarker = trimmed.match(/^`+/)?.[0] ?? "```";
        inFence = true;
      } else if (fenceClose) {
        inFence = false;
        fenceMarker = "";
      }
      if (recolour) {
        line.style.color = FENCE_GREEN;
        line
          .querySelectorAll<HTMLSpanElement>("span")
          .forEach((s) => (s.style.color = FENCE_GREEN));
      }
    });
  }, [content]);

  return (
    <div
      ref={containerRef}
      className={cn(
        "text-xs",
        // Flatten Streamdown's code-block chrome — we only want the
        // tokenised text, not its card / header / padding.
        "[&_[data-streamdown='code-block-header']]:hidden",
        "[&_[data-streamdown='code-block']]:border-0",
        "[&_[data-streamdown='code-block']]:rounded-none",
        "[&_[data-streamdown='code-block']]:bg-transparent",
        "[&_[data-streamdown='code-block']]:shadow-none",
        "[&_[data-streamdown='code-block-body']]:border-0",
        "[&_[data-streamdown='code-block-body']]:bg-transparent",
        // Force padding to 0 with ``!`` important — MarkdownContent's
        // global ``[&_[data-streamdown='code-block-body']]:px-4 / pt-2
        // / pb-4`` overrides share the same Tailwind specificity and
        // would otherwise win since they sit deeper in the cascade.
        "[&_[data-streamdown='code-block-body']]:!py-0",
        "[&_[data-streamdown='code-block-body']]:!px-0",
        "[&_[data-language]]:!py-0",
        "[&_[data-language]]:!px-0",
        "[&_pre]:m-0",
        // Auto-wrap long lines instead of horizontal scrolling — long
        // markdown body lines (URLs, paragraphs without breaks) would
        // otherwise force the whole panel into an x-scroll, which
        // doesn't read well in a sidecar preview.
        "[&_pre]:whitespace-pre-wrap",
        "[&_pre]:break-words",
        "[&_[data-streamdown='code-block-body']]:overflow-x-visible",
        "[&_[data-language]]:overflow-x-visible",
        // Hide Streamdown's floating copy-button capsule — the source
        // view has no equivalent toolbar, and the global
        // ``MarkdownContent`` rule sets ``display: flex !important`` on
        // it so plain ``hidden`` doesn't win.
        "[&_[data-streamdown='code-block']>div:has(>[data-streamdown='code-block-actions'])]:!hidden",
      )}
    >
      <MarkdownContent content={wrapInCodeFence(content, "markdown")} />
    </div>
  );
};

/** Map a file extension to the Shiki / Streamdown language identifier
 * used for syntax highlighting. ``null`` means "no highlighting" — the
 * source view falls back to a plain ``<pre>``. */
const languageForPath = (path: string): string | null => {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  switch (ext) {
    case "py":
      return "python";
    case "ts":
    case "tsx":
      return "typescript";
    case "js":
    case "jsx":
      return "javascript";
    case "json":
      return "json";
    case "yaml":
    case "yml":
      return "yaml";
    case "sh":
    case "bash":
      return "bash";
    case "html":
    case "htm":
      return "html";
    case "css":
      return "css";
    case "md":
    case "markdown":
      return "markdown";
    case "toml":
      return "toml";
    case "rs":
      return "rust";
    case "go":
      return "go";
    case "rb":
      return "ruby";
    case "java":
      return "java";
    case "kt":
      return "kotlin";
    case "swift":
      return "swift";
    case "sql":
      return "sql";
    case "xml":
      return "xml";
    default:
      return null;
  }
};

export const SkillDetailPanel = ({
  skill,
  files,
  onLoadFile,
  onDelete,
  onCopy,
  onOpenInFinder,
}: SkillDetailPanelProps) => {
  const { t } = useI18n();
  const iconStyle = getSkillIconStyle(skill.name);

  // Flat file list — drives the auto-select effect (first file with
  // SKILL.md prioritised). Walks the nested tree so files inside
  // subdirectories are reachable, not just top-level.
  const sortedFiles = useMemo(
    () => (files ? collectFiles(files) : []),
    [files],
  );

  // When the file list changes (skill switch), auto-select root-level
  // SKILL.md. If no root SKILL.md exists, clear the selection so the
  // preview pane shows the placeholder instead of a stale file.
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  useEffect(() => {
    if (sortedFiles.length === 0) {
      setSelectedPath(null);
      return;
    }
    const rootSkillMd = sortedFiles.find(
      (f) => f.path === "SKILL.md" || f.path.toLowerCase() === "skill.md",
    );
    setSelectedPath(rootSkillMd?.path ?? null);
  }, [sortedFiles]);

  const [content, setContent] = useState<string>("");
  const [contentLoading, setContentLoading] = useState(false);

  // Markdown files default to rendered preview; everything else can only
  // show the raw source. Reset on every file change so the per-file
  // default sticks (clicking from a .py to a .md restores rendered).
  const isMarkdown = selectedPath?.toLowerCase().endsWith(".md") ?? false;
  const sourceLanguage = selectedPath ? languageForPath(selectedPath) : null;
  const [viewMode, setViewMode] = useState<"rendered" | "source">("rendered");
  useEffect(() => {
    setViewMode(isMarkdown ? "rendered" : "source");
  }, [selectedPath, isMarkdown]);

  // ``copied`` flips true for ~1.2 s after a successful clipboard
  // write so the toolbar copy icon swaps to a checkmark for visual
  // confirmation. Reset whenever the file changes (a stale check on
  // a different file would be misleading).
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    setCopied(false);
  }, [selectedPath]);
  const handleCopySource = async () => {
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard permission denied / insecure context — fail silently.
    }
  };

  useEffect(() => {
    if (!selectedPath || !onLoadFile) return;
    let cancelled = false;
    setContentLoading(true);
    onLoadFile(selectedPath)
      .then((c) => {
        if (!cancelled) setContent(c);
      })
      .catch((err) => {
        if (!cancelled)
          setContent(
            `Error loading file: ${err instanceof Error ? err.message : "unknown"}`,
          );
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedPath, onLoadFile]);

  // Subtitle below the name: ``来源 · 位置 · 版本`` (any missing piece
  // is dropped).
  const categoryLabel = (() => {
    switch (skill.category) {
      case "builtin":
        return t("skill.builtin");
      case "official":
        return t("skill.groupOfficial");
      case "agents":
        return t("skill.groupAgents");
      case "claude":
        return t("skill.groupClaude");
      case "codex":
        return t("skill.groupCodex");
      default:
        return null;
    }
  })();
  const subtitleParts: string[] = [];
  if (categoryLabel) subtitleParts.push(categoryLabel);
  if (skill.path) subtitleParts.push(skill.path);
  if (skill.version && skill.version !== "–") subtitleParts.push(skill.version);

  return (
    <aside
      className={cn(
        // Render inside the project's own right-panel frame; this
        // wrapper is just a flex shell so the inline pieces (header /
        // file tree / preview) stack vertically. No border / radius /
        // shadow — the outer panel already provides them, doubling up
        // produces a card-in-card frame.
        "flex h-full flex-col overflow-hidden",
      )}
    >
      {/* Streamdown's table fullscreen view is portaled to
          ``document.body`` so the inline ``[&_...]`` overrides on the
          markdown wrapper don't reach it. Mirror the inline card style
          (cool-grey toolbar, soft border, normalised cell padding,
          square icon buttons) via a global stylesheet keyed off
          ``[data-streamdown="table-fullscreen"]``. Kept inside the
          component because nothing else needs it; React mounts it once
          per panel and any duplicates collapse via identical CSS. */}
      <style>{`
        [data-streamdown="table-fullscreen"] > div {
          background: white;
          border-radius: 12px;
          border: 1px solid #F0F1F3;
          overflow: hidden;
          /* Top offset = project TopBar height (36px). AppShell's
             inner flex uses p-4 pt-0, so the main card sits flush
             under the topbar; matching that here puts the fullscreen
             card on the same baseline. Other sides match AppShell's
             16px outer gutter. */
          margin: 36px 16px 16px 16px;
          height: calc(100% - 52px);
        }
        [data-streamdown="table-fullscreen"] > div > div:first-child {
          background: #F8F9FA;
          border-bottom: 1px solid #F0F1F3;
          padding: 0 16px;
          height: 32px;
          align-items: center;
          gap: 4px;
        }
        [data-streamdown="table-fullscreen"] > div > div:first-child button {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 20px;
          height: 20px;
          padding: 0;
          color: var(--ink-muted, #b6b7bc);
          cursor: default;
        }
        [data-streamdown="table-fullscreen"] > div > div:first-child button > svg {
          width: 12px;
          height: 12px;
        }
        [data-streamdown="table-fullscreen"] > div > div:nth-child(2) {
          padding: 0;
          background: white;
        }
        [data-streamdown="table-fullscreen"] table[data-streamdown="table"] {
          border: none;
          width: 100%;
          border-collapse: collapse;
        }
        [data-streamdown="table-fullscreen"] [data-streamdown="table-header"] {
          background: white;
          border-bottom: 1px solid #F0F1F3;
        }
        [data-streamdown="table-fullscreen"] [data-streamdown="table-header-cell"] {
          padding: 10px 16px;
          text-align: left;
          font-weight: 400;
          font-size: 12px;
          color: var(--color-ink-meta, #6e7481);
        }
        [data-streamdown="table-fullscreen"] [data-streamdown="table-row"] {
          border-bottom: 1px solid #F0F1F3;
        }
        [data-streamdown="table-fullscreen"] [data-streamdown="table-row"]:last-child {
          border-bottom: none;
        }
        [data-streamdown="table-fullscreen"] [data-streamdown="table-cell"] {
          padding: 12px 16px;
          font-size: 13px;
          color: var(--color-ink-heading, #131313);
        }
      `}</style>
      <div className="border-b border-surface-border px-4 pb-4 pt-4">
        <div className="mb-3 flex items-center gap-3">
          <div
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-transparent text-lg font-semibold"
            style={{ backgroundColor: iconStyle.bg, color: iconStyle.fg }}
          >
            {iconStyle.letter}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-base font-medium text-ink-heading">
              {skill.name}
            </div>
            <div className="text-xs text-ink-body">
              {subtitleParts.join(" · ")}
            </div>
          </div>
          {/* Inline actions on the title row: copy, open-in-finder (left)
              and delete (right). View-detail and edit moved out — the
              inline preview already covers most of what the detail
              page used to show, and edit is rare enough that surfacing
              it on every panel was noise. */}
          <TooltipProvider delayDuration={150}>
            <div className="flex shrink-0 items-center gap-0.5">
              {onCopy ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-label={t("skill.copyAsCustom")}
                      onClick={onCopy}
                      className="flex h-7 w-7 cursor-default items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
                    >
                      <Copy className="h-3.5 w-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    复制当前skill到技能列表
                  </TooltipContent>
                </Tooltip>
              ) : null}
              {onOpenInFinder ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-label="Reveal in Finder"
                      onClick={onOpenInFinder}
                      className="flex h-7 w-7 cursor-default items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
                    >
                      <FolderOpen className="h-3.5 w-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">文件夹</TooltipContent>
                </Tooltip>
              ) : null}
              {onDelete ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-label={t("skill.deleteSkill")}
                      onClick={onDelete}
                      className="flex h-7 w-7 cursor-default items-center justify-center rounded-md text-[#f54b4b] transition-colors hover:bg-[#f54b4b]/10 hover:text-[#f54b4b]"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">删除当前skill</TooltipContent>
                </Tooltip>
              ) : null}
            </div>
          </TooltipProvider>
        </div>
        <p className="text-sm leading-relaxed text-ink-body">
          {skill.description}
        </p>
        {skill.tags.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-1">
            {skill.tags.map((tag) => (
              <Badge key={tag} variant="outline">
                {tag}
              </Badge>
            ))}
          </div>
        )}
      </div>

      {/* File tree (left) + preview (right). Tree is a fixed-width
          column with its own scroll; preview takes the remaining space.
          Replaces the original stacked layout, which capped the tree at
          200 px and pushed the preview below it. */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <div className="flex w-[240px] shrink-0 flex-col overflow-hidden border-r border-surface-border px-4 pt-3">
          <div className="label-mono mb-2">{t("skill.fileStructure")}</div>
          {files === undefined ? (
            <div className="flex items-center gap-1.5 py-2 text-xs text-ink-meta">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t("common.loading")}
            </div>
          ) : sortedFiles.length === 0 ? (
            <div className="py-2 text-xs text-ink-meta">
              {t("skill.noFiles")}
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto pb-3">
              <ProjectFileTree
                rootPath={skill.name}
                tree={toFileTreeNodes(files)}
                onFileClick={setSelectedPath}
                activeFilePath={selectedPath}
                defaultOpenDepth={0}
                guideLineOffset={-7}
                guideLineSpacing={20}
              />
            </div>
          )}
        </div>

        {/* Preview column: header row keeps its own px-4 padding for the
            file path label + mode toggles; the scroll body below has no
            right padding so its native scrollbar sits flush against the
            outer panel border (matches the conversation page pattern).
            Inner content padding lives on each branch's own wrapper. */}
        <div className="flex flex-1 min-w-0 flex-col overflow-hidden">
          <div className="label-mono mb-2 mt-3 flex items-center justify-between gap-2 px-4">
            <span className="truncate">{selectedPath ?? "Preview"}</span>
            <div className="flex shrink-0 items-center gap-1">
              {contentLoading && (
                <Loader2 className="h-3 w-3 animate-spin text-ink-meta" />
              )}
              {/* Copy source — always available when there's content,
                  sits LEFT of the view-mode toggles so the row reads
                  ``[copy] [eye] [code]``. Replaces the per-file
                  copy chrome that Streamdown attaches to its own
                  code-block header (which we hide in source view). */}
              {selectedPath && content ? (
                <button
                  type="button"
                  aria-label={t("common.copy")}
                  onClick={() => void handleCopySource()}
                  className="flex h-5 w-5 cursor-default items-center justify-center rounded text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
                >
                  {copied ? (
                    <Check className="h-3 w-3 text-success" />
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                </button>
              ) : null}
              {/* View-mode toggle: only meaningful for markdown files
                  (everything else has nothing to render). Showing it
                  conditionally avoids a dead "查看" affordance on
                  source-only files. */}
              {selectedPath && isMarkdown ? (
                <>
                  <button
                    type="button"
                    aria-label="Rendered"
                    onClick={() => setViewMode("rendered")}
                    className={cn(
                      "flex h-5 w-5 cursor-default items-center justify-center rounded transition-colors",
                      viewMode === "rendered"
                        ? "bg-brand/10 text-brand"
                        : "text-ink-meta hover:bg-surface-soft hover:text-ink-body",
                    )}
                  >
                    <Eye className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    aria-label="Source"
                    onClick={() => setViewMode("source")}
                    className={cn(
                      "flex h-5 w-5 cursor-default items-center justify-center rounded transition-colors",
                      viewMode === "source"
                        ? "bg-brand/10 text-brand"
                        : "text-ink-meta hover:bg-surface-soft hover:text-ink-body",
                    )}
                  >
                    <Code className="h-3 w-3" />
                  </button>
                </>
              ) : null}
            </div>
          </div>
          {!selectedPath ? (
            <div className="flex-1 overflow-auto pl-4 pr-0 pb-3">
              <pre className="mr-4 whitespace-pre-wrap break-all rounded-md border border-surface-border bg-surface p-3 font-mono text-xs italic leading-relaxed text-ink-meta">
                {t("skill.selectFileToPreview")}
              </pre>
            </div>
          ) : isMarkdown && viewMode === "rendered" ? (
            // Markdown rendered view — relies on ``MarkdownContent`` for
            // the code-block + table card styling (those overrides
            // ship with the component so every consumer gets them).
            // The wrapper here only handles scroll-edge layout: scroll
            // container is flush with the panel's right border so the
            // scrollbar tracks the outer edge, while ``[&>*]:mr-4``
            // insets the rendered content from the right so it has
            // breathing room.
            <div className="flex-1 overflow-auto bg-surface pb-4 pl-4 pr-0 [&>*]:mr-4">
              <MarkdownContent content={transformFrontmatter(content)} />
            </div>
          ) : isMarkdown ? (
            // Markdown source view: Shiki for full syntax highlighting
            // (headings / links / inline code / bold), plus a DOM pass
            // inside ``MarkdownSourceView`` that recolours every
            // fenced-code region (delimiters + body) green so the
            // ``\`\`\`xxx`` blocks read at a glance.
            <div className="flex-1 overflow-auto pl-4 pr-0 pb-3">
              <div className="mr-4">
                <MarkdownSourceView content={content} />
              </div>
            </div>
          ) : sourceLanguage ? (
            // Source view: pipe through MarkdownContent wrapped in a
            // fenced code block so Streamdown's ``code`` plugin
            // (Shiki) syntax-highlights it. Two cosmetic overrides:
            //   - Hide Streamdown's own code-block header (the
            //     ``python`` label + copy/download chrome) — we already
            //     have the file path + view-mode toggles in our own
            //     header above, so the duplicated row is noise.
            //     Selector targets the documented stable
            //     ``data-streamdown="code-block-header"`` attribute.
            //   - Force the surrounding container to a flat white
            //     background to match the rest of the file content
            //     area (was inheriting Streamdown's tinted card bg).
            <div className="flex-1 overflow-auto pl-4 pr-0 pb-3">
              {/* Plain syntax-highlighted source — flatten every layer
                  Streamdown adds (header, container border / radius /
                  bg, body padding, pre margin, shiki pre padding) so
                  the code text sits directly on the panel surface with
                  only the panel-level frame around it. */}
              <div
                className={cn(
                  "mr-4 bg-surface text-xs",
                  "[&_[data-streamdown='code-block-header']]:hidden",
                  // Streamdown's floating actions overlay (copy /
                  // download capsule) is duplicated by our own copy
                  // button in the file-path row above — hide it here
                  // so we don't render two copy affordances on top of
                  // each other. ``!hidden`` is needed because the
                  // global ``MarkdownContent`` style block sets
                  // ``display: flex !important`` on the overlay to
                  // override Streamdown's own compound class list;
                  // ``display: none`` without ``!important`` loses.
                  "[&_[data-streamdown='code-block']>div:has(>[data-streamdown='code-block-actions'])]:!hidden",
                  "[&_[data-streamdown='code-block']]:border-0",
                  "[&_[data-streamdown='code-block']]:rounded-none",
                  "[&_[data-streamdown='code-block']]:bg-transparent",
                  "[&_[data-streamdown='code-block']]:shadow-none",
                  "[&_[data-streamdown='code-block-body']]:border-0",
                  "[&_[data-streamdown='code-block-body']]:bg-transparent",
                  // Streamdown's body element carries both
                  // ``data-streamdown="code-block-body"`` and
                  // ``data-language``; default class includes ``p-4``,
                  // and the ``MarkdownContent`` global overrides set
                  // ``px-4 pt-2 pb-4`` — both win over a non-important
                  // override here because Tailwind arbitrary variants
                  // share the same specificity. Use ``!`` important
                  // so the source-view variant wins.
                  "[&_[data-streamdown='code-block-body']]:!py-0",
                  "[&_[data-streamdown='code-block-body']]:!px-0",
                  "[&_[data-language]]:!py-0",
                  "[&_[data-language]]:!px-0",
                  "[&_pre]:m-0",
                )}
              >
                <MarkdownContent
                  content={wrapInCodeFence(content, sourceLanguage)}
                />
              </div>
            </div>
          ) : (
            <div className="flex-1 overflow-auto pl-4 pr-0 pb-3">
              <pre className="mr-4 whitespace-pre-wrap break-all rounded-md border border-surface-border bg-surface p-3 font-mono text-xs leading-relaxed text-ink-label">
                {content}
              </pre>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
};
