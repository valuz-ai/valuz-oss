import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { kbApi, type KbListItem, type KbTreeNode } from "@valuz/core";
import { t } from "@valuz/shared/i18n";
import type { KbBindingTreeNode } from "@valuz/ui";

// Tree-mapping helpers — intentionally a small duplicate of the
// equivalents in ``use-project-kb-bindings.ts``. They map the stable
// ``kbApi`` response shape into the ``KbBindingTreeNode`` the UI tree
// components consume. Kept local so this hook has zero coupling to the
// binding-specific hook; if a third consumer appears, extract to a
// shared ``kb-tree-helpers`` module.

function apiNodeToTreeNode(n: KbTreeNode): KbBindingTreeNode {
  return {
    id: n.id,
    name: n.name,
    kind: n.kind === "folder" ? "folder" : "document",
    status: n.status,
    documentCount: n.document_count,
    children: n.kind === "folder" ? [] : undefined,
    childrenLoaded: n.kind !== "folder",
  };
}

function kbToTreeNode(
  kb: KbListItem,
  rootNodes: KbTreeNode[],
): KbBindingTreeNode {
  return {
    id: kb.id,
    name: kb.name,
    kind: "kb",
    status: kb.status,
    documentCount: kb.document_count,
    children: rootNodes.map(apiNodeToTreeNode),
    childrenLoaded: true,
  };
}

function replaceFolderChildren(
  children: KbBindingTreeNode[],
  folderId: string,
  newChildren: KbBindingTreeNode[],
): KbBindingTreeNode[] {
  return children.map((c) => {
    if (c.id === folderId) {
      return { ...c, children: newChildren, childrenLoaded: true };
    }
    if (c.children) {
      return {
        ...c,
        children: replaceFolderChildren(c.children, folderId, newChildren),
      };
    }
    return c;
  });
}

export interface UseKbDocTreeResult {
  /** All knowledge bases as a tree: ``kb`` → ``folder`` → ``document``.
   * Each KB's first level is loaded eagerly; deeper folders are
   * loaded on demand via ``expandFolder``. */
  kbTree: KbBindingTreeNode[];
  loading: boolean;
  /** Lazy-load a folder's children. No-op once ``childrenLoaded``. */
  expandFolder: (kbId: string, folderId: string) => Promise<void>;
  /** Re-fetch the whole tree (e.g. after the KB index changed). */
  reload: () => Promise<void>;
}

/**
 * Loads the **global** knowledge-base document tree for the
 * conversation attachment picker — independent of any project
 * binding (chat sessions have no project to scope to). When
 * ``enabled`` is false the hook stays idle and returns an empty
 * tree, so callers can gate it behind "the picker is open" without
 * paying the fetch cost on every render.
 */
export function useKbDocTree(enabled: boolean): UseKbDocTreeResult {
  const [kbTree, setKbTree] = useState<KbBindingTreeNode[]>([]);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    if (!enabled) {
      setKbTree([]);
      return;
    }
    setLoading(true);
    try {
      const kbListRes = await kbApi
        .list()
        .catch(() => ({ knowledge_bases: [] as KbListItem[] }));
      const kbNodes = await Promise.all(
        kbListRes.knowledge_bases.map(async (kb) => {
          const tree = await kbApi
            .tree(kb.id)
            .catch(() => ({ nodes: [] as KbTreeNode[] }));
          return kbToTreeNode(kb, tree.nodes);
        }),
      );
      setKbTree(kbNodes);
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const expandFolder = useCallback(async (kbId: string, folderId: string) => {
    try {
      const res = await kbApi.tree(kbId, folderId);
      setKbTree((prev) =>
        prev.map((kb) =>
          kb.id === kbId
            ? {
                ...kb,
                children: replaceFolderChildren(
                  kb.children ?? [],
                  folderId,
                  res.nodes.map(apiNodeToTreeNode),
                ),
              }
            : kb,
        ),
      );
    } catch {
      toast.error(t("knowledge.cannotLoadDir" as Parameters<typeof t>[0]));
    }
  }, []);

  return { kbTree, loading, expandFolder, reload };
}
