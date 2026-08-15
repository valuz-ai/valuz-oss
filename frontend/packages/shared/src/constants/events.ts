export const AGENT_EVENTS = {
  thinking: 'thinking',
  textDelta: 'text_delta',
  textDone: 'text_done',
  toolCall: 'tool_call',
  toolResult: 'tool_result',
  error: 'error',
} as const

/** Native desktop menu asks the renderer to close its foremost preview first. */
export const DESKTOP_PREVIEW_CLOSE_REQUESTED =
  'desktop:preview-close-requested'
