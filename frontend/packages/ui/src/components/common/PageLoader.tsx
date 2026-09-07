import type { ReactNode } from "react";
import { assetUrl } from "@valuz/shared";
import { cn } from "../../lib/cn";
import { LoadingState } from "./LoadingState";

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
}: PageLoaderProps) =>
  children || logo ? (
    <div
      className={cn(
        "flex w-full justify-center py-16",
        className,
      )}
      role="status"
      aria-label={label}
    >
      {children ?? <LogoShimmer />}
    </div>
  ) : (
    <LoadingState
      variant="page"
      label={label === "Loading" ? undefined : label}
      className={className}
    />
  );
