/**
 * Shared project list state.
 *
 * The desktop layout (project groups + 对话 chat aggregate) and the
 * conversation page both need a consistent view of which projects
 * exist on the backend. Holding it in a Zustand store (instead of
 * each component fetching independently into local ``useState``) lets
 * any code path that creates a project push the row in
 * synchronously — the sidebar updates the same render tick the API
 * call returns, no need to wait for the post-navigate refetch.
 *
 * Shape matches ``ProjectListItem`` returned by
 * ``GET /v1/projects``. ``upsertProject`` is the canonical
 * "merge a single project" action — used by the conversation
 * page's ``ensureSession`` after it allocates a fresh chat
 * project, and by the create-project flow.
 */

import { create } from "zustand";
import type { ProjectListItem } from "../api/projects-api";

export type ProjectItem = ProjectListItem;

interface ProjectStoreState {
  projects: ProjectItem[];
  activeProjectId: string | null;
  /** Replace the entire list — used by the layout's ``fetchProjects``. */
  setProjects: (projects: ProjectItem[]) => void;
  /** Merge one project by id (idempotent). Pushes a new row to the
   *  front when missing, updates in place when present. Useful for
   *  optimistic updates after creation/rename. */
  upsertProject: (project: ProjectItem) => void;
  /** Drop one project by id — for the delete flow. */
  removeProject: (projectId: string) => void;
  setActiveProject: (projectId: string | null) => void;
}

export const useProjectStore = create<ProjectStoreState>((set) => ({
  projects: [],
  activeProjectId: null,
  setProjects: (projects) => set({ projects }),
  upsertProject: (project) =>
    set((state) => {
      const idx = state.projects.findIndex((w) => w.id === project.id);
      if (idx === -1) {
        // Prepend so freshly-minted chat projects sit at the top of
        // any "most recent" rendering. The layout sorts the "对话"
        // group by session ``updated_at`` independently, so this only
        // affects raw-list ordering for callers that don't sort.
        return { projects: [project, ...state.projects] };
      }
      const next = state.projects.slice();
      next[idx] = { ...next[idx], ...project };
      return { projects: next };
    }),
  removeProject: (projectId) =>
    set((state) => ({
      projects: state.projects.filter((w) => w.id !== projectId),
    })),
  setActiveProject: (activeProjectId) => set({ activeProjectId }),
}));
