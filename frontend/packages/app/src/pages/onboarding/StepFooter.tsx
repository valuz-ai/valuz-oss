import { Button } from "@valuz/ui";

/**
 * Shared footer row for onboarding config steps: an optional status hint on the
 * left, and skip / primary actions on the right. Uses the app's Button so it
 * matches the rest of the product rather than the editorial welcome screen.
 */
export const StepFooter = ({
  status,
  onSkip,
  skipLabel,
  primaryLabel,
  onPrimary,
  primaryDisabled,
}: {
  status?: string;
  onSkip?: () => void;
  skipLabel?: string;
  primaryLabel: string;
  onPrimary: () => void;
  primaryDisabled?: boolean;
}) => (
  <div className="flex items-center justify-between">
    <span className="text-xs text-ink-meta">{status}</span>
    <div className="flex items-center gap-2">
      {onSkip && skipLabel && (
        <button
          type="button"
          onClick={onSkip}
          className="px-2 text-xs text-ink-meta transition-colors hover:text-ink-heading"
        >
          {skipLabel}
        </button>
      )}
      <Button size="sm" onClick={onPrimary} disabled={primaryDisabled}>
        {primaryLabel} →
      </Button>
    </div>
  </div>
);
