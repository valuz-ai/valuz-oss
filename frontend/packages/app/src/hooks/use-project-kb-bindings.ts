import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  bindingApi,
  kbApi,
  type BindingItem,
  type KbListItem,
  type KbTreeNode,
} from "@valuz/core";
import { t } from "@valuz/shared/i18n";
import type { KbBindingTreeNode } from "@valuz/ui";

// Local helpers — kept in sync with the equivalents in the project /
// conversation pages. They only walk the in-memory tree, no I/O.

function apiNodeToBindingNode(n: KbTreeNode): KbBindingTreeNode {
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

function kbToBindingNode(
  kb: KbListItem,
  rootNodes: KbTreeNode[],
): KbBindingTreeNode {
  return {
    id: kb.id,
    name: kb.name,
    kind: "kb",
    status: kb.status,
    documentCount: kb.document_count,
    children: rootNodes.map(apiNodeToBindingNode),
    childrenLoaded: true,
  };
}

function containsNodeId(node: KbBindingTreeNode, id: string): boolean {
  if (node.id === id) return true;
  return node.children?.some((c) => containsNodeId(c, id)) ?? false;
}

function findTreeNode(
  nodes: KbBindingTreeNode[],
  id: string,
): KbBindingTreeNode | null {
  for (const n of nodes) {
    if (n.id === id) return n;
    if (n.children) {
      const found = findTreeNode(n.children, id);
      if (found) return found;
    }
  }
  return null;
}

function findParentFolder(
  root: KbBindingTreeNode,
  childId: string,
): KbBindingTreeNode | null {
  if (!root.children) return null;
  for (const c of root.children) {
    if (c.id === childId) return root;
    const found = findParentFolder(c, childId);
    if (found) return found;
  }
  return null;
}

function updateChildren(
  children: KbBindingTreeNode[],
  folderId: string,
  newChildren: KbBindingTreeNode[],
): KbBindingTreeNode[] {
  return children.map((c) => {
    if (c.id === folderId)
      return { ...c, children: newChildren, childrenLoaded: true };
    if (c.children)
      return {
        ...c,
        children: updateChildren(c.children, folderId, newChildren),
      };
    return c;
  });
}

export interface UseProjectKbBindingsResult {
  kbTree: KbBindingTreeNode[];
  bindings: BindingItem[];
  /** Toggle a binding (kb / folder / document) for the project. */
  handleToggleBinding: (
    kind: "kb" | "folder" | "document",
    targetId: string,
  ) => Promise<void>;
  /** Lazy-load a folder's children when the user expands it. */
  handleExpandKbFolder: (kbId: string, folderId: string) => Promise<void>;
  /** Refetch tree + bindings (use after external edits). */
  refresh: () => Promise<void>;
  /**
   * Replace the set of added KBs atomically. Existing sub-bindings for KBs
   * still in ``kbIds`` are preserved; bindings for removed KBs are dropped;
   * newly added KBs get a top-level ``kb`` binding.
   */
  handleSetAddedKbs: (kbIds: string[]) => Promise<void>;
  /** Remove a KB from the project — drops every binding under it. */
  handleRemoveKb: (kbId: string) => Promise<void>;
  /** Reset a KB to "whole KB in scope" — drops its folder / document
   *  bindings and leaves a single ``kb`` binding. */
  handleSelectAllInKb: (kbId: string) => Promise<void>;
}

/**
 * Loads the project's KB tree + binding state and exposes the toggle /
 * expand handlers the ProjectDetailContextPanel expects. ``projectId``
 * may be ``null`` while a different (chat) project is active —
 * the hook then no-ops and returns empty state.
 *
 * Centralized here so DesktopProjectDetailPage and DesktopConversationPage
 * share one source of truth: KB toggles made from inside an active
 * conversation feed back into the same data model the project detail
 * page reads.
 */
export function useProjectKbBindings(
  projectId: string | null,
): UseProjectKbBindingsResult {
  const [kbTree, setKbTree] = useState<KbBindingTreeNode[]>([]);
  const [bindings, setBindings] = useState<BindingItem[]>([]);

  const refresh = useCallback(async () => {
    if (!projectId) {
      setKbTree([]);
      setBindings([]);
      return;
    }
    const [kbListRes, bindingsRes] = await Promise.all([
      kbApi.list().catch(() => ({ knowledge_bases: [] as KbListItem[] })),
      bindingApi
        .list(projectId)
        .catch(() => ({ bindings: [] as BindingItem[] })),
    ]);
    setBindings(bindingsRes.bindings);
    const kbNodes = await Promise.all(
      kbListRes.knowledge_bases.map(async (kb) => {
        const tree = await kbApi
          .tree(kb.id)
          .catch(() => ({ nodes: [] as KbTreeNode[] }));
        return kbToBindingNode(kb, tree.nodes);
      }),
    );
    setKbTree(kbNodes);
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const isDirectlyBound = useCallback(
    (kind: string, targetId: string) =>
      bindings.some((b) => b.binding_kind === kind && b.target_id === targetId),
    [bindings],
  );

  const isCoveredByParent = useCallback(
    (
      kind: string,
      targetId: string,
    ): { covered: boolean; parentKind?: string; parentId?: string } => {
      if (kind === "kb") return { covered: false };
      for (const kb of kbTree) {
        if (kind === "folder" || kind === "document") {
          if (isDirectlyBound("kb", kb.id) && containsNodeId(kb, targetId)) {
            return { covered: true, parentKind: "kb", parentId: kb.id };
          }
        }
        if (kind === "document") {
          const parentFolder = findParentFolder(kb, targetId);
          if (parentFolder && isDirectlyBound("folder", parentFolder.id)) {
            return {
              covered: true,
              parentKind: "folder",
              parentId: parentFolder.id,
            };
          }
        }
      }
      return { covered: false };
    },
    [kbTree, isDirectlyBound],
  );

  const handleToggleBinding = useCallback(
    async (kind: "kb" | "folder" | "document", targetId: string) => {
      if (!projectId) return;
      let newBindings: Array<{ binding_kind: string; target_id: string }>;

      if (isDirectlyBound(kind, targetId)) {
        newBindings = bindings
          .filter((b) => !(b.binding_kind === kind && b.target_id === targetId))
          .map((b) => ({
            binding_kind: b.binding_kind,
            target_id: b.target_id,
          }));
      } else {
        const coverage = isCoveredByParent(kind, targetId);
        if (coverage.covered && coverage.parentKind && coverage.parentId) {
          const parentNode = findTreeNode(kbTree, coverage.parentId);
          if (parentNode?.children) {
            const siblingBindings = parentNode.children
              .filter((c) => c.id !== targetId)
              .map((c) => ({ binding_kind: c.kind, target_id: c.id }));
            newBindings = bindings
              .filter(
                (b) =>
                  !(
                    b.binding_kind === coverage.parentKind &&
                    b.target_id === coverage.parentId
                  ),
              )
              .map((b) => ({
                binding_kind: b.binding_kind,
                target_id: b.target_id,
              }))
              .concat(siblingBindings);
          } else {
            return;
          }
        } else {
          newBindings = [
            ...bindings.map((b) => ({
              binding_kind: b.binding_kind,
              target_id: b.target_id,
            })),
            { binding_kind: kind, target_id: targetId },
          ];
        }
      }

      try {
        const res = await bindingApi.update(projectId, newBindings);
        setBindings(res.bindings);
      } catch {
        toast.error(t("knowledge.bindFailed" as Parameters<typeof t>[0]));
      }
    },
    [projectId, bindings, kbTree, isDirectlyBound, isCoveredByParent],
  );

  const handleExpandKbFolder = useCallback(
    async (kbId: string, folderId: string) => {
      try {
        const res = await kbApi.tree(kbId, folderId);
        setKbTree((prev) =>
          prev.map((kb) =>
            kb.id === kbId
              ? {
                  ...kb,
                  children: updateChildren(
                    kb.children ?? [],
                    folderId,
                    res.nodes.map(apiNodeToBindingNode),
                  ),
                }
              : kb,
          ),
        );
      } catch {
        toast.error(t("knowledge.cannotLoadDir" as Parameters<typeof t>[0]));
      }
    },
    [],
  );

  /**
   * Finds the KB root node that contains ``targetId`` (by walking the tree).
   * Returns ``null`` if no KB contains it.
   */
  const findOwningKbId = useCallback(
    (targetId: string): string | null => {
      for (const kb of kbTree) {
        if (containsNodeId(kb, targetId)) return kb.id;
      }
      return null;
    },
    [kbTree],
  );

  const handleSetAddedKbs = useCallback(
    async (kbIds: string[]) => {
      if (!projectId) return;
      const kbIdSet = new Set(kbIds);

      // Keep existing bindings whose owning KB is still in kbIds.
      // ``findOwningKbId`` returns ``null`` when the binding's node
      // isn't in the lazily-loaded ``kbTree`` (e.g. a binding on a deep
      // document inside an unexpanded folder). In that case keep the
      // binding rather than silently dropping a real selection we just
      // can't attribute to a KB from the current tree state.
      const kept = bindings
        .filter((b) => {
          const owningKb = findOwningKbId(b.target_id);
          if (owningKb === null) return true;
          return kbIdSet.has(owningKb);
        })
        .map((b) => ({ binding_kind: b.binding_kind, target_id: b.target_id }));

      // Determine which KBs already have at least one binding preserved.
      const representedKbIds = new Set(
        kept.map((b) => findOwningKbId(b.target_id)).filter(Boolean),
      );

      // For each newly-added KB with no existing binding, add a top-level one.
      for (const kbId of kbIds) {
        if (!representedKbIds.has(kbId)) {
          kept.push({ binding_kind: "kb", target_id: kbId });
        }
      }

      try {
        const res = await bindingApi.update(projectId, kept);
        setBindings(res.bindings);
      } catch {
        toast.error(t("knowledge.bindFailed" as Parameters<typeof t>[0]));
      }
    },
    [projectId, bindings, findOwningKbId],
  );

  const handleRemoveKb = useCallback(
    async (kbId: string) => {
      if (!projectId) return;
      // Drop every binding owned by this KB. ``findOwningKbId`` may
      // return ``null`` for a deep node not in the loaded tree — keep
      // those rather than dropping what we can't attribute to this KB.
      const kept = bindings
        .filter((b) => findOwningKbId(b.target_id) !== kbId)
        .map((b) => ({ binding_kind: b.binding_kind, target_id: b.target_id }));
      try {
        const res = await bindingApi.update(projectId, kept);
        setBindings(res.bindings);
      } catch {
        toast.error(t("knowledge.bindFailed" as Parameters<typeof t>[0]));
      }
    },
    [projectId, bindings, findOwningKbId],
  );

  const handleSelectAllInKb = useCallback(
    async (kbId: string) => {
      if (!projectId) return;
      // Drop this KB's folder / document bindings, then add a single
      // kb-level binding so the whole KB is back in scope.
      const kept = bindings
        .filter((b) => findOwningKbId(b.target_id) !== kbId)
        .map((b) => ({ binding_kind: b.binding_kind, target_id: b.target_id }));
      kept.push({ binding_kind: "kb", target_id: kbId });
      try {
        const res = await bindingApi.update(projectId, kept);
        setBindings(res.bindings);
      } catch {
        toast.error(t("knowledge.bindFailed" as Parameters<typeof t>[0]));
      }
    },
    [projectId, bindings, findOwningKbId],
  );

  return {
    kbTree,
    bindings,
    handleToggleBinding,
    handleExpandKbFolder,
    refresh,
    handleSetAddedKbs,
    handleRemoveKb,
    handleSelectAllInKb,
  };
}
