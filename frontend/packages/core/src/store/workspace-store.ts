/**
 * Shared workspace list state.
 *
 * The desktop layout (project groups + 对话 chat aggregate) and the
 * conversation page both need a consistent view of which workspaces
 * exist on the backend. Holding it in a Zustand store (instead of
 * each component fetching independently into local ``useState``) lets
 * any code path that creates a workspace push the row in
 * synchronously — the sidebar updates the same render tick the API
 * call returns, no need to wait for the post-navigate refetch.
 *
 * Shape matches ``WorkspaceListItem`` returned by
 * ``GET /v1/workspaces``. ``upsertWorkspace`` is the canonical
 * "merge a single workspace" action — used by the conversation
 * page's ``ensureSession`` after it allocates a fresh chat
 * workspace, and by the create-project flow.
 */

import { create } from "zustand";
import type { WorkspaceListItem } from "../api/workspaces-api";

export type WorkspaceItem = WorkspaceListItem;

interface WorkspaceStoreState {
  workspaces: WorkspaceItem[];
  activeWorkspaceId: string | null;
  /** Replace the entire list — used by the layout's ``fetchProjects``. */
  setWorkspaces: (workspaces: WorkspaceItem[]) => void;
  /** Merge one workspace by id (idempotent). Pushes a new row to the
   *  front when missing, updates in place when present. Useful for
   *  optimistic updates after creation/rename. */
  upsertWorkspace: (workspace: WorkspaceItem) => void;
  /** Drop one workspace by id — for the delete flow. */
  removeWorkspace: (workspaceId: string) => void;
  setActiveWorkspace: (workspaceId: string | null) => void;
}

export const useWorkspaceStore = create<WorkspaceStoreState>((set) => ({
  workspaces: [],
  activeWorkspaceId: null,
  setWorkspaces: (workspaces) => set({ workspaces }),
  upsertWorkspace: (workspace) =>
    set((state) => {
      const idx = state.workspaces.findIndex((w) => w.id === workspace.id);
      if (idx === -1) {
        // Prepend so freshly-minted chat workspaces sit at the top of
        // any "most recent" rendering. The layout sorts the "对话"
        // group by session ``updated_at`` independently, so this only
        // affects raw-list ordering for callers that don't sort.
        return { workspaces: [workspace, ...state.workspaces] };
      }
      const next = state.workspaces.slice();
      next[idx] = { ...next[idx], ...workspace };
      return { workspaces: next };
    }),
  removeWorkspace: (workspaceId) =>
    set((state) => ({
      workspaces: state.workspaces.filter((w) => w.id !== workspaceId),
    })),
  setActiveWorkspace: (activeWorkspaceId) => set({ activeWorkspaceId }),
}));
