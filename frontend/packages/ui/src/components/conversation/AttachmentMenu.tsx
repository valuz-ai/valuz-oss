import { useRef, useEffect } from "react";
import { FileUp, Database } from "lucide-react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface AttachmentMenuProps {
  onLocalUpload?: () => void;
  onKnowledgeBasePick?: () => void;
  onClose?: () => void;
  /**
   * When true, both entries are greyed out and non-interactive — the
   * session has hit its attachment cap (local + KB counted together).
   * ``disabledHint`` is shown beneath the entries so the user knows
   * why; the parent removes a file to free a slot.
   */
  disabled?: boolean;
  disabledHint?: string;
}

export const AttachmentMenu = ({
  onLocalUpload,
  onKnowledgeBasePick,
  onClose,
  disabled = false,
  disabledHint,
}: AttachmentMenuProps) => {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!onClose) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const entryClass = cn(
    "flex w-full items-center gap-2 rounded-md px-2 py-2 text-xs",
    disabled
      ? "cursor-not-allowed text-ink-muted"
      : "text-ink-label hover:bg-surface-muted",
  );

  return (
    <div
      ref={ref}
      className="absolute bottom-full left-0 z-50 mb-1 min-w-[180px] rounded-lg border border-surface-border bg-surface p-1 shadow-md"
    >
      <button
        type="button"
        disabled={disabled}
        className={entryClass}
        onClick={() => {
          if (disabled) return;
          onLocalUpload?.();
          onClose?.();
        }}
      >
        <FileUp className="h-3.5 w-3.5 text-ink-meta" />
        {t("conversation.localUpload")}
      </button>
      <button
        type="button"
        disabled={disabled}
        className={entryClass}
        onClick={() => {
          if (disabled) return;
          onKnowledgeBasePick?.();
          onClose?.();
        }}
      >
        <Database className="h-3.5 w-3.5 text-ink-meta" />
        {t("conversation.fromKnowledgeBase")}
      </button>
      {disabled && disabledHint ? (
        <p className="px-2 py-1 text-2xs text-ink-meta">{disabledHint}</p>
      ) : null}
    </div>
  );
};
