import { useRef, useState } from "react";
import {
  Archive,
  FileText,
  Folder,
  Loader2,
  Sparkles,
  Upload,
} from "lucide-react";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  SkillLinkImport,
  cn,
} from "@valuz/ui";
import type { LinkPreview } from "@valuz/ui";
import {
  useTranslation,
  type SkillCreationContext,
  type SkillImportArchivePreview,
  type SkillImportCandidate,
} from "@valuz/core";
import { toast } from "sonner";

type ImportMode = "ai" | "link" | "upload";
type UploadKind = "archive";

export interface SkillAddDialogCallbacks {
  onArchivePreview: (file: File) => Promise<SkillImportArchivePreview>;
  onArchiveConfirm: (data: {
    preview_id: string;
    name?: string;
  }) => Promise<{ name: string }>;
  /** Navigate to the skill-creator draft conversation for this context.
   *  Draft-first: the session is minted on the user's first send (with
   *  the agent they picked in the composer), not when the dialog closes —
   *  so the conversation gets the same default-agent + switching UX as
   *  新对话. */
  onStartAiCreate: (context: SkillCreationContext) => void;
  onLinkPreview: (url: string) => Promise<SkillImportArchivePreview>;
  onLinkConfirm: (data: {
    preview_id: string;
    name: string;
  }) => Promise<{ name: string }>;
}

interface SkillAddDialogProps extends SkillAddDialogCallbacks {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onComplete: () => void;
  mode?: ImportMode;
  creationContext?: SkillCreationContext;
}

export function SkillAddDialog({
  open,
  onOpenChange,
  onComplete,
  mode = "ai",
  creationContext,
  onArchivePreview,
  onArchiveConfirm,
  onStartAiCreate,
  onLinkPreview,
  onLinkConfirm,
}: SkillAddDialogProps) {
  const { t } = useTranslation();
  const [newSkillName, setNewSkillName] = useState("");

  const archiveInputRef = useRef<HTMLInputElement>(null);
  const [uploadKind, setUploadKind] = useState<UploadKind | null>(null);
  const [uploadPreview, setUploadPreview] =
    useState<SkillImportArchivePreview | null>(null);
  const [uploadPreviewing, setUploadPreviewing] = useState(false);
  const [dragActive, setDragActive] = useState(false);

  const [linkPreview, setLinkPreview] = useState<LinkPreview | null>(null);
  const [linkPreviewId, setLinkPreviewId] = useState<string | null>(null);
  const [linkCandidates, setLinkCandidates] = useState<SkillImportCandidate[]>(
    [],
  );
  const [linkSelectedIds, setLinkSelectedIds] = useState<string[]>([]);
  const [linkFetching, setLinkFetching] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);

  const resetForm = () => {
    setNewSkillName("");
    setUploadKind(null);
    setUploadPreview(null);
    setUploadPreviewing(false);
    setDragActive(false);
    setLinkPreview(null);
    setLinkCandidates([]);
    setLinkSelectedIds([]);
    setLinkFetching(false);
    setLinkError(null);
    setLinkPreviewId(null);
  };

  const handleClose = () => {
    onOpenChange(false);
    resetForm();
  };

  /* ── Upload handlers ──────────────────────────────────── */

  const handleArchiveFile = async (file: File) => {
    setUploadPreviewing(true);
    try {
      const preview = await onArchivePreview(file);
      setUploadKind("archive");
      setUploadPreview(preview);
      setNewSkillName(preview.name);
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("common.error" as Parameters<typeof t>[0]),
      );
    } finally {
      setUploadPreviewing(false);
    }
  };

  const handleUploadConfirm = async () => {
    if (!uploadPreview || !uploadKind) return;
    setSubmitting(true);
    try {
      await onArchiveConfirm({
        preview_id: uploadPreview.preview_id,
        name: newSkillName || undefined,
      });
      handleClose();
      onComplete();
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("common.saveFailed" as Parameters<typeof t>[0]),
      );
    } finally {
      setSubmitting(false);
    }
  };

  /* ── AI / Link handlers ──────────────────────────────── */

  const handleStartCreateChat = () => {
    const context: SkillCreationContext = creationContext ?? {
      kind: "skills_library",
    };
    handleClose();
    onStartAiCreate(context);
  };

  const handleLinkFetch = async (url: string) => {
    setLinkFetching(true);
    setLinkError(null);
    setLinkCandidates([]);
    setLinkSelectedIds([]);
    try {
      const preview = await onLinkPreview(url);
      setLinkPreviewId(preview.preview_id);
      setLinkPreview({
        name: preview.name,
        description: preview.description,
        files: preview.file_tree
          .filter((f) => f.type === "file")
          .map((f) => ({ path: f.path, size: f.size ?? 0 })),
      });
      // A collection/plugin URL yields >1 skill — surface them all and
      // default to importing every one (user can uncheck).
      const candidates = preview.skills ?? [];
      if (candidates.length > 1) {
        setLinkCandidates(candidates);
        setLinkSelectedIds(candidates.map((c) => c.preview_id));
      }
    } catch (err) {
      setLinkError(
        err instanceof Error
          ? err.message
          : t("skill.cannotAccessLink" as Parameters<typeof t>[0]),
      );
    } finally {
      setLinkFetching(false);
    }
  };

  const toggleLinkCandidate = (previewId: string) => {
    setLinkSelectedIds((prev) =>
      prev.includes(previewId)
        ? prev.filter((id) => id !== previewId)
        : [...prev, previewId],
    );
  };

  const handleLinkImport = async () => {
    const isMulti = linkCandidates.length > 1;

    if (isMulti) {
      const chosen = linkCandidates.filter((c) =>
        linkSelectedIds.includes(c.preview_id),
      );
      if (chosen.length === 0) return;
      setSubmitting(true);
      // Confirm is one call per skill (each candidate has its own preview_id).
      const results = await Promise.allSettled(
        chosen.map((c) =>
          onLinkConfirm({ preview_id: c.preview_id, name: c.name }),
        ),
      );
      const failed = results.filter((r) => r.status === "rejected").length;
      setSubmitting(false);
      if (failed === 0) {
        toast.success(
          t("skill.importedCount" as Parameters<typeof t>[0], {
            count: chosen.length,
          }),
        );
        handleClose();
        onComplete();
      } else if (failed < chosen.length) {
        toast.warning(
          t("skill.importedPartial" as Parameters<typeof t>[0], {
            ok: chosen.length - failed,
            failed,
          }),
        );
        onComplete();
      } else {
        toast.error(t("skill.importAllFailed" as Parameters<typeof t>[0]));
      }
      return;
    }

    if (!linkPreview || !linkPreviewId) return;
    setSubmitting(true);
    try {
      await onLinkConfirm({
        preview_id: linkPreviewId,
        name: linkPreview.name,
      });
      handleClose();
      setLinkPreviewId(null);
      onComplete();
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("skill.operationFailed" as Parameters<typeof t>[0]),
      );
    } finally {
      setSubmitting(false);
    }
  };

  /* ── Render ────────────────────────────────────────────── */

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(o) => {
          if (!o) handleClose();
        }}
      >
        <DialogContent className="flex max-h-[min(720px,calc(100vh-2rem))] max-w-2xl flex-col gap-0 overflow-hidden p-0">
          <DialogHeader>
            <DialogTitle className="px-6 pt-6">
              {getDialogTitle(mode, t)}
            </DialogTitle>
            {mode !== "upload" && (
              <DialogDescription className="px-6">
                {getDialogDescription(mode, t)}
              </DialogDescription>
            )}
          </DialogHeader>

          <div
            className={cn(
              "min-h-0 flex-1 overflow-y-auto px-6 pt-5",
              mode === "upload" ? "pb-0" : "pb-5",
              mode === "ai" && "border-t border-surface-border",
            )}
          >
            {mode === "ai" && (
              <div className="flex min-h-[360px] flex-col justify-between rounded-md border border-surface-border bg-surface-soft p-5">
                <div>
                  <div className="flex items-start gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-brand-light text-brand">
                      <Sparkles className="h-5 w-5" />
                    </div>
                    <div>
                      <h3 className="text-sm font-medium text-ink-heading">
                        {t("skill.aiCreate" as Parameters<typeof t>[0])}
                      </h3>
                      <p className="mt-2 max-w-xl text-sm leading-6 text-ink-body">
                        {t("skill.openCreatorDesc" as Parameters<typeof t>[0])}
                      </p>
                    </div>
                  </div>
                </div>
                <div className="mt-8 flex justify-end">
                  <Button onClick={handleStartCreateChat}>
                    <Sparkles className="h-4 w-4" />
                    {t("skill.openCreator" as Parameters<typeof t>[0])}
                  </Button>
                </div>
              </div>
            )}

            {mode === "link" && (
              <SkillLinkImport
                onFetch={handleLinkFetch}
                onImport={handleLinkImport}
                onCancel={handleClose}
                preview={linkPreview}
                candidates={linkCandidates}
                selectedIds={linkSelectedIds}
                onToggleCandidate={toggleLinkCandidate}
                fetching={linkFetching}
                importing={submitting}
                error={linkError}
                className="!p-0"
              />
            )}

            {mode === "upload" && (
              <div className="space-y-3">
                <div
                  className={cn(
                    "flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed bg-surface-soft px-6 py-10 text-center transition-colors",
                    dragActive
                      ? "border-brand bg-brand-light/40"
                      : "border-surface-border-hover hover:border-brand/40",
                  )}
                  onClick={() => archiveInputRef.current?.click()}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragActive(true);
                  }}
                  onDragLeave={() => setDragActive(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragActive(false);
                    const f = e.dataTransfer.files?.[0];
                    if (f) void handleArchiveFile(f);
                  }}
                >
                  {uploadPreviewing ? (
                    <>
                      <Loader2 className="h-8 w-8 animate-spin text-brand" />
                      <p className="mt-3 text-sm text-ink-body">
                        {t("common.processing" as Parameters<typeof t>[0])}
                      </p>
                    </>
                  ) : uploadPreview && uploadKind === "archive" ? (
                    <>
                      <Archive className="h-8 w-8 text-brand" />
                      <p className="mt-3 max-w-full truncate text-sm font-medium text-ink-heading">
                        {uploadPreview.name}
                      </p>
                      <p className="mt-1 text-xs text-ink-meta">
                        {t("common.refresh" as Parameters<typeof t>[0])}
                      </p>
                    </>
                  ) : (
                    <>
                      <Upload className="h-8 w-8 text-ink-muted" />
                      <p className="mt-3 text-sm text-ink-body">
                        {t("skill.uploadHint" as Parameters<typeof t>[0])}
                      </p>
                      <p className="mt-1 text-xs text-ink-meta">
                        {t("skill.uploadHintDetail" as Parameters<typeof t>[0])}
                      </p>
                    </>
                  )}
                </div>

                {uploadPreview && (
                  <ImportPreviewCard
                    preview={uploadPreview}
                    name={newSkillName}
                    onNameChange={setNewSkillName}
                  />
                )}
              </div>
            )}
          </div>

          {mode === "upload" && (
            <DialogFooter className="px-6 pt-5 pb-4">
              <Button
                variant="outline"
                onClick={handleClose}
                disabled={submitting}
              >
                {t("common.cancel" as Parameters<typeof t>[0])}
              </Button>
              <Button
                onClick={handleUploadConfirm}
                loading={submitting}
                disabled={!uploadPreview}
              >
                {t("common.import" as Parameters<typeof t>[0])}
              </Button>
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>

      <input
        ref={archiveInputRef}
        type="file"
        accept=".zip,.tar,.tar.gz,.tgz"
        className="sr-only"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleArchiveFile(file);
          if (archiveInputRef.current) archiveInputRef.current.value = "";
        }}
      />
    </>
  );
}

/* ── Import preview sub-component ───────────────────────── */

function ImportPreviewCard({
  preview,
  name,
  onNameChange,
}: {
  preview: SkillImportArchivePreview | null;
  name: string;
  onNameChange: (v: string) => void;
}) {
  const { t } = useTranslation();
  if (!preview) {
    return (
      <div className="flex min-h-[280px] flex-col items-center justify-center rounded-md border border-dashed border-surface-border bg-surface-soft px-4 py-8 text-center">
        <FileText className="h-6 w-6 text-ink-muted" />
        <p className="mt-3 text-sm font-medium text-ink-heading">
          {t("common.preview" as Parameters<typeof t>[0])}
        </p>
        <p className="mt-1 max-w-[220px] text-xs leading-5 text-ink-meta">
          {t("skill.uploadHintDetail" as Parameters<typeof t>[0])}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-surface-border bg-surface-soft p-4">
      <div className="mb-2 flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-brand" />
        <span className="text-sm font-medium text-ink-heading">
          {t("common.preview" as Parameters<typeof t>[0])}
        </span>
        {preview.name_conflict && (
          <Badge variant="outline" className="text-warning">
            {t("skill.nameConflict" as Parameters<typeof t>[0])}
          </Badge>
        )}
      </div>
      <div className="mb-2">
        <label className="mb-1 block text-xs text-ink-meta">
          {t("skill.importName" as Parameters<typeof t>[0])}
        </label>
        <Input
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          className="h-8 text-sm"
        />
        {preview.suggested_name && (
          <p className="mt-1 text-xs text-ink-meta">
            {t("skill.suggestion" as Parameters<typeof t>[0])}{" "}
            <button
              type="button"
              className="text-brand underline"
              onClick={() => onNameChange(preview.suggested_name!)}
            >
              {preview.suggested_name}
            </button>
          </p>
        )}
      </div>
      <p className="mb-2 text-xs text-ink-body">{preview.description}</p>
      {preview.file_tree.length > 0 && (
        <div className="max-h-[300px] overflow-y-auto rounded-md border border-surface-border bg-background px-2 py-1 font-mono text-xs leading-6 text-ink-label">
          {preview.file_tree.map((f) => (
            <div key={f.path} className="flex items-center gap-1.5">
              {f.type === "directory" ? (
                <Folder className="h-3 w-3 text-brand-500" />
              ) : (
                <FileText className="h-3 w-3 text-ink-muted" />
              )}
              <span
                className={
                  f.path.endsWith("SKILL.md") ? "font-medium text-brand" : ""
                }
              >
                {f.path}
              </span>
              {f.size != null && (
                <span className="text-ink-meta">
                  ({(f.size / 1024).toFixed(1)}K)
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function getDialogTitle(
  mode: ImportMode,
  t: ReturnType<typeof useTranslation>["t"],
) {
  if (mode === "ai") {
    return t("skill.aiCreate" as Parameters<typeof t>[0]);
  }
  if (mode === "link") {
    return t("skill.linkImport" as Parameters<typeof t>[0]);
  }
  return t("skill.upload" as Parameters<typeof t>[0]);
}

function getDialogDescription(
  mode: ImportMode,
  t: ReturnType<typeof useTranslation>["t"],
) {
  if (mode === "ai")
    return t("skill.openCreatorDesc" as Parameters<typeof t>[0]);
  if (mode === "link")
    return t("skill.linkImportDesc" as Parameters<typeof t>[0]);
  return t("skill.uploadHintDetail" as Parameters<typeof t>[0]);
}
