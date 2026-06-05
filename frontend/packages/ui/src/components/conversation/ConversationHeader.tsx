import { useState, useRef, useEffect } from "react";
import { Link2, RotateCw, ChevronDown, Check } from "lucide-react";
import { Button } from "../ui/button";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

const MODELS = [
  { id: "claude-sonnet", label: "Claude Sonnet 4.5" },
  { id: "claude-opus", label: "Claude Opus 4.6" },
  { id: "gpt-5", label: "GPT-5" },
  { id: "deepseek", label: "DeepSeek V3" },
];

export interface ConversationHeaderProps {
  title: string;
  model?: string;
  locked?: boolean;
  onTitleChange?: (title: string) => void;
  onModelChange?: (model: string) => void;
  onCopyLink?: () => void;
  onRefresh?: () => void;
}

export const ConversationHeader = ({
  title,
  model = "claude-sonnet",
  locked = false,
  onTitleChange,
  onModelChange,
  onCopyLink,
  onRefresh,
}: ConversationHeaderProps) => {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const [modelOpen, setModelOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!editing) setDraft(title);
  }, [title, editing]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  // Close dropdown on outside click
  useEffect(() => {
    if (!modelOpen) return;
    const handler = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      )
        setModelOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [modelOpen]);

  const finishEdit = () => {
    setEditing(false);
    if (draft.trim() && draft !== title) onTitleChange?.(draft.trim());
  };

  const currentModel = MODELS.find((m) => m.id === model) ?? MODELS[0];

  return (
    <div className="flex w-full items-center gap-2">
      {/* Title — editable */}
      {editing ? (
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={finishEdit}
          onKeyDown={(e) => {
            if (e.key === "Enter") finishEdit();
            if (e.key === "Escape") {
              setDraft(title);
              setEditing(false);
            }
          }}
          className="min-w-0 flex-1 truncate rounded border border-brand/30 bg-surface px-2 py-0.5 text-base font-medium text-ink-heading focus:outline-none"
        />
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="min-w-0 flex-1 truncate text-left text-base font-medium text-ink-heading transition-colors hover:text-brand"
          title={t("conversation.clickEditTitle")}
        >
          {title}
        </button>
      )}

      {/* Model selector */}
      <div className="relative" ref={dropdownRef}>
        <button
          type="button"
          onClick={() => !locked && setModelOpen(!modelOpen)}
          className={cn(
            "flex items-center gap-1 rounded-md px-2 py-1 text-2xs font-mono transition",
            locked
              ? "text-ink-meta cursor-default"
              : "text-ink-meta hover:bg-surface-soft",
          )}
        >
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              locked ? "bg-ink-muted" : "bg-success",
            )}
          />
          {currentModel.label}
          {!locked && <ChevronDown className="h-3 w-3" />}
        </button>

        {modelOpen && (
          <div className="absolute right-0 top-full z-50 mt-1 min-w-[180px] rounded-lg border border-surface-border bg-surface py-1 shadow-md">
            {MODELS.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => {
                  onModelChange?.(m.id);
                  setModelOpen(false);
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-ink-label hover:bg-surface-muted"
              >
                {model === m.id && <Check className="h-3 w-3 text-brand" />}
                <span className={model === m.id ? "" : "ml-5"}>{m.label}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1">
        {onCopyLink ? (
          <Button variant="ghost" size="icon-sm" onClick={onCopyLink}>
            <Link2 className="h-4 w-4" />
          </Button>
        ) : null}
        {onRefresh ? (
          <Button variant="ghost" size="icon-sm" onClick={onRefresh}>
            <RotateCw className="h-4 w-4" />
          </Button>
        ) : null}
      </div>
    </div>
  );
};
