import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ExternalLink, Loader2, Plug, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@valuz/ui";
import {
  connectorsApi,
  marketplaceApi,
  useTranslation,
  type CreateConnectorRequest,
  type MarketplaceItem,
  type MarketplaceItemDetail,
} from "@valuz/core";
import { formatCount, MarketplaceSourcePill } from "./marketplace-ui";

interface MarketplaceConnectorDialogProps {
  item: MarketplaceItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConnected: (item: MarketplaceItem) => void;
}

export function MarketplaceConnectorDialog({
  item,
  open,
  onOpenChange,
  onConnected,
}: MarketplaceConnectorDialogProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<MarketplaceItemDetail | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open || !item) return;
    let cancelled = false;
    setLoading(true);
    setDetail(null);
    marketplaceApi
      .get(item.id)
      .then((next) => {
        if (cancelled) return;
        setDetail(next);
        const initial: Record<string, string> = {};
        for (const field of next.connector_config?.fields ?? []) {
          initial[field.key] = field.prefix ?? "";
        }
        setForm(initial);
      })
      .catch((error) => {
        if (!cancelled) {
          toast.error(
            error instanceof Error
              ? error.message
              : t("marketplace.connectorLoadFailed"),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [item, open, t]);

  const config = detail?.connector_config ?? null;
  const missingRequired = useMemo(
    () =>
      config?.fields.some(
        (field) => field.required && !(form[field.key] ?? "").trim(),
      ) ?? false,
    [config, form],
  );

  const connect = async () => {
    if (!item || !detail || !config || !config.supported) return;
    if (missingRequired) {
      toast.error(t("marketplace.connectorRequiredFields"));
      return;
    }
    const env = { ...config.env };
    const headers = Object.entries(config.headers).map(([key, value]) => ({
      key,
      value,
      secret: false,
    }));
    const params = Object.entries(config.params).map(([key, value]) => ({
      key,
      value,
      secret: false,
    }));
    for (const field of config.fields) {
      const value = (form[field.key] ?? "").trim();
      if (!value) continue;
      if (field.target === "env") env[field.name] = value;
      if (field.target === "header") {
        headers.push({ key: field.name, value, secret: field.secret });
      }
      if (field.target === "param") {
        params.push({ key: field.name, value, secret: field.secret });
      }
    }
    const payload: CreateConnectorRequest = {
      slug: config.slug,
      display_name: detail.title,
      description: detail.description,
      connector_type: "modelscope",
      transport: config.transport,
      url: config.url ?? undefined,
      auth_type: config.auth_type,
      oauth_authorization_endpoint:
        config.oauth_authorization_endpoint ?? undefined,
      oauth_token_endpoint: config.oauth_token_endpoint ?? undefined,
      oauth_registration_endpoint:
        config.oauth_registration_endpoint ?? undefined,
      oauth_scopes: config.oauth_scopes,
      command: config.command ?? undefined,
      args: config.args,
      env: Object.keys(env).length > 0 ? env : undefined,
      headers: headers.length > 0 ? headers : undefined,
      params: params.length > 0 ? params : undefined,
    };
    setSubmitting(true);
    try {
      const result = await connectorsApi.create(payload);
      if (result.needs_auth && result.authorization_url) {
        window.open(result.authorization_url, "_blank");
      }
      onConnected(item);
      toast.success(t("marketplace.connectorAdded"));
      onOpenChange(false);
      navigate("/connectors");
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("marketplace.connectorAddFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <div className="flex items-start gap-3 pr-7">
            <div className="flex h-11 w-11 flex-none items-center justify-center overflow-hidden rounded-lg bg-sky-100 text-sky-700">
              {detail?.icon || item?.icon ? (
                <img
                  src={detail?.icon ?? item?.icon ?? undefined}
                  alt=""
                  className="h-full w-full object-cover"
                />
              ) : (
                <Plug className="h-5 w-5" />
              )}
            </div>
            <div className="min-w-0 flex-1">
              <DialogTitle className="text-left leading-snug">
                {detail?.title ?? item?.title ?? t("marketplace.modalTypeConnector")}
              </DialogTitle>
              <DialogDescription className="mt-1 line-clamp-3 text-left leading-relaxed">
                {detail?.description ?? item?.description}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {loading ? (
          <div className="flex min-h-48 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-brand" />
          </div>
        ) : detail && config ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-body">
              <MarketplaceSourcePill
                source={detail.source}
                itemType={detail.type}
              />
              {detail.stats.views != null ? (
                <span>
                  {t("marketplace.connectorViews", {
                    count: formatCount(detail.stats.views),
                  })}
                </span>
              ) : null}
              {detail.stats.stars != null && detail.stats.stars > 0 ? (
                <span>GitHub {formatCount(detail.stats.stars)} ★</span>
              ) : null}
              {detail.badges.includes("verified") ? (
                <span className="inline-flex items-center gap-1 text-emerald-700">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  {t("marketplace.connectorVerified")}
                </span>
              ) : null}
            </div>

            <div className="rounded-lg border border-surface-border bg-surface-soft px-3 py-2.5">
              <div className="text-xs font-medium text-ink-heading">
                {t("marketplace.connectorRunMode")}
              </div>
              <div className="mt-1 font-mono text-xs text-ink-body">
                {config.transport === "stdio"
                  ? `${config.command ?? ""} ${config.args.join(" ")}`
                  : `${config.transport.toUpperCase()} · ${config.url ?? ""}`}
              </div>
            </div>

            {!config.supported ? (
              <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-sm leading-relaxed text-amber-900">
                {config.unsupported_reason}
              </div>
            ) : null}

            {config.supported && config.fields.length > 0 ? (
              <div>
                <div className="mb-2 text-sm font-semibold text-ink-heading">
                  {t("marketplace.connectorConfiguration")}
                </div>
                <div className="space-y-3">
                  {config.fields.map((field) => (
                    <label key={field.key} className="block">
                      <span className="mb-1 block text-xs text-ink-body">
                        {field.label}
                        {field.required ? <span className="ml-0.5 text-error-text">*</span> : null}
                      </span>
                      <input
                        type={field.secret ? "password" : "text"}
                        value={form[field.key] ?? ""}
                        placeholder={field.placeholder ?? ""}
                        onChange={(event) =>
                          setForm((prev) => ({
                            ...prev,
                            [field.key]: event.target.value,
                          }))
                        }
                        className="h-9 w-full rounded-lg border border-surface-border bg-background px-3 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                      />
                    </label>
                  ))}
                </div>
              </div>
            ) : null}

            {detail.origin_url ? (
              <a
                href={detail.origin_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-brand hover:underline"
              >
                {detail.source === "modelscope"
                  ? t("marketplace.connectorViewOnModelScope")
                  : t("marketplace.connectorViewDetails")}
                <ExternalLink className="h-3 w-3" />
              </a>
            ) : null}
          </div>
        ) : null}

        <div className="mt-2 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button
            size="sm"
            disabled={
              loading ||
              submitting ||
              missingRequired ||
              !config?.supported ||
              detail?.installed
            }
            onClick={() => void connect()}
          >
            {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plug className="h-3.5 w-3.5" />}
            {detail?.installed
              ? t("marketplace.connected")
              : t("marketplace.connectConnector")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
