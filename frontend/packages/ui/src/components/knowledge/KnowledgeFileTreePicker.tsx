import { useMemo, useState } from "react";
import {
  ChevronRight,
  Database,
  FileText,
  Folder,
  FolderOpen,
  Loader2,
  Search,
  X,
} from "lucide-react";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Input } from "../ui/input";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";
import type { KbBindingTreeNode } from "../project/ProjectContextPanel";

export interface KnowledgeFileTreePickerProps {
  /** All knowledge bases as a tree (``kb`` → ``folder`` → ``document``).
   * KB first levels arrive eagerly; deeper folders are filled in by
   * ``onExpandFolder``. */
  kbTree: KbBindingTreeNode[];
  /** Document ids that should start checked (e.g. already attached to
   * the session). */
  selected: string[];
  loading?: boolean;
  /** Lazy-load a folder's children. The picker calls this the first
   * time a folder row is expanded (``childrenLoaded`` is false). */
  onExpandFolder: (kbId: string, folderId: string) => void | Promise<void>;
  onConfirm: (selectedDocIds: string[]) => void;
  onCancel: () => void;
}

/** Recursively decide whether a node (or any descendant document)
 * matches the search query. KB / folder nodes are kept only when a
 * descendant document matches; document nodes match on their own
 * name. An empty query keeps everything. */
function nodeMatchesSearch(node: KbBindingTreeNode, query: string): boolean {
  if (!query) return true;
  if (node.kind === "document") {
    return node.name.toLowerCase().includes(query);
  }
  return (node.children ?? []).some((c) => nodeMatchesSearch(c, query));
}

function TreeRow({
  node,
  kbId,
  depth,
  expanded,
  localSelected,
  query,
  onToggleExpand,
  onExpandFolder,
  onToggleDoc,
}: {
  node: KbBindingTreeNode;
  /** Owning KB id — threaded down so folder rows can call
   * ``onExpandFolder(kbId, folderId)``. For a KB row this equals
   * ``node.id``. */
  kbId: string;
  depth: number;
  expanded: Set<string>;
  localSelected: Set<string>;
  query: string;
  onToggleExpand: (node: KbBindingTreeNode, kbId: string) => void;
  onExpandFolder: (kbId: string, folderId: string) => void | Promise<void>;
  onToggleDoc: (id: string) => void;
}) {
  const { t } = useI18n();
  const isExpandable = node.kind === "kb" || node.kind === "folder";
  const isExpanded = expanded.has(node.id);
  const isMissing = node.status === "missing";

  if (!nodeMatchesSearch(node, query)) return null;

  // A search query forces expansion so matching documents are visible
  // without the user hand-expanding every folder.
  const showChildren = isExpandable && (isExpanded || Boolean(query));

  return (
    <>
      <div
        className={cn(
          "flex items-center gap-1.5 rounded-md py-1.5 pr-2 text-xs transition-colors",
          isMissing && "opacity-55",
          node.kind === "document"
            ? "hover:bg-surface-muted/60"
            : "hover:bg-surface-muted/40",
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {/* expand affordance — KB / folder only */}
        {isExpandable ? (
          <button
            type="button"
            onClick={() => onToggleExpand(node, kbId)}
            className="flex h-4 w-4 shrink-0 items-center justify-center"
            aria-label={isExpanded ? t("common.collapse") : t("common.expand")}
          >
            <ChevronRight
              className={cn(
                "h-2.5 w-2.5 text-ink-muted transition-transform",
                showChildren && "rotate-90",
              )}
            />
          </button>
        ) : (
          <span className="w-4 shrink-0" />
        )}

        {/* document rows carry a checkbox; KB / folder rows are
            navigation-only — folders cannot be picked, per the
            attachment rule (only files attach). */}
        {node.kind === "document" ? (
          <Checkbox
            checked={localSelected.has(node.id)}
            onCheckedChange={() => onToggleDoc(node.id)}
            disabled={isMissing}
            className="h-3.5 w-3.5 shrink-0 border-surface-border-hover data-[state=checked]:border-brand data-[state=checked]:bg-brand data-[state=checked]:text-white"
          />
        ) : null}

        {/* icon */}
        {node.kind === "kb" ? (
          <Database className="h-3 w-3 shrink-0 text-ink-muted" />
        ) : node.kind === "folder" ? (
          showChildren ? (
            <FolderOpen className="h-3 w-3 shrink-0 text-ink-muted" />
          ) : (
            <Folder className="h-3 w-3 shrink-0 text-ink-muted" />
          )
        ) : (
          <FileText className="h-3 w-3 shrink-0 text-ink-muted" />
        )}

        {/* label — clicking a document label toggles it too, for a
            bigger hit target. KB / folder labels toggle expansion. */}
        <button
          type="button"
          onClick={() => {
            if (node.kind === "document") {
              if (!isMissing) onToggleDoc(node.id);
            } else {
              onToggleExpand(node, kbId);
            }
          }}
          className="min-w-0 flex-1 truncate text-left text-ink-label"
        >
          {node.name}
        </button>

        {node.kind !== "document" && node.documentCount != null ? (
          <span className="shrink-0 text-2xs text-ink-meta">
            {node.documentCount}
          </span>
        ) : null}
      </div>

      {showChildren
        ? (node.children ?? []).map((child) => (
            <TreeRow
              key={child.id}
              node={child}
              kbId={kbId}
              depth={depth + 1}
              expanded={expanded}
              localSelected={localSelected}
              query={query}
              onToggleExpand={onToggleExpand}
              onExpandFolder={onExpandFolder}
              onToggleDoc={onToggleDoc}
            />
          ))
        : null}
    </>
  );
}

/**
 * Tree-structured knowledge-base file picker. Documents are organised
 * under their KB and folders; folders are expandable for navigation
 * but **not** selectable — only files attach. Mirrors the flat
 * ``KnowledgeFilePicker`` chrome (search + footer) so the two read
 * consistently.
 */
export const KnowledgeFileTreePicker = ({
  kbTree,
  selected,
  loading = false,
  onExpandFolder,
  onConfirm,
  onCancel,
}: KnowledgeFileTreePickerProps) => {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const [localSelected, setLocalSelected] = useState<Set<string>>(
    new Set(selected),
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const query = search.trim().toLowerCase();

  const toggleDoc = (id: string) => {
    setLocalSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleExpand = (node: KbBindingTreeNode, kbId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(node.id)) {
        next.delete(node.id);
      } else {
        next.add(node.id);
        // Lazy-load folder children the first time it's opened. KB
        // rows arrive pre-loaded so this only fires for folders.
        if (node.kind === "folder" && !node.childrenLoaded) {
          void onExpandFolder(kbId, node.id);
        }
      }
      return next;
    });
  };

  const visibleKbs = useMemo(
    () => kbTree.filter((kb) => nodeMatchesSearch(kb, query)),
    [kbTree, query],
  );

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex shrink-0 items-center justify-between gap-2">
        <span className="text-lg font-medium text-ink-heading">
          {t("knowledge.selectFromKb")}
        </span>
        <button
          type="button"
          aria-label={t("common.close")}
          onClick={onCancel}
          className="flex h-6 w-6 items-center justify-center rounded-md text-ink-muted transition hover:bg-surface-muted hover:text-ink-heading focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:outline-hidden"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Search */}
      <div className="relative shrink-0">
        <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("knowledge.searchDocPlaceholder")}
          className="h-8 pl-8 text-xs"
        />
      </div>

      {/* Tree — flex-1 so the list fills the dialog; min-h-0 lets it
          scroll inside the flex column instead of overflowing it. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-6 text-xs text-ink-meta">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t("common.loading")}
          </div>
        ) : visibleKbs.length === 0 ? (
          <div className="py-6 text-center text-xs text-ink-meta">
            {query
              ? t("knowledge.noMatchDocs")
              : t("knowledge.noAvailableDocs")}
          </div>
        ) : (
          <div className="space-y-0">
            {visibleKbs.map((kb) => (
              <TreeRow
                key={kb.id}
                node={kb}
                kbId={kb.id}
                depth={0}
                expanded={expanded}
                localSelected={localSelected}
                query={query}
                onToggleExpand={toggleExpand}
                onExpandFolder={onExpandFolder}
                onToggleDoc={toggleDoc}
              />
            ))}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex shrink-0 items-center justify-between">
        <span className="text-2xs text-ink-meta">
          {t("knowledge.selectedCount", { count: String(localSelected.size) })}
        </span>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button
            size="sm"
            onClick={() => onConfirm(Array.from(localSelected))}
          >
            {t("common.confirm")}
          </Button>
        </div>
      </div>
    </div>
  );
};
