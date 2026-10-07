import type { ReactNode } from "react";
import { cn } from "@valuz/ui";
import type { ChangeTone } from "./dsh-helpers";

const NOTICE_TONE: Record<ChangeTone, string> = {
  success: "border-success-border bg-success-light text-success-text",
  warning: "border-warning-border bg-warning-light text-warning-text",
  error: "border-error-border bg-error-light text-error-text",
};

/**
 * A toned inline notice (semantic tokens only). Shared by the install result
 * and the backend "restart to apply" hint; an error notice is an ``alert``,
 * the others a polite ``status``.
 */
export const Notice = ({
  tone,
  children,
}: {
  tone: ChangeTone;
  children: ReactNode;
}) => (
  <div
    role={tone === "error" ? "alert" : "status"}
    className={cn(
      "space-y-1.5 rounded-md border px-3 py-2 text-xs leading-relaxed",
      NOTICE_TONE[tone],
    )}
  >
    {children}
  </div>
);
