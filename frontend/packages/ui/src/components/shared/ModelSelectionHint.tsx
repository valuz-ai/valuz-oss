/**
 * Trailing, right-aligned cell for a provider-owned model selection hint
 * (for example a points multiplier such as ``1.5×``).
 *
 * Every model list — the Composer popovers and the Radix ``Select`` based
 * pickers — renders the model name in a ``flex-1 truncate`` span and this
 * hint after it, so the hints line up as a column just before the check
 * indicator. Collapsed triggers never render it: the hint is only useful
 * while the user is comparing options.
 */
export const ModelSelectionHint = ({
  hint,
}: {
  hint: string | null | undefined;
}) => {
  const text = hint?.trim();
  if (!text) return null;
  return (
    <span className="shrink-0 text-2xs tabular-nums text-ink-meta">
      {text}
    </span>
  );
};
