import type { FC } from 'react'
import { ChevronRight, FolderOpen, Trash2 } from 'lucide-react'
import { cn } from '../../lib/cn'
import { Button } from '../ui/button'

export interface ProjectCardProps {
  name: string
  note: string
  href?: string
  /** Optional trailing node on the name row — multi-target editions pass an
   * execution-origin icon here, exactly where the knowledge card shows it. */
  badge?: React.ReactNode
  /** Optional bottom-left fact, e.g. a count. Mirrors the KB card's doc count. */
  meta?: React.ReactNode
  onDelete?: () => void
  LinkComponent?: React.ComponentType<{
    to: string
    className?: string
    children?: React.ReactNode
  }>
}

/**
 * Same card as the knowledge grid: square-ish tile, icon in a tinted rounded
 * square, name + path, and a footer that carries the row's facts and actions.
 * Two lists of "places your work lives" that looked like different products
 * is one product decision too many.
 */
export const ProjectCard: FC<ProjectCardProps> = ({
  name,
  note,
  href,
  badge,
  meta,
  onDelete,
  LinkComponent,
}) => {
  const inner = (
    <div
      className={cn(
        // No pointer cursor: the knowledge card (a button under Tailwind v4)
        // doesn't get one, and these two grids stay in step.
        'group cursor-default',
        'flex min-h-[148px] w-full flex-col rounded-2xl border border-surface-border',
        'bg-card p-4 text-left shadow-xs transition-all',
        'hover:-translate-y-1 hover:shadow-md',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand">
          <FolderOpen className="h-4 w-4" />
        </div>
      </div>
      <div className="mt-4 min-w-0">
        <div className="flex items-center gap-1">
          <span className="truncate text-sm font-medium text-ink-heading">
            {name}
          </span>
          {badge ?? null}
        </div>
        <div className="mt-1 line-clamp-2 break-all text-xs leading-5 text-ink-meta">
          {note}
        </div>
      </div>
      <div className="mt-auto flex items-center justify-between pt-4">
        <span className="text-xs text-ink-meta">{meta ?? null}</span>
        <div className="flex items-center gap-1">
          {onDelete ? (
            <Button
              variant="ghost"
              size="icon"
              className="size-4 shrink-0 opacity-0 transition-opacity hover:bg-transparent group-hover:opacity-100"
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                onDelete()
              }}
            >
              <Trash2 className="h-4 w-4 text-ink-muted" />
            </Button>
          ) : null}
          <ChevronRight className="h-4 w-4 shrink-0 text-ink-muted opacity-0 transition-opacity group-hover:opacity-100" />
        </div>
      </div>
    </div>
  )

  if (LinkComponent && href) {
    return (
      <LinkComponent to={href} className="block cursor-default">
        {inner}
      </LinkComponent>
    )
  }

  return inner
}
