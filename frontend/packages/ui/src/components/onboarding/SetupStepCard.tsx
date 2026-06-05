import type { FC } from 'react'
import { Check } from 'lucide-react'
import { cn } from '../../lib/cn'

export interface SetupStepCardProps {
  eyebrow: string
  title: string
  desc: string
  active?: boolean
  done?: boolean
}

export const SetupStepCard: FC<SetupStepCardProps> = ({
  eyebrow,
  title,
  desc,
  active = false,
  done = false,
}) => {
  return (
    <div
      className={cn(
        'flex items-start gap-4 rounded-[18px] border p-5 transition-all',
        active
          ? 'border-brand/25 bg-card shadow-md'
          : 'border-surface-border bg-card/72',
      )}
    >
      <div
        className={cn(
          'flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-sm font-medium',
          done
            ? 'border-brand bg-brand text-white'
            : active
              ? 'border-brand/25 bg-brand-light text-brand'
              : 'border-surface-border bg-surface-soft text-ink-body',
        )}
      >
        {done ? <Check className="h-4 w-4" /> : null}
      </div>

      <div className="flex flex-col gap-1">
        <span className="text-[10px] uppercase tracking-wide text-ink-muted">
          {eyebrow}
        </span>
        <span className="text-sm font-medium text-ink-title">{title}</span>
        <span className="text-xs text-ink-body">{desc}</span>
      </div>
    </div>
  )
}
