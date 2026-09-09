import { useCallback, useEffect, useState } from "react";
import {
  projectsApi,
  type ProjectDetail,
  type SessionListItem,
} from "@valuz/core";
import type { FileTreeNode } from "@valuz/ui";
import { useProjectKbBindings, useKbDocTree } from "@valuz/app/hooks";
import { mergeFolderChildren, toFileTree } from "./file-tree-utils";

/**
 * How deep the first file-tree listing goes.
 *
 * Two levels of folders show up expanded-ready without a second request, which
 * covers most project layouts; anything below is fetched on expand. Raising it
 * is not free — the walk stats every entry it lists.
 */
const FILE_TREE_INITIAL_DEPTH = 3;

/** Depth per on-expand fetch. One level: the user is looking at one level. */
const FILE_TREE_EXPAND_DEPTH = 1;

type KbPickerStateParams = {
  selectedProjectId: string | null;
  activeProject: ProjectDetail | null;
  /** The open session's creation-time worktree snapshot (or ``null``). */
  activeWorktree: SessionListItem["worktree"] | null;
};

/**
 * ── KB / file-picker state ───────────────────────────────────────────
 *
 * Owns the knowledge-base and file-tree surface of the conversation
 * page: the attachment picker's open flag + the lazily-loaded global
 * KB doc tree behind it, the read-only project KB bindings, and the
 * project file tree + ``refreshFileTree`` (with its refetch effect).
 * Bodies, comments and dependency arrays are moved verbatim from
 * ``ConversationPage``. The turn-end file-tree refresh stays in the
 * page — it couples to ``isBusy`` / ``refreshArtifacts``.
 */
export function useKbPickerState({
  selectedProjectId,
  activeProject,
  activeWorktree,
}: KbPickerStateParams) {
  const [kbPickerOpen, setKbPickerOpen] = useState(false);

  const [fileTree, setFileTree] = useState<FileTreeNode[]>([]);

  // Project KB bindings — loaded for project projects only and
  // rendered **read-only** in the session panel (per product rule,
  // binding edits happen on the project detail page and apply to the
  // next session). ``handleExpandKbFolder`` is still wired so the
  // user can drill folders to inspect what's selected; only the
  // toggle handler is withheld. Passing ``null`` for chat /
  // skill-creator projects makes the hook no-op so those panels
  // show no KB tree.
  const {
    kbTree: projectKbTree,
    bindings: projectKbBindings,
    handleExpandKbFolder: handleExpandProjectKbFolder,
  } = useProjectKbBindings(
    activeProject?.kind === "project" ? selectedProjectId : null,
  );

  // Global KB document tree for the attachment picker. Loads lazily —
  // only when the picker is open — and is independent of the
  // project (chat sessions have no project scope; the picker shows
  // every KB for both chat and project conversations).
  const {
    kbTree: pickerKbTree,
    loading: pickerKbLoading,
    expandFolder: pickerExpandFolder,
  } = useKbDocTree(kbPickerOpen);

  // Load project file tree for the right context panel's Files tab.
  // Loaded for both project AND chat projects — chat sessions write
  // generated artifacts into their managed cwd, and the user wants
  // those visible in the right rail too (under the "Generated files" label).
  // The ``"chat-default"`` sentinel is NOT a real project (no row, no cwd —
  // the backend materializes a fresh chat project only when the first
  // session is created), so fetching its tree would just 404.
  const refreshFileTree = useCallback(() => {
    if (!selectedProjectId || selectedProjectId === "chat-default") {
      setFileTree([]);
      return;
    }
    projectsApi
      .listFiles(selectedProjectId, {
        depth: FILE_TREE_INITIAL_DEPTH,
        // Worktree sessions show their own checkout, not the shared project cwd.
        worktree: activeWorktree?.name ?? undefined,
      })
      .then((res) => setFileTree(toFileTree(res.files)))
      .catch(() => setFileTree([]));
  }, [selectedProjectId, activeWorktree]);

  useEffect(() => {
    refreshFileTree();
  }, [refreshFileTree]);

  /**
   * Load one folder's contents on expand.
   *
   * This is what lifts the depth ceiling: the initial listing goes a couple of
   * levels down (enough for the common case in one request) and marks what it
   * cut off, then each folder the user actually opens costs exactly one more
   * request. Asking for a deep tree up front instead would stat every entry in
   * it — on a cloud object-storage mount, one network round trip apiece.
   */
  const expandFileTreeFolder = useCallback(
    async (path: string) => {
      if (!selectedProjectId || selectedProjectId === "chat-default") return;
      const res = await projectsApi.listFiles(selectedProjectId, {
        depth: FILE_TREE_EXPAND_DEPTH,
        worktree: activeWorktree?.name ?? undefined,
        path,
      });
      setFileTree((prev) =>
        mergeFolderChildren(prev, path, toFileTree(res.files, path)),
      );
    },
    [selectedProjectId, activeWorktree],
  );

  return {
    kbPickerOpen,
    setKbPickerOpen,
    pickerKbTree,
    pickerKbLoading,
    pickerExpandFolder,
    projectKbTree,
    projectKbBindings,
    handleExpandProjectKbFolder,
    fileTree,
    setFileTree,
    refreshFileTree,
    expandFileTreeFolder,
  };
}
