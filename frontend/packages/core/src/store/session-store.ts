import { create } from "zustand";
import type { SessionListItem } from "@valuz/shared";
import { sessionsApi } from "../api/sessions-api";

interface SessionStoreState {
  sessions: SessionListItem[];
  activeSessionId: string | null;
  loading: boolean;

  setSessions: (sessions: SessionListItem[]) => void;
  setActiveSession: (sessionId: string | null) => void;

  fetchSessions: (workspaceId?: string) => Promise<void>;
  createSession: (
    workspaceId: string,
    title?: string,
  ) => Promise<SessionListItem>;
  renameSession: (sessionId: string, name: string) => Promise<void>;
  deleteSession: (sessionId: string) => Promise<void>;
}

export const useSessionStore = create<SessionStoreState>((set) => ({
  sessions: [],
  activeSessionId: null,
  loading: false,

  setSessions: (sessions) => set({ sessions }),
  setActiveSession: (activeSessionId) => set({ activeSessionId }),

  fetchSessions: async (workspaceId?: string) => {
    set({ loading: true });
    try {
      const { sessions } = await sessionsApi.list(workspaceId);
      set({ sessions });
    } finally {
      set({ loading: false });
    }
  },

  createSession: async (workspaceId: string, title?: string) => {
    const detail = await sessionsApi.create({
      workspace_id: workspaceId,
      title,
    });
    const item: SessionListItem = {
      id: detail.id,
      workspace_id: detail.workspace_id,
      name: detail.name,
      status: detail.status,
      origin: detail.origin,
      last_user_message_text: detail.last_user_message_text,
      locked_model_id: detail.locked_model_id,
      locked_provider_id: detail.locked_provider_id ?? null,
      runtime_provider: detail.runtime_provider,
      permission_mode: detail.permission_mode,
      effort: detail.effort ?? null,
      // A freshly-created session is always user-initiated (the
      // ``sessions.create`` endpoint is what the composer/sidebar
      // calls). Task-internal sessions are spawned server-side by
      // the orchestrator and never round-trip through this store.
      task_id: null,
      updated_at: detail.updated_at,
    };
    set((state) => ({ sessions: [item, ...state.sessions] }));
    return item;
  },

  renameSession: async (sessionId: string, name: string) => {
    await sessionsApi.rename(sessionId, name);
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.id === sessionId ? { ...s, name } : s,
      ),
    }));
  },

  deleteSession: async (sessionId: string) => {
    await sessionsApi.delete(sessionId);
    set((state) => ({
      sessions: state.sessions.filter((s) => s.id !== sessionId),
      activeSessionId:
        state.activeSessionId === sessionId ? null : state.activeSessionId,
    }));
  },
}));
