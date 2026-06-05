import type { FC } from 'react'

export interface SuggestionListProps {
  suggestions: string[]
  onClick?: (text: string) => void
}

export const SuggestionList: FC<SuggestionListProps> = ({
  suggestions,
  onClick,
}) => {
  return (
    <div className="flex max-w-[750px] flex-wrap justify-center gap-2">
      {suggestions.map((text) => (
        <button
          key={text}
          type="button"
          className="max-w-[750px] rounded-lg border border-transparent bg-[#f7f8fa] px-3 py-1.5 text-left text-xs font-normal leading-5 text-[#6e7481] shadow-none transition-colors hover:border-transparent hover:bg-surface-muted dark:border-surface-border dark:bg-surface-2 dark:text-ink-body dark:shadow-sm dark:hover:border-surface-border-hover"
          onClick={() => onClick?.(text)}
        >
          {text}
        </button>
      ))}
    </div>
  )
}
