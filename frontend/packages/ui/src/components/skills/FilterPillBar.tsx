import { cn } from '../../lib/cn'

export interface FilterPillBarProps {
  filters: string[]
  activeFilter: string
  onSelect: (filter: string) => void
  count?: number
}

export const FilterPillBar = ({ filters, activeFilter, onSelect, count }: FilterPillBarProps) => (
  <div className="flex items-center gap-2 overflow-x-auto py-1">
    {filters.map((filter) => (
      <button
        key={filter}
        type="button"
        onClick={() => onSelect(filter)}
        className={cn(
          'whitespace-nowrap rounded-full px-4 py-1.5 text-xs font-medium transition-colors duration-150',
          activeFilter === filter
            ? 'bg-brand text-white'
            : 'bg-surface-muted text-ink-body hover:bg-brand-light hover:text-brand',
        )}
      >
        {filter}
      </button>
    ))}
    {count !== undefined ? (
      <div className="ml-auto whitespace-nowrap font-mono text-2xs text-ink-meta">
        {count} installed
      </div>
    ) : null}
  </div>
)
