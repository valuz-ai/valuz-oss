import type { FC } from "react";
import { cn } from "../../lib/cn";
import { statusPillClass, statusPulses } from "./status-tone";

export interface StatusPillProps {
  status: string;
  /** Display text — callers own their own i18n keys. */
  label: string;
  className?: string;
}

/**
 * Soft-pill status tag with the shared per-status color (see
 * docs/desktop/status-colors.md). ``running`` / ``active`` pills carry a
 * pulsing dot. Color tones live in ``./status-tone``.
 */
export const StatusPill: FC<StatusPillProps> = ({
  status,
  label,
  className,
}) => {
  const pulses = statusPulses(status);
  return (
    <span
      className={cn(
        // ``relative + overflow-hidden`` are only needed for the running
        // shimmer overlay below, but they're cheap on the static states.
        "relative inline-flex shrink-0 items-center gap-1 overflow-hidden rounded-full px-2 py-0.5 text-[11px] font-medium",
        statusPillClass(status),
        className,
      )}
    >
      {pulses && (
        <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
      )}
      {label}
      {pulses && (
        // Continuous highlight sweep across the whole pill (dot + label),
        // signalling "this run is still alive" alongside the dot's pulse.
        // The white-tinted gradient is translucent, so the dot and text
        // stay visible — it reads as a glint passing over the surface.
        // Re-uses the global ``shimmer`` keyframe (translateX -100% →
        // 200%) that PageLoader / LogoLoader already ride.
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 animate-[shimmer_2.4s_linear_infinite] bg-gradient-to-r from-transparent via-white/55 to-transparent"
        />
      )}
    </span>
  );
};
