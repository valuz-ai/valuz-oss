import type { ReactNode } from 'react'

export interface UserMessageBubbleProps {
  children: ReactNode
}

export const UserMessageBubble = ({ children }: UserMessageBubbleProps) => (
  <div className="ml-auto max-w-[78%] rounded-[18px] bg-surface-muted px-4 py-2.5 text-sm leading-6 text-ink-heading">
    {children}
  </div>
)
