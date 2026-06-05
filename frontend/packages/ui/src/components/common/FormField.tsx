import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface FormFieldProps {
  /** Field label text */
  label: string;
  /** htmlFor attribute to link label with input */
  htmlFor?: string;
  /** Error message displayed below the field */
  error?: string;
  /** The input/control element */
  children: ReactNode;
  /** Extra class on the wrapper */
  className?: string;
}

/**
 * Form field wrapper with a styled label and optional error message.
 * Wraps any input/control (Input, Textarea, Select, etc.).
 */
export const FormField = ({
  label,
  htmlFor,
  error,
  children,
  className,
}: FormFieldProps) => (
  <div className={cn("space-y-1.5", className)}>
    <label
      htmlFor={htmlFor}
      className="block text-xs font-medium text-ink-label"
    >
      {label}
    </label>
    {children}
    {error && <p className="text-xs text-error-text">{error}</p>}
  </div>
);
