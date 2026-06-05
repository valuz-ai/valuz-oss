export type AgentEvent =
  | { type: 'thinking'; content: string }
  | { type: 'text_delta'; delta: string }
  | { type: 'text_done'; content: string }
  | { type: 'tool_call'; toolName: string; toolInput: unknown; id: string }
  | { type: 'tool_result'; id: string; result: string }
  | { type: 'error'; message: string; recoverable: boolean }

export interface WorkspaceSummary {
  id: string
  name: string
  mode: 'chat' | 'project'
  summary: string
}
