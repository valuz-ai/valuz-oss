import { useState, type ReactNode } from "react";
import {
  CheckSquare,
  ChevronRight,
  Database,
  FileText,
  Paperclip,
  BookOpen,
  X,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

/* ── Data types ────────────────────────────────────────────── */

export interface TodoItem {
  id: string;
  text: string;
  done: boolean;
}

export interface SessionFile {
  /** Stable id from the attachment row — required when a remove
   * affordance is rendered so the parent can route the delete to
   * the right backend row. Optional for back-compat with the few
   * usages that render display-only rows. */
  id?: string;
  name: string;
  /** Optional size label, e.g. "2.3 MB" */
  size?: string;
  /** ``local`` (multipart upload) or ``kb_doc`` (live reference to a
   * KB document). Drives the row icon — knowledge-base picks render
   * a ``Database`` glyph so users can tell the two paths apart at a
   * glance. Defaults to ``local`` for back-compat with older callers. */
  sourceKind?: "local" | "kb_doc";
}

export interface KBFile {
  id: string;
  name: string;
  indexing?: boolean;
}

/* ── Props ─────────────────────────────────────────────────── */

export interface SessionContextPanelProps {
  todos?: TodoItem[];
  generatedFiles?: SessionFile[];
  uploadedFiles?: SessionFile[];
  kbFiles?: KBFile[];
  onToggleTodo?: (id: string) => void;
  onRemoveKBFile?: (id: string) => void;
  onFileClick?: (name: string) => void;
  /** Remove an uploaded attachment from the session. Renders an
   * inline ``X`` affordance per row when wired; KB-sourced and
   * local rows share the same handler — the backend routes the
   * disk-cleanup vs row-only delete by ``source_kind``. */
  onRemoveUploadedFile?: (id: string) => void;
  /** Collapse/expand controlled from outside */
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

/* ── Collapsible section ───────────────────────────────────── */

const Section = ({
  icon,
  title,
  badge,
  defaultOpen = true,
  children,
}: {
  icon: ReactNode;
  title: string;
  badge?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="overflow-hidden rounded-[10px] border border-surface-border bg-surface-soft">
      <button
        type="button"
        className="flex h-10 w-full items-center gap-2 px-3 transition-colors hover:bg-surface-muted/60"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-ink-body transition-transform",
            open && "rotate-90",
          )}
        />
        <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center text-ink-body">
          {icon}
        </span>
        <span className="text-xs font-medium tracking-[0.8px] text-ink-heading">
          {title}
        </span>
        {badge ? (
          <span className="ml-auto text-xs text-ink-meta">{badge}</span>
        ) : null}
      </button>
      {open ? <div className="px-4 pb-3.5">{children}</div> : null}
    </div>
  );
};

/* ── Main component ────────────────────────────────────────── */

export const SessionContextPanel = ({
  todos = [],
  generatedFiles = [],
  uploadedFiles = [],
  kbFiles = [],
  onToggleTodo,
  onRemoveKBFile,
  onFileClick,
  onRemoveUploadedFile,
  collapsed = false,
  onToggleCollapse,
}: SessionContextPanelProps) => {
  const { t } = useI18n();
  const doneCount = todos.filter((t) => t.done).length;
  const hasContent =
    todos.length > 0 ||
    generatedFiles.length > 0 ||
    uploadedFiles.length > 0 ||
    kbFiles.length > 0;

  // Collapsed: narrow strip with expand button only
  if (collapsed) {
    return (
      <div className="flex h-full w-10 flex-col items-center border-l border-surface-border bg-surface pt-3">
        <button
          type="button"
          onClick={onToggleCollapse}
          className="flex h-7 w-7 items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft"
          title={t("conversation.expandPanel", "展开 Panel")}
        >
          <PanelRightOpen className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full w-[260px] flex-col border-l border-surface-border bg-surface">
      {/* Header */}
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-surface-border px-5">
        <span className="text-sm font-medium text-ink-heading">
          Session Context
        </span>
        <button
          type="button"
          onClick={onToggleCollapse}
          className="flex h-7 w-7 items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft"
          title={t("conversation.collapsePanel", "收起 Panel")}
        >
          <PanelRightClose className="h-4 w-4" />
        </button>
      </div>

      {/* Body */}
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
        {!hasContent && (
          <div className="flex flex-1 items-center justify-center">
            <p className="text-xs text-ink-body">
              {t("conversation.sessionContextEmpty", "对话产出物将在这里展示")}
            </p>
          </div>
        )}

        {/* TODO section */}
        {todos.length > 0 && (
          <Section
            icon={<CheckSquare className="h-3.5 w-3.5" />}
            title={t("conversation.todos", t("conversation.todos"))}
            badge={`${doneCount}/${todos.length}`}
          >
            <div className="space-y-1.5">
              {todos.map((todo) => (
                <label key={todo.id} className="flex items-start gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={todo.done}
                    onChange={() => onToggleTodo?.(todo.id)}
                    className="mt-0.5 h-3.5 w-3.5 rounded border-surface-border text-brand accent-brand"
                  />
                  <span
                    className={cn(
                      "leading-5",
                      todo.done
                        ? "text-ink-body line-through decoration-ink-body"
                        : "text-ink-heading",
                    )}
                  >
                    {todo.text}
                  </span>
                </label>
              ))}
            </div>
          </Section>
        )}

        {/* Generated files */}
        {generatedFiles.length > 0 && (
          <Section
            icon={<FileText className="h-3.5 w-3.5" />}
            title={t(
              "conversation.generatedFiles",
              t("conversation.generatedFiles"),
            )}
            badge={`${generatedFiles.length}个`}
          >
            <div className="space-y-1.5">
              {generatedFiles.map((file) => (
                <button
                  key={file.name}
                  type="button"
                  className="flex w-full items-center gap-2 rounded-lg border border-surface-border bg-surface px-3 py-2 text-left transition-colors hover:bg-surface-muted"
                  onClick={() => onFileClick?.(file.name)}
                >
                  <FileText className="h-3.5 w-3.5 shrink-0 text-ink-meta" />
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-heading">
                    {file.name}
                  </span>
                </button>
              ))}
            </div>
          </Section>
        )}

        {/* Uploaded files — unified panel for local uploads and KB
            picks. ``sourceKind`` drives the row icon so the user can
            tell "this came from my disk" apart from "this came from
            the KB" at a glance. The row id is used to route the
            optional remove affordance to the right attachment row. */}
        {uploadedFiles.length > 0 && (
          <Section
            icon={<Paperclip className="h-3.5 w-3.5" />}
            title={t(
              "conversation.uploadedFiles",
              t("conversation.uploadedFiles"),
            )}
            badge={`${uploadedFiles.length}个`}
          >
            <div className="space-y-1.5">
              {uploadedFiles.map((file) => {
                const isKb = file.sourceKind === "kb_doc";
                const Icon = isKb ? Database : Paperclip;
                const rowKey =
                  file.id ?? `${file.sourceKind ?? "local"}:${file.name}`;
                return (
                  <div
                    key={rowKey}
                    className="group flex items-center gap-2 rounded-lg border border-surface-border bg-surface px-3 py-2 transition-colors hover:bg-surface-muted"
                  >
                    <Icon
                      className={cn(
                        "h-3.5 w-3.5 shrink-0",
                        isKb ? "text-[#1d4ed8]" : "text-ink-meta",
                      )}
                    />
                    <span className="min-w-0 flex-1 truncate text-xs text-ink-heading">
                      {file.name}
                    </span>
                    {file.size && !onRemoveUploadedFile ? (
                      <span className="text-2xs text-ink-meta">
                        {file.size}
                      </span>
                    ) : null}
                    {onRemoveUploadedFile && file.id ? (
                      <button
                        type="button"
                        onClick={() => onRemoveUploadedFile(file.id!)}
                        className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-ink-meta opacity-0 transition-opacity hover:text-red-500 group-hover:opacity-100"
                        title={t("common.remove", t("common.remove"))}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </Section>
        )}

        {/* KB files */}
        {kbFiles.length > 0 && (
          <Section
            icon={<BookOpen className="h-3.5 w-3.5" />}
            title={t("conversation.kbFiles", t("conversation.kbFiles"))}
            badge={`${kbFiles.length}个`}
          >
            <div className="space-y-1.5">
              {kbFiles.map((file) => (
                <div
                  key={file.id}
                  className="flex items-center gap-2 rounded-lg border border-surface-border bg-surface px-3 py-2"
                >
                  <BookOpen className="h-3.5 w-3.5 shrink-0 text-ink-meta" />
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-heading">
                    {file.name}
                  </span>
                  {file.indexing ? (
                    <span className="text-2xs text-brand">
                      {t("conversation.indexing", t("conversation.indexing"))}
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onRemoveKBFile?.(file.id)}
                      className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-ink-meta transition-colors hover:bg-surface-soft hover:text-red-500"
                      title={t("common.remove", t("common.remove"))}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </Section>
        )}
      </div>
    </div>
  );
};
