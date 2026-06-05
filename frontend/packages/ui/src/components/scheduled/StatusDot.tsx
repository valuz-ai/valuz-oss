import { cn } from "../../lib/cn";

export interface StatusDotProps {
  status: "on" | "off" | "ok" | "err" | "skip" | "pending";
  className?: string;
}

export const StatusDot = ({ status, className }: StatusDotProps) => {
  const tone =
    status === "on" || status === "ok"
      ? "bg-success"
      : status === "err"
        ? "bg-error"
        : status === "pending"
          ? "bg-brand"
          : "bg-ink-muted";
  const animate = status === "pending" ? "animate-pulse" : "";
  return (
    <span
      className={cn(
        "inline-block h-1.5 w-1.5 rounded-full",
        tone,
        animate,
        className,
      )}
    />
  );
};
