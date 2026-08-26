import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  Check,
  FolderOpen,
  Loader2,
  Package,
  Plug,
  Sparkles,
  Users,
} from "lucide-react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
} from "@valuz/ui";
import {
  projectsApi,
  useTranslation,
  type ImportProjectConfirmResult,
  type ImportProjectPreview,
  type ProjectListItem,
} from "@valuz/core";
// Module-level ``t`` for one-shot messages inside effects: it reads the
// current locale per call and takes no part in React identity, so the effect
// below needs no ``t`` dependency (see .claude/rules/frontend.md).
import { t as _t } from "@valuz/shared/i18n";
import { usePlatform } from "../platform";

type Tx = ReturnType<typeof useTranslation>["t"];
const k = (key: string) => key as Parameters<Tx>[0];

/**
 * Upload → preview → confirm flow for a ``.valuzpack`` project bundle. The
 * parent owns the file pick and passes the chosen ``file``; this dialog
 * stages a preview (what's inside, what already exists, name-conflict
 * warning), commits on confirm, and shows the result — including
 * connectors the user still needs to wire up.
 *
 * Import UX: when the preview reports ``name_conflict``, a prominent
 * warning banner is shown and the confirm button is disabled (import
 * would skip anyway). When confirm returns
 * ``status === "skipped_name_conflict"`` the result view surfaces that.
 */
export function ImportProjectDialog({
  file,
  open,
  onOpenChange,
  onImported,
}: {
  file: File | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a successful import so the parent can refresh state. */
  onImported?: (project: ProjectListItem) => void;
}) {
  const { t } = useTranslation();
  const platform = usePlatform();
  const [preview, setPreview] = useState<ImportProjectPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ImportProjectConfirmResult | null>(null);
  const [rootPath, setRootPath] = useState("");
  // Editable on the way in: the pack's own name is only a default, and
  // renaming is how the importer gets past a clash with a project they
  // already own.
  const [name, setName] = useState("");

  // The parent passes a fresh inline ``onOpenChange`` on every render, so
  // depending on it re-ran the preview effect continuously: each pass reset
  // ``rootPath`` and re-uploaded the archive, and a folder the user had just
  // picked vanished a moment later. Hold it in a ref — the effect needs to
  // *call* it, never to react to it.
  const onOpenChangeRef = useRef(onOpenChange);
  useEffect(() => {
    onOpenChangeRef.current = onOpenChange;
  }, [onOpenChange]);

  const handlePickFolder = async () => {
    const path = await platform.selectDirectory();
    if (path) setRootPath(path);
  };

  useEffect(() => {
    if (!open || !file) return;
    let cancelled = false;
    // Defer state resets + fetch off the synchronous effect body (matches
    // the codebase's effect pattern / cascading-render lint).
    void Promise.resolve().then(async () => {
      if (cancelled) return;
      setPreview(null);
      setResult(null);
      setRootPath("");
      setName("");
      setLoading(true);
      try {
        const p = await projectsApi.importProjectPreview(file);
        if (!cancelled) {
          setPreview(p);
          setName(p.project?.name ?? "");
        }
      } catch (err) {
        if (cancelled) return;
        toast.error(
          err instanceof Error ? err.message : _t(k("project.importFailed")),
        );
        onOpenChangeRef.current(false);
      } finally {
        if (!cancelled) setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
    // Only the file being previewed may restart this: ``t`` and the callback
    // are read through stable references above.
  }, [open, file]);

  const handleConfirm = async () => {
    if (!preview) return;
    setImporting(true);
    try {
      const res = await projectsApi.importProjectConfirm(
        preview.preview_id,
        rootPath,
        name,
      );
      setResult(res);
      if (res.status === "created") {
        toast.success(
          t(k("project.importCreated"), {
            members: (res.members_created ?? 0) + (res.members_reused ?? 0),
            automations: res.automations_created ?? 0,
            agents: res.agents_created ?? 0,
          }),
        );
        if (res.project) onImported?.(res.project);
      } else {
        toast.warning(t(k("project.skipped")));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "";
      // 409 → the user-picked folder is already bound to another project.
      toast.error(
        message.includes("409") || message.toLowerCase().includes("bound")
          ? t(k("project.dirAlreadyBound"))
          : t(k("project.importFailed")),
      );
    } finally {
      setImporting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] flex-col overflow-hidden sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Package className="h-4 w-4 text-brand" />
            {name || preview?.project?.name || t(k("project.importTitle"))}
          </DialogTitle>
          {preview ? (
            <DialogDescription>{t(k("project.importSub"))}</DialogDescription>
          ) : null}
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-12 text-ink-meta">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : result ? (
          <ImportResultView result={result} />
        ) : preview ? (
          <ImportPreviewView
            preview={preview}
            name={name}
            onNameChange={setName}
            rootPath={rootPath}
            onPickFolder={() => void handlePickFolder()}
          />
        ) : null}

        {!result && preview ? (
          <div className="mt-2 flex justify-end">
            <Button
              onClick={() => void handleConfirm()}
              // A clash blocks only while the name is unchanged — editing it
              // is the way out, so the button unlocks as soon as it differs.
              disabled={
                importing ||
                !name.trim() ||
                (preview.name_conflict && name.trim() === preview.project?.name)
              }
              loading={importing}
            >
              {importing ? t(k("project.importing")) : t(k("project.confirm"))}
            </Button>
          </div>
        ) : null}
        {result ? (
          <div className="mt-2 flex justify-end">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t(k("project.done"))}
            </Button>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="text-2xs font-semibold uppercase tracking-wider text-ink-section">
        {title}
      </div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function ImportPreviewView({
  preview,
  name,
  onNameChange,
  rootPath,
  onPickFolder,
}: {
  preview: ImportProjectPreview;
  name: string;
  onNameChange: (name: string) => void;
  rootPath: string;
  onPickFolder: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex-1 space-y-4 overflow-y-auto py-1">
      {preview.name_conflict ? (
        <div className="flex items-start gap-2 rounded-md border border-warning-light bg-warning-light/30 px-3 py-2 text-sm text-warning-text">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="space-y-0.5">
            <div className="font-medium">{t(k("project.nameConflict"))}</div>
            <div className="text-xs">{t(k("project.nameConflictHint"))}</div>
          </div>
        </div>
      ) : null}

      <Section title={t(k("project.nameSection"))}>
        <Input
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder={preview.project?.name ?? ""}
          aria-invalid={
            preview.name_conflict && name.trim() === preview.project?.name
          }
        />
      </Section>

      <Section title={t(k("project.folderSection"))}>
        <div className="flex items-center gap-2 rounded-md bg-surface-soft px-2.5 py-1.5 text-sm">
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
          <span
            className="min-w-0 flex-1 truncate text-ink-body"
            title={rootPath}
          >
            {rootPath || t(k("project.folderEmpty"))}
          </span>
          <Button
            variant="outline"
            size="xs"
            onClick={onPickFolder}
            className="shrink-0"
          >
            {t(k("project.chooseFolder"))}
          </Button>
        </div>
        <div className="text-2xs text-ink-meta">
          {t(k("project.folderHint"))}
        </div>
      </Section>

      <Section title={t(k("project.membersSection"))}>
        {preview.members.length === 0 ? (
          <div className="text-xs text-ink-meta">
            {t(k("project.noMembers"))}
          </div>
        ) : (
          preview.members.map((m) => (
            <div
              key={m.agent_slug}
              className="flex items-center justify-between rounded-md bg-surface-soft px-2.5 py-1.5 text-sm"
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <Users className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
                <span className="truncate text-ink-body">{m.name}</span>
              </span>
              <span className="shrink-0 text-2xs text-ink-meta">
                {m.in_library
                  ? t(k("project.inLibrary"))
                  : t(k("project.willAdd"))}
              </span>
            </div>
          ))
        )}
      </Section>

      {preview.automations.length > 0 ? (
        <Section title={t(k("project.automationsSection"))}>
          {preview.automations.map((a, idx) => (
            <div
              key={`${a.name}-${idx}`}
              className="flex items-center justify-between rounded-md bg-surface-soft px-2.5 py-1.5 text-sm"
            >
              <span className="truncate text-ink-body">{a.name}</span>
              <span className="shrink-0 text-2xs text-ink-meta">
                {a.trigger_kind}
              </span>
            </div>
          ))}
        </Section>
      ) : null}

      {preview.skills.length > 0 ? (
        <Section title={t(k("project.skillsSection"))}>
          <div className="flex flex-wrap gap-1.5">
            {preview.skills.map((s) => (
              <span
                key={s.slug}
                className="inline-flex items-center gap-1 rounded-md bg-surface-soft px-2 py-0.5 font-mono text-2xs text-ink-body"
              >
                <Sparkles className="h-3 w-3 text-ink-muted" />
                {s.slug}
                {s.already_present ? (
                  <span className="text-ink-meta">
                    {t(k("project.alreadyHave"))}
                  </span>
                ) : null}
              </span>
            ))}
          </div>
        </Section>
      ) : null}

      {preview.connectors.length > 0 ? (
        <Section title={t(k("project.connectorsSection"))}>
          {preview.connectors.map((c) => (
            <div
              key={c.slug}
              className="flex items-center justify-between rounded-md bg-surface-soft px-2.5 py-1.5 text-sm"
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <Plug className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
                <span className="truncate text-ink-body">{c.display_name}</span>
              </span>
              <span className="shrink-0 text-2xs text-ink-meta">
                {c.already_present
                  ? t(k("project.alreadyHave"))
                  : c.requires_credentials
                    ? t(k("project.needsKey"))
                    : c.requires_setup
                      ? t(k("project.needsSetup"))
                      : ""}
              </span>
            </div>
          ))}
        </Section>
      ) : null}
    </div>
  );
}

function ImportResultView({ result }: { result: ImportProjectConfirmResult }) {
  const { t } = useTranslation();
  if (result.status === "skipped_name_conflict") {
    return (
      <div className="flex-1 space-y-4 overflow-y-auto py-1">
        <div className="flex items-start gap-2 rounded-md border border-warning-light bg-warning-light/30 px-3 py-2 text-sm text-warning-text">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="space-y-0.5">
            <div className="font-medium">{t(k("project.skipped"))}</div>
            <div className="text-xs">{t(k("project.nameConflictHint"))}</div>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="flex-1 space-y-4 overflow-y-auto py-1">
      <div className="flex items-center gap-2 rounded-md bg-success-light px-3 py-2 text-sm text-success-text">
        <Check className="h-4 w-4 shrink-0" />
        {t(k("project.importCreated"), {
          members: result.members_created + result.members_reused,
          automations: result.automations_created,
          agents: result.agents_created,
        })}
      </div>
      {(result.automation_errors ?? []).length > 0 ? (
        <div className="space-y-1.5 rounded-md border border-warning-light bg-warning-light/30 p-3">
          <div className="flex items-center gap-1.5 text-xs font-medium text-warning-text">
            <AlertTriangle className="h-3.5 w-3.5" />
            {t(k("project.automationErrors"))}
          </div>
          <div className="space-y-1">
            {(result.automation_errors ?? []).map((e) => (
              <div
                key={e.name}
                className="rounded-md bg-surface px-2 py-1 text-2xs text-ink-body"
              >
                <span className="font-medium">{e.name}</span>
                <span className="text-ink-meta"> — {e.error}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {result.connectors_to_configure.length > 0 ? (
        <div className="space-y-1.5 rounded-md border border-warning-light bg-warning-light/30 p-3">
          <div className="flex items-center gap-1.5 text-xs font-medium text-warning-text">
            <AlertTriangle className="h-3.5 w-3.5" />
            {t(k("project.connectorsToConfigure"))}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {result.connectors_to_configure.map((c) => (
              <span
                key={c.slug}
                className="inline-flex items-center gap-1 rounded-md bg-surface px-2 py-0.5 text-2xs text-ink-body"
              >
                <Plug className="h-3 w-3 text-ink-muted" />
                {c.display_name}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
