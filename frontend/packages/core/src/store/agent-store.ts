import { create } from 'zustand'
import type { AgentEvent } from '@valuz/shared'

interface AgentStoreState {
  events: AgentEvent[]
  appendEvent: (event: AgentEvent) => void
  clearEvents: () => void
}

export const useAgentStore = create<AgentStoreState>((set) => ({
  events: [],
  appendEvent: (event) => set((state) => ({ events: [...state.events, event] })),
  clearEvents: () => set({ events: [] }),
}))
