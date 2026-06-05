import type { FC } from 'react'
import { FolderOpen, FileText } from 'lucide-react'

export interface FileListProps {
  files: Array<{
    name: string
    type: 'folder' | 'file'
    size: string
    modified: string
  }>
}

export const FileList: FC<FileListProps> = ({ files }) => {
  return (
    <div className="flex flex-col divide-y divide-surface-border">
      {files.map((file) => (
        <div
          key={file.name}
          className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0"
        >
          {file.type === 'folder' ? (
            <FolderOpen className="h-4 w-4 shrink-0 text-brand-500" />
          ) : (
            <FileText className="h-4 w-4 shrink-0 text-ink-muted" />
          )}
          <span className="flex-1 truncate text-sm text-ink-title">
            {file.name}
          </span>
          <span className="shrink-0 text-xs text-ink-muted">{file.size}</span>
          <span className="shrink-0 text-xs text-ink-muted">
            {file.modified}
          </span>
        </div>
      ))}
    </div>
  )
}
