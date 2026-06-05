import type { FC } from 'react'
import { ScanSearch } from 'lucide-react'
import { cn } from '../../lib/cn'

export interface ParsingModeSelectorProps {
  modes: Array<{ id: string; name: string; detail: string; latency: string }>
  selectedId: string
  onSelect: (id: string) => void
}

export const ParsingModeSelector: FC<ParsingModeSelectorProps> = ({
  modes,
  selectedId,
  onSelect,
}) => {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {modes.map((mode) => {
        const selected = selectedId === mode.id
        return (
          <button
            key={mode.id}
            type="button"
            onClick={() => onSelect(mode.id)}
            className={cn(
              'flex items-start gap-3 rounded-xl border p-5 text-left transition-all',
              selected
                ? 'border-brand/25 bg-card shadow-md'
                : 'border-surface-border bg-card',
            )}
          >
            <div className="flex flex-1 flex-col gap-1">
              <span className="text-sm font-medium text-ink-title">
                {mode.name}
              </span>
              <span className="text-xs text-ink-body">{mode.detail}</span>
              <span className="mt-1 text-[11px] uppercase text-ink-muted">
                {mode.latency}
              </span>
            </div>
            <ScanSearch className="h-5 w-5 shrink-0 text-ink-muted" />
          </button>
        )
      })}
    </div>
  )
}
