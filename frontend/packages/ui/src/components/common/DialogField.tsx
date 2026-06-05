import { forwardRef, type ReactNode } from "react";
import { cn } from "../../lib/cn";

/**
 * Canonical form-field primitives for dialogs. Every config / form
 * dialog (connector credentials, parser engine config, …) must use
 * these so label / input / placeholder / help text stay identical
 * across the app.
 *
 * The baseline is the Connectors config dialog's hand-written markup —
 * label = ``text-xs text-ink-meta``, input = soft-border / rounded-lg
 * with a brand focus ring. Do NOT reintroduce ad-hoc ``<input
 * className="…">`` inside dialogs; wrap with {@link DialogField} and
 * use {@link DialogInput} (or pass a Select / Switch as children).
 */

export interface DialogFieldProps {
  label: string;
  /** Appends a red ``*`` after the label. */
  required?: boolean;
  /** Secondary help line rendered under the control. */
  help?: string;
  /** Optional "get it here" external link appended to the help line. */
  helpUrl?: string;
  helpLinkText?: string;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
}

export const DialogField = ({
  label,
  required,
  help,
  helpUrl,
  helpLinkText,
  htmlFor,
  children,
  className,
}: DialogFieldProps) => (
  <div className={className}>
    <label htmlFor={htmlFor} className="mb-1 block text-xs text-ink-meta">
      {label}
      {required && <span className="ml-0.5 text-error-text">*</span>}
    </label>
    {children}
    {(help || helpUrl) && (
      <div className="mt-1 text-2xs text-ink-meta">
        {help}
        {helpUrl && (
          <a
            href={helpUrl}
            target="_blank"
            rel="noreferrer noopener"
            className={cn(
              "text-brand underline-offset-2 hover:underline",
              help && "ml-1",
            )}
          >
            {helpLinkText}
          </a>
        )}
      </div>
    )}
  </div>
);

/** Text/password input matching the dialog baseline. Drop-in for a
 * native ``<input>`` — forwards ref + all native props. */
export const DialogInput = forwardRef<
  HTMLInputElement,
  React.ComponentProps<"input">
>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      "w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
DialogInput.displayName = "DialogInput";
