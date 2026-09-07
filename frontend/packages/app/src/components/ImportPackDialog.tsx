import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Check, Package, Plug, Sparkles } from "lucide-react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  LoadingState,
} from "@valuz/ui";
import {
  agentTemplatesApi,
  useTranslation,
  type ImportPackPreview,
  type ImportPackResult,
} from "@valuz/core";

type Tx = ReturnType<typeof useTranslation>["t"];
const k = (key: string) => key as Parameters<Tx>[0];

/**
 * Upload → preview → confirm flow for a ``.valuzpack``. The parent owns the
 * file pick and passes the chosen ``file``; this dialog stages a preview
 * (what's inside, what already exists, what needs setup), commits on confirm,
 * and shows the result — including connectors the user still needs to wire up.
 */
export function ImportPackDialog({
  file,
  open,
  onOpenChange,
  onImported,
}: {
  file: File | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a successful import so the parent can refresh its agent list. */
  onImported: () => void;
}) {
  const { t } = useTranslation();
  const [preview, setPreview] = useState<ImportPackPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ImportPackResult | null>(null);

  useEffect(() => {
    if (!open || !file) return;
    let cancelled = false;
    // Defer the state resets + fetch off the synchronous effect body (avoids the
    // cascading-render lint and matches the codebase's effect pattern).
    void Promise.resolve().then(async () => {
      if (cancelled) return;
      setPreview(null);
      setResult(null);
      setLoading(true);
      try {
        const p = await agentTemplatesApi.importPack(file);
        if (!cancelled) setPreview(p);
      } catch (err) {
        if (cancelled) return;
        toast.error(err instanceof Error ? err.message : t(k("agent.pack.importFailed")));
        onOpenChange(false);
      } finally {
        if (!cancelled) setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [open, file, onOpenChange, t]);

  const handleConfirm = async () => {
    if (!preview) return;
    setImporting(true);
    try {
      const res = await agentTemplatesApi.confirmImportPack(preview.preview_id);
      setResult(res);
      toast.success(
        t(k("agent.pack.importDone"), {
          created: res.created,
          skipped: res.skipped,
        }),
      );
      onImported();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t(k("agent.pack.importFailed")));
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
            {preview?.collection?.name || t(k("agent.pack.importTitle"))}
          </DialogTitle>
          {preview?.collection?.description ? (
            <DialogDescription>{preview.collection.description}</DialogDescription>
          ) : null}
        </DialogHeader>

        {loading ? (
          <LoadingState variant="section" />
        ) : result ? (
          <ImportResultView result={result} />
        ) : preview ? (
          <ImportPreviewView preview={preview} />
        ) : null}

        {!result && preview ? (
          <div className="mt-2 flex justify-end">
            <Button onClick={() => void handleConfirm()} disabled={importing} loading={importing}>
              {importing ? t(k("agent.pack.importing")) : t(k("agent.pack.confirm"))}
            </Button>
          </div>
        ) : null}
        {result ? (
          <div className="mt-2 flex justify-end">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t(k("agent.pack.done"))}
            </Button>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="text-2xs font-semibold uppercase tracking-wider text-ink-section">
        {title}
      </div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function ImportPreviewView({ preview }: { preview: ImportPackPreview }) {
  const { t } = useTranslation();
  return (
    <div className="flex-1 space-y-4 overflow-y-auto py-1">
      <Section title={t(k("agent.pack.agentsSection"))}>
        {preview.agents.map((a) => (
          <div
            key={a.slug}
            className="flex items-center justify-between rounded-md bg-surface-soft px-2.5 py-1.5 text-sm"
          >
            <span className="truncate text-ink-body">{a.name}</span>
            <span className="shrink-0 text-2xs text-ink-meta">
              {a.in_library ? t(k("agent.pack.inLibrary")) : t(k("agent.pack.willAdd"))}
            </span>
          </div>
        ))}
      </Section>

      {preview.skills.length > 0 ? (
        <Section title={t(k("agent.pack.skillsSection"))}>
          <div className="flex flex-wrap gap-1.5">
            {preview.skills.map((s) => (
              <span
                key={s.slug}
                className="inline-flex items-center gap-1 rounded-md bg-surface-soft px-2 py-0.5 font-mono text-2xs text-ink-body"
              >
                <Sparkles className="h-3 w-3 text-ink-muted" />
                {s.slug}
              </span>
            ))}
          </div>
        </Section>
      ) : null}

      {preview.connectors.length > 0 ? (
        <Section title={t(k("agent.pack.connectorsSection"))}>
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
                  ? t(k("agent.pack.alreadyHave"))
                  : c.requires_credentials
                    ? t(k("agent.pack.needsKey"))
                    : c.requires_setup
                      ? t(k("agent.pack.needsSetup"))
                      : ""}
              </span>
            </div>
          ))}
        </Section>
      ) : null}
    </div>
  );
}

function ImportResultView({ result }: { result: ImportPackResult }) {
  const { t } = useTranslation();
  return (
    <div className="flex-1 space-y-4 overflow-y-auto py-1">
      <div className="flex items-center gap-2 rounded-md bg-success-light px-3 py-2 text-sm text-success-text">
        <Check className="h-4 w-4 shrink-0" />
        {t(k("agent.pack.importDone"), { created: result.created, skipped: result.skipped })}
      </div>
      {result.connectors_to_configure.length > 0 ? (
        <div className="space-y-1.5 rounded-md border border-warning-light bg-warning-light/30 p-3">
          <div className="flex items-center gap-1.5 text-xs font-medium text-warning-text">
            <AlertTriangle className="h-3.5 w-3.5" />
            {t(k("agent.pack.toConfigure"))}
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
