import {
  Check,
  Zap,
  Loader2,
  X,
} from "lucide-react";
import { Toaster as SonnerToaster } from "sonner";

const ToastInfoIcon = () => (
  <svg
    aria-hidden="true"
    className="h-[11px] w-[11px]"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <line x1="12" y1="6" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);

export const AppToaster = () => (
  <SonnerToaster
    theme="light"
    position="bottom-right"
    icons={{
      success: <Check className="h-[11px] w-[11px]" />,
      error: <X className="h-[11px] w-[11px]" />,
      info: <ToastInfoIcon />,
      warning: <Zap className="h-[11px] w-[11px]" />,
      loading: <Loader2 className="h-[11px] w-[11px] animate-spin" />,
    }}
    toastOptions={{
      classNames: {
        toast:
          "flex !w-[300px] !items-start !gap-2.5 !rounded-xl !border !border-surface-border !bg-surface !px-3.5 !py-[11px] !text-sm !text-ink-heading !shadow-3",
        title: "text-sm font-semibold leading-[1.35] !text-ink-heading",
        description: "text-xs leading-[1.45] !text-ink-body",
        icon: "!mt-0 !ml-0 !mr-0 flex !h-[18px] !w-[18px] shrink-0 !items-center !justify-center rounded-full text-white [&_svg]:!m-0 [&_svg]:stroke-[2]",
        success: "[&_[data-icon]]:bg-success",
        error: "[&_[data-icon]]:bg-error",
        info: "[&_[data-icon]]:bg-brand",
        warning: "[&_[data-icon]]:bg-warning-text [&_[data-icon]]:text-white",
        loading: "[&_[data-icon]]:bg-brand",
      },
    }}
  />
);
