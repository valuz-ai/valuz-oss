import type { FC } from 'react'
import { FileText } from 'lucide-react'
import { IndexingStatusBadge } from '../knowledge/IndexingStatusBadge'

export interface KnowledgeListProps {
  items: Array<{
    name: string
    status: 'ready' | 'indexing' | 'failed'
    chunks: number
    importedAt: string
  }>
}

export const KnowledgeList: FC<KnowledgeListProps> = ({ items }) => {
  return (
    <div className="flex flex-col divide-y divide-surface-border">
      {items.map((item) => (
        <div
          key={item.name}
          className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0"
        >
          <FileText className="h-4 w-4 shrink-0 text-ink-muted" />
          <span className="flex-1 truncate text-sm text-ink-title">
            {item.name}
          </span>
          <IndexingStatusBadge status={item.status} />
          <span className="shrink-0 text-xs text-ink-muted">
            {item.chunks} chunks
          </span>
          <span className="shrink-0 text-xs text-ink-muted">
            {item.importedAt}
          </span>
        </div>
      ))}
    </div>
  )
}
