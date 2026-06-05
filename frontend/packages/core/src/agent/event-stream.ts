import type { AgentEvent } from '@valuz/shared'

export const createEventStream = () => {
  const events: AgentEvent[] = []

  return {
    append(event: AgentEvent) {
      events.push(event)
    },
    snapshot() {
      return [...events]
    },
  }
}
