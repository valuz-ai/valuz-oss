import { cn } from '../../lib/cn'

export interface MetricStripProps {
  items: Array<{ label: string; value: string; hint?: string }>
}

export const MetricStrip = ({ items }: MetricStripProps) => (
  <div className={cn('grid grid-cols-3 gap-2')}>
    {items.map((item) => (
      <div
        key={item.label}
        className="rounded-xl bg-card px-3.5 py-3 shadow-xs"
      >
        <div className="text-[10px] uppercase tracking-[0.8px] text-ink-section">
          {item.label}
        </div>
        <div className="mt-2 text-xl font-medium text-ink-heading">{item.value}</div>
        {item.hint ? (
          <div className="mt-1 text-xs text-ink-body">{item.hint}</div>
        ) : null}
      </div>
    ))}
  </div>
)
