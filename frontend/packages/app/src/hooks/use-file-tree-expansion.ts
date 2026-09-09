import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import { projectsApi } from "@valuz/core";
import type { FileTreeNode } from "@valuz/ui";

import { mergeFolderChildren, toFileTree } from "../lib/file-tree";

/**
 * How deep the first listing of a project file tree goes.
 *
 * ``depth`` counts DESCENTS, so this returns four levels of names: enough that
 * most project layouts are browsable without a second request. Raising it is
 * not free — the walk stats every entry it lists, and on a cloud
 * object-storage mount that is a network round trip apiece.
 */
export const FILE_TREE_INITIAL_DEPTH = 3;

/**
 * Depth per on-expand fetch. Zero descents = exactly the one level the user
 * just opened; its own subfolders come back marked truncated and cost nothing
 * until they are opened in turn.
 */
export const FILE_TREE_EXPAND_DEPTH = 0;

/**
 * The tree's on-demand loader, shared by every surface that shows one.
 *
 * This is what removes the depth ceiling: instead of one walk deep enough for
 * the worst tree, each level is fetched when someone actually looks at it.
 * Lives here rather than in each page because the three surfaces that render a
 * project file tree have already drifted once on how deep they ask.
 */
export function useFileTreeExpansion({
  projectId,
  worktree,
  setFileTree,
}: {
  /** ``null`` (or the ``chat-default`` sentinel) disables expansion. */
  projectId: string | null;
  worktree?: string;
  setFileTree: Dispatch<SetStateAction<FileTreeNode[]>>;
}): (path: string) => Promise<void> {
  return useCallback(
    async (path: string) => {
      if (!projectId || projectId === "chat-default") return;
      const res = await projectsApi.listFiles(projectId, {
        depth: FILE_TREE_EXPAND_DEPTH,
        worktree,
        path,
      });
      setFileTree((prev) =>
        mergeFolderChildren(prev, path, toFileTree(res.files, path)),
      );
    },
    [projectId, worktree, setFileTree],
  );
}
