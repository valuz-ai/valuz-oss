interface StreamingThinkingPreviewProps {
  content: string;
}

/**
 * Shown above the in-flight assistant bubble while ``thinking_delta``
 * events are arriving. Disappears the moment the full ``thinking``
 * event flushes the buffer.
 */
export const StreamingThinkingPreview = ({
  content,
}: StreamingThinkingPreviewProps) => {
  if (!content) return null;
  return (
    <div className="mb-2 rounded-lg border border-violet-500/30 bg-violet-500/5 px-3 py-2 text-xs leading-5 text-violet-700 dark:text-violet-300">
      <div className="mb-1 flex items-center gap-1.5 font-medium uppercase tracking-wide opacity-80">
        <span className="size-1.5 animate-pulse rounded-full bg-violet-500" />
        Thinking
      </div>
      <p className="whitespace-pre-wrap font-mono text-[11px] leading-5 opacity-90">
        {content}
      </p>
    </div>
  );
};
