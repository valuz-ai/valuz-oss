import type { ReactNode } from "react";

/**
 * Shared layout for onboarding steps 2–4. Deliberately matches the existing
 * app style (compact, sans, centered narrow card) rather than the editorial
 * welcome screen — the serif/full-bleed treatment is reserved for the welcome
 * brand moment only. Width is configurable so the team grid can go wider than
 * the single-column config steps.
 */
export const OnboardingStep = ({
  title,
  subtitle,
  width = "md",
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  /** md = single-column config (~460px); lg = the 2×2 team grid (~600px). */
  width?: "md" | "lg";
  children: ReactNode;
  footer?: ReactNode;
}) => {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-20">
      <div
        className={`w-full ${width === "lg" ? "max-w-[600px]" : "max-w-[460px]"}`}
      >
        <h1 className="text-lg font-semibold text-ink-heading">{title}</h1>
        {subtitle && (
          <p className="mt-1 text-sm leading-6 text-ink-body">{subtitle}</p>
        )}

        <div className="mt-6">{children}</div>

        {footer && <div className="mt-6">{footer}</div>}
      </div>
    </main>
  );
};
