import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Folder, FolderOpen } from "lucide-react";
import { cn } from "../../lib/cn";
import { getFileTypeIcon } from "../../lib/file-type-icons";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "../ui/context-menu";
import { useI18n } from "../../hooks/use-i18n";

/**
 * Render a file/folder name in a single line with truncate-on-overflow,
 * and only show a tooltip with the full name when the text is actually
 * truncated. Native ``title`` would always trigger after ~1s; this
 * uses shadcn Tooltip with a 100ms delay so feedback is near-instant
 * for names that need it, and zero noise for names that fit.
 */
/**
 * Render a file/folder name truncated to a single line. Lazily attach
 * a ``title`` attribute when (and only when) the text overflows, so
 * the browser's native tooltip pops on hover for long names but stays
 * silent for short ones. Native ``title`` has a ~1s open delay
 * baked in by the browser — not as snappy as a custom tooltip, but
 * 100% reliable across nested-button cases that tripped Radix
 * Tooltip up here.
 */
const TruncatedName = ({
  text,
  className,
}: {
  text: string;
  className?: string;
}) => {
  const ref = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setTruncated(el.scrollWidth > el.clientWidth);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text]);
  // ``min-w-0`` lets the flex parent collapse this slot below the
  // intrinsic text width so ``truncate`` actually clips. Without it
  // the slot grows to fit the text and the ellipsis never appears.
  return (
    <span
      ref={ref}
      title={truncated ? text : undefined}
      className={cn("min-w-0 flex-1 truncate text-left", className)}
    >
      {text}
    </span>
  );
};

export interface FileTreeNode {
  name: string;
  type: "file" | "folder";
  children?: FileTreeNode[];
  path: string;
}

export interface ProjectFileTreeProps {
  rootPath: string;
  tree: FileTreeNode[];
  onFileClick?: (path: string) => void;
  onFileDoubleClick?: (path: string) => void;
  onOpenInSystem?: (path: string) => void;
  onDeleteFile?: (path: string) => void;
  /** Path of the file currently considered "active" — gets a brand-tinted
   * background. Use for inline previews where the tree is the navigator
   * and a sibling pane shows the selected file's content. */
  activeFilePath?: string | null;
  /** Folders at depth ``< defaultOpenDepth`` start expanded; deeper
   * folders start collapsed. Default 1 = root level open, nested closed
   * (the original project-page behaviour). Pass 0 to start with every
   * folder collapsed. */
  defaultOpenDepth?: number;
  /** Hide the synthetic root path row when the parent renders its own
   * root/header controls. */
  hideRootRow?: boolean;
  /** Pixel offset applied to the vertical guide lines. */
  guideLineOffset?: number;
  /** Pixel spacing between vertical guide lines. */
  guideLineSpacing?: number;
}

const TreeNode = ({
  node,
  onFileClick,
  onFileDoubleClick,
  onOpenInSystem,
  onDeleteFile,
  depth,
  activeFilePath,
  defaultOpenDepth,
  guideLineOffset,
  guideLineSpacing,
}: {
  node: FileTreeNode;
  onFileClick?: (path: string) => void;
  onFileDoubleClick?: (path: string) => void;
  onOpenInSystem?: (path: string) => void;
  onDeleteFile?: (path: string) => void;
  depth: number;
  activeFilePath?: string | null;
  defaultOpenDepth: number;
  guideLineOffset: number;
  guideLineSpacing: number;
}) => {
  const { t } = useI18n();
  const [open, setOpen] = useState(depth < defaultOpenDepth);
  const guideColumns = Array.from({ length: depth + 1 }, (_, i) => (
    <span
      key={i}
      aria-hidden="true"
      className="absolute -top-1 -bottom-1 w-[0.5px] bg-surface-border"
      style={{ left: `${i * guideLineSpacing + 13 + guideLineOffset}px` }}
    />
  ));
  const rowStyle = {
    marginInline: "0",
    width: "100%",
    paddingLeft: `${depth * 14 + 20 + (depth > 0 ? 4 : 0)}px`,
  };

  if (node.type === "folder") {
    return (
      <div>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="relative flex w-full items-center gap-1.5 rounded px-2 py-1 text-xs text-ink-label hover:bg-surface-muted"
          style={rowStyle}
        >
          {guideColumns}
          {open ? (
            <ChevronDown className="relative h-3 w-3 shrink-0 text-ink-muted" />
          ) : (
            <ChevronRight className="relative h-3 w-3 shrink-0 text-ink-muted" />
          )}
          {open ? (
            <FolderOpen className="relative h-3.5 w-3.5 shrink-0 text-ink-muted" />
          ) : (
            <Folder className="relative h-3.5 w-3.5 shrink-0 text-ink-muted" />
          )}
          <TruncatedName text={node.name} className="relative" />
        </button>
        {open &&
          node.children?.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              onFileClick={onFileClick}
              onFileDoubleClick={onFileDoubleClick}
              onOpenInSystem={onOpenInSystem}
              onDeleteFile={onDeleteFile}
              depth={depth + 1}
              activeFilePath={activeFilePath}
              defaultOpenDepth={defaultOpenDepth}
              guideLineOffset={guideLineOffset}
              guideLineSpacing={guideLineSpacing}
            />
          ))}
      </div>
    );
  }

  const hasMenu = onOpenInSystem || onDeleteFile;
  const isActive = activeFilePath === node.path;

  const FileIcon = getFileTypeIcon(node.name);
  const inner = (
    <button
      type="button"
      onClick={() => onFileClick?.(node.path)}
      onDoubleClick={() => onFileDoubleClick?.(node.path)}
      className={cn(
        "relative flex w-full items-center gap-1.5 rounded-[4px] px-2 py-1 text-xs transition-colors",
        isActive
          ? "bg-surface-soft text-ink-heading"
          : "text-ink-heading hover:bg-surface-muted",
      )}
      style={rowStyle}
    >
      {guideColumns}
      <FileIcon className="relative h-3.5 w-3.5 shrink-0 text-ink-muted" />
      <TruncatedName text={node.name} className="relative" />
    </button>
  );

  if (!hasMenu) return inner;

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{inner}</ContextMenuTrigger>
      <ContextMenuContent className="min-w-[140px]">
        {onOpenInSystem && (
          <ContextMenuItem onClick={() => onOpenInSystem(node.path)}>
            {t("project.openInSystem")}
          </ContextMenuItem>
        )}
        {onDeleteFile && (
          <ContextMenuItem
            variant="destructive"
            onClick={() => onDeleteFile(node.path)}
          >
            {t("common.delete")}
          </ContextMenuItem>
        )}
      </ContextMenuContent>
    </ContextMenu>
  );
};

export const ProjectFileTree = ({
  rootPath,
  tree,
  onFileClick,
  onFileDoubleClick,
  onOpenInSystem,
  onDeleteFile,
  activeFilePath,
  defaultOpenDepth = 1,
  hideRootRow = false,
  guideLineOffset = 0,
  guideLineSpacing = 14,
}: ProjectFileTreeProps) => {
  const { t } = useI18n();
  if (tree.length === 0) {
    return <p className="text-2xs text-ink-meta">{t("project.noFiles")}</p>;
  }

  return (
    <div className="flex flex-col">
      {/* Root folder row — sits flush left so it aligns with the
          "文件结构" section title above it. No chevron spacer here:
          this row isn't expandable, and the alignment with the
          surrounding header text matters more than visual rhyme with
          the indented children below. */}
      {!hideRootRow && (
        <div className="mb-0.5 flex items-center gap-1.5 py-0.5 text-xs text-ink-label">
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
          <span className="truncate">{rootPath}</span>
        </div>
      )}
      <div className="flex-1">
        {tree.map((node) => (
          <TreeNode
            key={node.path}
            node={node}
            onFileClick={onFileClick}
            onFileDoubleClick={onFileDoubleClick}
            onOpenInSystem={onOpenInSystem}
            onDeleteFile={onDeleteFile}
            depth={0}
            activeFilePath={activeFilePath}
            defaultOpenDepth={defaultOpenDepth}
            guideLineOffset={guideLineOffset}
            guideLineSpacing={guideLineSpacing}
          />
        ))}
      </div>
    </div>
  );
};
