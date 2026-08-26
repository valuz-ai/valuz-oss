import { create } from "zustand";
import { tasksApi, type Task } from "../api/tasks-api";

interface TaskStoreState {
  /** Cross-project task list, newest activity first. Hydrated from
   * ``GET /v1/tasks`` and refreshed when the user navigates into / out
   * of task or project pages so the sidebar TASKS section stays close
   * to live without an SSE subscription. */
  tasks: Task[];
  loading: boolean;

  setTasks: (tasks: Task[]) => void;
  fetchAllTasks: (limit?: number) => Promise<void>;
}

export const useTaskStore = create<TaskStoreState>((set) => ({
  tasks: [],
  loading: false,
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
}));
