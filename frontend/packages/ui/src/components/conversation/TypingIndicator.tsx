export const TypingIndicator = () => (
  <div className="flex items-center gap-1 px-4 py-2">
    {[0, 1, 2].map((i) => (
      <span
        key={i}
        className="h-1.5 w-1.5 animate-pulse rounded-full bg-ink-muted"
        style={{ animationDelay: `${i * 0.15}s` }}
      />
    ))}
  </div>
)
