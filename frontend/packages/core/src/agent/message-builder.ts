import type { AgentEvent } from '@valuz/shared'

export const buildAssistantMessage = (events: AgentEvent[]) =>
  events
    .filter((event) => event.type === 'text_done' || event.type === 'thinking')
    .map((event) => ('content' in event ? event.content : ''))
    .join('\n')
