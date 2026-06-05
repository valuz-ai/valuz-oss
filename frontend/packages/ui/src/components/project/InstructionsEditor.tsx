import {
  forwardRef,
  useState,
  useEffect,
  useRef,
  useCallback,
  useImperativeHandle,
} from "react";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Textarea } from "../ui/textarea";
import { useI18n } from "../../hooks/use-i18n";

export interface InstructionsEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hideEditAction?: boolean;
  showInlineEditAction?: boolean;
}

export interface InstructionsEditorHandle {
  openEditor: () => void;
}

export const InstructionsEditor = forwardRef<
  InstructionsEditorHandle,
  InstructionsEditorProps
>(function InstructionsEditor(
  {
    value,
    onChange,
    placeholder,
    hideEditAction = false,
    showInlineEditAction = true,
  },
  ref,
) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Sync external value changes
  useEffect(() => {
    if (!open) setDraft(value);
  }, [value, open]);

  const handleChange = (next: string) => {
    setDraft(next);
  };

  const openEditor = useCallback(() => {
    setDraft(value);
    setOpen(true);
    // Focus textarea on next tick
    setTimeout(() => textareaRef.current?.focus(), 0);
  }, [value]);

  useImperativeHandle(ref, () => ({ openEditor }), [openEditor]);

  const finishEdit = () => {
    onChange(draft);
    setOpen(false);
  };

  const isEmpty = !value.trim();
  const isLong = draft.length > 2000;

  return (
    <div>
      {isEmpty ? (
        <div className="rounded-lg bg-surface-soft px-3 py-3 text-xs text-ink-body">
          {placeholder || t("project.instructionPlaceholder")}
        </div>
      ) : (
        <div className="line-clamp-3 text-xs leading-relaxed text-ink-body whitespace-pre-wrap">
          {value}
        </div>
      )}
      {!hideEditAction && showInlineEditAction && (
        <Button
          variant="ghost"
          size="sm"
          className="mt-1.5 h-6 text-2xs hover:bg-surface-muted hover:text-ink-heading"
          onClick={openEditor}
        >
          {isEmpty ? t("project.writeInstructions") : t("common.edit")}
        </Button>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-[680px]">
          <DialogHeader>
            <DialogTitle>{t("project.instruction")}</DialogTitle>
            <DialogDescription className="sr-only">
              {t("project.instructionPlaceholder")}
            </DialogDescription>
          </DialogHeader>
          <Textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => handleChange(e.target.value)}
            placeholder={placeholder || t("project.instructionPlaceholder")}
            rows={14}
            className="min-h-[320px] font-mono text-xs"
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") finishEdit();
            }}
          />
          {isLong ? (
            <div className="text-2xs text-ink-meta">
              {t("project.instructionLong")}
            </div>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              className="text-ink-heading"
              onClick={() => setOpen(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button onClick={finishEdit}>{t("common.done")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
});
