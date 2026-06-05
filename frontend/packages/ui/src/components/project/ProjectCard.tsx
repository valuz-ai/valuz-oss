import type { FC } from 'react'
import { FolderOpen, Trash2 } from 'lucide-react'
import { cn } from '../../lib/cn'
import { Card, CardContent } from '../ui/card'
import { Button } from '../ui/button'

export interface ProjectCardProps {
  name: string
  note: string
  href?: string
  onDelete?: () => void
  LinkComponent?: React.ComponentType<{
    to: string
    className?: string
    children?: React.ReactNode
  }>
}

export const ProjectCard: FC<ProjectCardProps> = ({
  name,
  note,
  href,
  onDelete,
  LinkComponent,
}) => {
  const inner = (
    <Card
      className={cn(
        'group relative transition-all hover:border-brand/40 hover:shadow-sm',
      )}
    >
      <CardContent className="gap-3 py-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-brand/10 bg-brand-light text-brand">
          <FolderOpen className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-ink-heading">{name}</div>
          <div className="mt-0.5 truncate text-xs text-ink-body">{note}</div>
        </div>
        {onDelete ? (
          <Button
            variant="ghost"
            size="icon-xs"
            className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onDelete()
            }}
          >
            <Trash2 className="h-3.5 w-3.5 text-ink-muted" />
          </Button>
        ) : null}
      </CardContent>
    </Card>
  )

  if (LinkComponent && href) {
    return (
      <LinkComponent to={href} className="block">
        {inner}
      </LinkComponent>
    )
  }

  return inner
}
