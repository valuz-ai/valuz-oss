import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface SettingsRowProps {
  label: string
  desc?: string
  children: ReactNode
  className?: string
}

export const SettingsRow = ({ label, desc, children, className }: SettingsRowProps) => (
  <div
    className={cn(
      'grid grid-cols-[1fr_auto] gap-5 px-4 py-3',
      className,
    )}
  >
    <div>
      <div className="text-sm font-medium text-ink-heading">{label}</div>
      {desc ? (
        <div className="mt-0.5 text-xs text-ink-body">{desc}</div>
      ) : null}
    </div>
    <div className="flex items-center">{children}</div>
  </div>
)
