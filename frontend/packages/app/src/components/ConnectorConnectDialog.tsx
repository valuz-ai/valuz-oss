import { useState } from "react";
import { toast } from "sonner";
import { ArrowUpRight, Loader2 } from "lucide-react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@valuz/ui";
import {
  useTranslation,
  type CatalogConnector,
  type CreateConnectorRequest,
  type CatalogField,
} from "@valuz/core";

interface ConnectorConnectDialogProps {
  /** The catalog connector being connected. Non-null = open. Only used
   *  for catalog entries that declare credential/config fields; field-less
   *  entries connect directly without this dialog. */
  entry: CatalogConnector | null;
  onClose: () => void;
  /** Page-owned create + poll. Resolves once create returns. */
  onSubmit: (payload: CreateConnectorRequest) => Promise<void>;
}

function resolveLabel(
  label: CatalogField["label"],
  fallback: string,
  locale: string,
): string {
  if (typeof label === "string") return label;
  if (label && typeof label === "object") {
    return (
      label[locale] ?? label["en-US"] ?? Object.values(label)[0] ?? fallback
    );
  }
  return fallback;
}

export function ConnectorConnectDialog({
  entry,
  onClose,
  onSubmit,
}: ConnectorConnectDialogProps) {
  const { t, locale } = useTranslation();
  // Prefill header/param fields with their UI ``prefix`` hint (e.g.
  // "Bearer "). Computed lazily on mount: the page keys this dialog on the
  // entry slug, so a new entry remounts the component and re-runs this
  // initializer — no reset effect (and no cascading-render setState) needed.
  const [form, setForm] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    if (entry) {
      for (const f of [...entry.header_schema, ...entry.param_schema]) {
        initial[f.key] = f.prefix ?? "";
      }
      for (const f of entry.oauth_credentials_schema) initial[f.key] = "";
    }
    return initial;
  });
  const [submitting, setSubmitting] = useState(false);

  if (!entry) return null;

  const headerParamFields: (CatalogField & { target: "header" | "param" })[] = [
    ...entry.header_schema.map((f) => ({ ...f, target: "header" as const })),
    ...entry.param_schema.map((f) => ({ ...f, target: "param" as const })),
  ];

  const submit = async () => {
    // Required-field validation across both schema kinds.
    const missing =
      headerParamFields.some((f) => f.required && !form[f.key]?.trim()) ||
      entry.oauth_credentials_schema.some(
        (f) => f.required && !form[f.key]?.trim(),
      );
    if (missing) {
      toast.error(t("settings.connectors.allRequired"));
      return;
    }

    const headers: { key: string; secret: boolean; value: string }[] = [];
    const params: { key: string; secret: boolean; value: string }[] = [];
    for (const f of headerParamFields) {
      const value = form[f.key] ?? "";
      if (!value.trim()) continue;
      const item = { key: f.name, secret: f.secret, value };
      if (f.target === "param") params.push(item);
      else headers.push(item);
    }

    const creds: Record<string, string> = {};
    for (const f of entry.oauth_credentials_schema) {
      const v = form[f.key]?.trim();
      if (v) creds[f.key] = v;
    }

    let authType = entry.auth_type;
    // A bearer/api_key catalog entry that declares its secret as an
    // ``api_key`` credential becomes an explicit Authorization header
    // (mirrors the Settings connect flow).
    if ((authType === "bearer" || authType === "api_key") && creds["api_key"]) {
      const tok = creds["api_key"];
      headers.push({
        key: "Authorization",
        secret: true,
        value: tok.toLowerCase().startsWith("bearer ") ? tok : `Bearer ${tok}`,
      });
      delete creds["api_key"];
      authType = "bearer";
    }

    const payload: CreateConnectorRequest = {
      slug: entry.slug,
      display_name: entry.display_name,
      transport: entry.transport ?? "http",
      url: entry.url ?? "",
      auth_type: authType,
      description: entry.description,
      connector_type: "recommended",
      headers: headers.length > 0 ? headers : null,
      params: params.length > 0 ? params : null,
      credentials: Object.keys(creds).length > 0 ? creds : undefined,
    };

    setSubmitting(true);
    try {
      await onSubmit(payload);
      onClose();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("settings.connectors.addFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls =
    "w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand";

  return (
    <Dialog open onOpenChange={(o) => (o ? undefined : onClose())}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t("settings.connectors.configureCredentials", {
              name: entry.display_name,
            })}
          </DialogTitle>
          {entry.credentials_help_url ? (
            <DialogDescription asChild>
              <a
                href={entry.credentials_help_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-0.5 text-brand underline underline-offset-2"
              >
                {t("settings.connectors.goToGet")}
                <ArrowUpRight className="h-3 w-3" />
              </a>
            </DialogDescription>
          ) : (
            <DialogDescription>
              {t("settings.connectors.configureCredentials", {
                name: entry.display_name,
              })}
            </DialogDescription>
          )}
        </DialogHeader>

        <div className="space-y-3 py-2">
          {entry.oauth_credentials_schema.map((field) => (
            <div key={field.key}>
              <div className="mb-1 text-xs text-ink-meta">
                {field.label}
                {field.required ? (
                  <span className="ml-0.5 text-error-text">*</span>
                ) : null}
              </div>
              <input
                className={inputCls}
                type={field.secret ? "password" : "text"}
                placeholder={field.placeholder ?? ""}
                value={form[field.key] ?? ""}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, [field.key]: e.target.value }))
                }
              />
            </div>
          ))}

          {headerParamFields.map((field) => (
            <div key={field.key}>
              <div className="mb-1 text-xs text-ink-meta">
                {resolveLabel(field.label, field.key, locale)}
                {field.required ? (
                  <span className="ml-0.5 text-error-text">*</span>
                ) : null}
              </div>
              <input
                className={inputCls}
                type={field.secret ? "password" : "text"}
                placeholder={field.placeholder ?? ""}
                value={form[field.key] ?? ""}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, [field.key]: e.target.value }))
                }
              />
              {field.target === "param" && field.secret ? (
                <div className="mt-1 text-xs text-error-text">
                  {t("settings.connectors.secretParamWarning")}
                </div>
              ) : null}
            </div>
          ))}
        </div>

        <div className="mt-2 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button size="sm" disabled={submitting} onClick={() => void submit()}>
            {submitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            {t("settings.connectors.startAuth")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
