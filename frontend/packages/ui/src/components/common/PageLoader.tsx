import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { assetUrl } from "@valuz/shared";
import { cn } from "../../lib/cn";

export interface PageLoaderProps {
  className?: string;
  label?: string;
  children?: ReactNode;
  /** Show logo shimmer instead of spinner. */
  logo?: boolean;
}

export const LogoShimmer = ({ size = "sm" }: { size?: "sm" | "md" }) => {
  const dim = size === "sm" ? "h-6 w-6" : "h-8 w-8";
  const logoUrl = assetUrl("logo.png");
  return (
    <div className={cn("relative select-none", dim)}>
      <img
        src={logoUrl}
        alt=""
        aria-hidden="true"
        className="h-full w-full"
        draggable={false}
      />
      <div
        className="pointer-events-none absolute inset-0 overflow-hidden"
        style={{
          maskImage: `url(${logoUrl})`,
          maskSize: "contain",
          maskRepeat: "no-repeat",
          maskPosition: "center",
          WebkitMaskImage: `url(${logoUrl})`,
          WebkitMaskSize: "contain",
          WebkitMaskRepeat: "no-repeat",
          WebkitMaskPosition: "center",
        }}
      >
        <div className="absolute inset-y-0 left-0 w-1/2 animate-[shimmer_1.6s_linear_infinite] bg-gradient-to-r from-transparent via-white/85 to-transparent" />
      </div>
    </div>
  );
};

export const PageLoader = ({
  className,
  label = "Loading",
  children,
  logo = false,
}: PageLoaderProps) => (
  <div
    className={cn("flex items-center justify-center", className)}
    role="status"
    aria-label={label}
  >
    {children ??
      (logo ? (
        <LogoShimmer />
      ) : (
        <Loader2 className="h-6 w-6 animate-spin text-ink-muted" />
      ))}
  </div>
);
