import type { ReactNode } from 'react'

export interface AssistantMessageProps {
  children: ReactNode
  toolCalls?: ReactNode[]
}

export const AssistantMessage = ({ children, toolCalls }: AssistantMessageProps) => (
  <div className="flex gap-3">
    <div className="mt-1 h-6 w-6 shrink-0 rounded-full bg-brand" />
    <div>
      {children}
      {toolCalls && toolCalls.length > 0 ? (
        <div className="mt-2 space-y-2">{toolCalls}</div>
      ) : null}
    </div>
  </div>
)
