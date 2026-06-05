import { create } from "zustand";
import { tasksApi, type Task } from "../api/tasks-api";

interface TaskStoreState {
  /** Cross-project task list, newest activity first. Hydrated from
   * ``GET /v1/tasks`` and refreshed when the user navigates into / out
   * of task or project pages so the sidebar TASKS section stays close
   * to live without an SSE subscription. */
  tasks: Task[];
  loading: boolean;

  /** VALUZ-CHATPLAN S3 — map of ``task_id → latest plan_version seen``.
   *
   * Each ``task_plan_update`` event appends a new Plan Card to the
   * conversation flow (history is immutable), but only the latest card
   * for a given task should be interactive (Execute / Abandon / Open
   * Task Detail). Older cards render greyed-out. This map is the single
   * source of truth for "is this card the latest?" — the PlanCard
   * component reads it to decide whether to enable its action buttons.
   */
  latestPlanIdByTaskId: Record<string, number>;

  setTasks: (tasks: Task[]) => void;
  fetchAllTasks: (limit?: number) => Promise<void>;
  /** Record a fresh Plan Card for a task (called from the conversation
   * page when a ``task_plan_update`` event arrives). Monotonic — earlier
   * versions cannot clobber a newer one. */
  recordPlanVersion: (taskId: string, planVersion: number) => void;
}

export const useTaskStore = create<TaskStoreState>((set) => ({
  tasks: [],
  loading: false,
  latestPlanIdByTaskId: {},

  setTasks: (tasks) => set({ tasks }),

  fetchAllTasks: async (limit = 50) => {
    set({ loading: true });
    try {
      const { tasks } = await tasksApi.listAllTasks(limit);
      set({ tasks });
    } finally {
      set({ loading: false });
    }
  },

  recordPlanVersion: (taskId, planVersion) =>
    set((state) => {
      const existing = state.latestPlanIdByTaskId[taskId] ?? 0;
      if (planVersion <= existing) return state;
      return {
        latestPlanIdByTaskId: {
          ...state.latestPlanIdByTaskId,
          [taskId]: planVersion,
        },
      };
    }),
}));
