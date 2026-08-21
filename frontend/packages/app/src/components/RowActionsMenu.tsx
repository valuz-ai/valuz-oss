import { useTranslation } from "@valuz/core";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  ForkIcon,
  cn,
} from "@valuz/ui";
import { FilePenLine, Loader2, MoreHorizontal, Trash2 } from "lucide-react";

// Hover-revealed "⋯" actions for a list row, mirroring the sidebar's
// ``moreActions`` menu. Rendered INSIDE the status pill's slot (a ``relative``
// wrapper) and centered over it, so on hover the status fades out and this
// fades in centered on the same area — no layout shift. The slot wrapper owns
// the positioning context; the row element must be a ``<div role="button">``
// (not a ``<button>``) so nesting this trigger ``<button>`` stays valid markup.
export const RowActionsMenu = ({
  onRename,
  onDelete,
  onFork,
  forkPending,
  forkDisabled,
}: {
  onRename: () => void;
  onDelete: () => void;
  /** Whole-session fork (docs/design/session-fork.md); absent → no entry. */
  onFork?: () => void;
  /** THIS row's fork request is in flight (forks can take seconds on
   * remote-kernel deployments — #879): the trigger swaps to an always-visible
   * spinner so the row keeps showing feedback after the menu closed. */
  forkPending?: boolean;
  /** A fork is in flight somewhere — the Fork entry is disabled so a second
   * one can't be queued from another row before the first settles. */
  forkDisabled?: boolean;
}) => {
  const { t } = useTranslation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          className={cn(
            "absolute left-1/2 top-1/2 flex h-6 w-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-md text-ink-body opacity-0 transition-opacity hover:bg-surface-muted focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100",
            forkPending && "opacity-100",
          )}
          aria-label={t("sidebar.moreActions" as Parameters<typeof t>[0])}
        >
          {forkPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <MoreHorizontal className="h-3.5 w-3.5" />
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="min-w-[140px]"
        onCloseAutoFocus={(e) => e.preventDefault()}
        // The content is portaled, but React events still bubble through the
        // component tree — a click on an item would otherwise reach the row's
        // ``onClick`` (which navigates into the conversation/task) and the
        // navigation would unmount the rename prompt / delete dialog. Stop it
        // here so the menu actions actually run.
        onClick={(e) => e.stopPropagation()}
      >
        <DropdownMenuItem onSelect={onRename}>
          <FilePenLine />
          {t("sidebar.rename" as Parameters<typeof t>[0])}
        </DropdownMenuItem>
        {onFork && (
          <DropdownMenuItem
            disabled={forkPending || forkDisabled}
            onSelect={onFork}
          >
            {forkPending ? <Loader2 className="animate-spin" /> : <ForkIcon />}
            {t("sidebar.fork" as Parameters<typeof t>[0])}
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={onDelete}>
          <Trash2 />
          {t("common.delete" as Parameters<typeof t>[0])}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};
