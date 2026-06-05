import { useRef, useState } from "react";
import { toast } from "sonner";
import { Loader2, X } from "lucide-react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@valuz/ui";
import {
  connectorsApi,
  useTranslation,
  type CreateConnectorRequest,
} from "@valuz/core";

export type ConnectorAddMode = "http" | "stdio";

interface ConnectorAddDialogProps {
  open: boolean;
  mode: ConnectorAddMode;
  onOpenChange: (open: boolean) => void;
  /** Page-owned create + poll. Receives the built payload; resolves once
   *  the create call returns (the page handles OAuth window + polling). */
  onSubmit: (payload: CreateConnectorRequest) => Promise<void>;
}

interface KvRow {
  key: string;
  value: string;
  secret: boolean;
}

const emptyHttp = {
  display_name: "",
  url: "",
  transport: "http" as "http" | "sse",
  auth_type: "none" as "none" | "bearer" | "oauth",
  api_key: "",
  oauth_client_id: "",
  oauth_client_secret: "",
  oauth_authorization_endpoint: "",
  oauth_token_endpoint: "",
  oauth_registration_endpoint: "",
  headers: [] as KvRow[],
  params: [] as KvRow[],
};

const emptyStdio = {
  display_name: "",
  cmdline: "",
  env: [] as { key: string; value: string }[],
};

export function ConnectorAddDialog({
  open,
  mode,
  onOpenChange,
  onSubmit,
}: ConnectorAddDialogProps) {
  const { t } = useTranslation();
  const [http, setHttp] = useState(emptyHttp);
  const [stdio, setStdio] = useState(emptyStdio);
  const [submitting, setSubmitting] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const lastDiscoveredUrl = useRef<string | null>(null);

  const reset = () => {
    setHttp(emptyHttp);
    setStdio(emptyStdio);
    lastDiscoveredUrl.current = null;
  };

  const close = () => {
    reset();
    onOpenChange(false);
  };

  // Proactively probe OAuth metadata once the URL is filled and the auth
  // method is still unknown — mirrors the Settings add flow so OAuth
  // servers flip to auth_type="oauth" without the user knowing.
  const maybeDiscover = async () => {
    const url = http.url.trim();
    if (
      !url ||
      http.auth_type !== "none" ||
      discovering ||
      url === lastDiscoveredUrl.current
    ) {
      return;
    }
    lastDiscoveredUrl.current = url;
    setDiscovering(true);
    try {
      const res = await connectorsApi.discover({
        url,
        transport: http.transport,
      });
      if (res.discovered && res.auth_type === "oauth") {
        setHttp((f) =>
          f.url.trim() === url
            ? {
                ...f,
                auth_type: "oauth",
                oauth_authorization_endpoint:
                  res.oauth_authorization_endpoint ??
                  f.oauth_authorization_endpoint,
                oauth_token_endpoint:
                  res.oauth_token_endpoint ?? f.oauth_token_endpoint,
                oauth_registration_endpoint:
                  res.oauth_registration_endpoint ??
                  f.oauth_registration_endpoint,
              }
            : f,
        );
        toast.success(
          t(
            res.oauth_registration_endpoint
              ? "settings.connectors.detectOAuthFound"
              : "settings.connectors.detectOAuthFoundManual",
          ),
        );
      }
    } catch {
      // Best-effort — silent on miss.
    } finally {
      setDiscovering(false);
    }
  };

  const submitHttp = async () => {
    if (!http.display_name.trim() || !http.url.trim()) {
      toast.error(t("settings.connectors.nameAndUrlRequired"));
      return;
    }
    if (http.auth_type === "bearer" && !http.api_key.trim()) {
      toast.error(t("settings.connectors.apiKeyRequired"));
      return;
    }
    if (
      http.auth_type === "oauth" &&
      !http.oauth_registration_endpoint.trim() &&
      !http.oauth_client_id.trim()
    ) {
      toast.error(t("settings.connectors.clientIdRequiredNoDcr"));
      return;
    }
    const headers = http.headers
      .filter((h) => h.key.trim())
      .map((h) => ({ key: h.key.trim(), secret: h.secret, value: h.value }));
    const params = http.params
      .filter((p) => p.key.trim())
      .map((p) => ({ key: p.key.trim(), secret: p.secret, value: p.value }));
    if (http.auth_type === "bearer" && http.api_key.trim()) {
      const tok = http.api_key.trim();
      headers.push({
        key: "Authorization",
        secret: true,
        value: tok.toLowerCase().startsWith("bearer ") ? tok : `Bearer ${tok}`,
      });
    }
    const creds: Record<string, string> = {};
    if (http.auth_type === "oauth") {
      if (http.oauth_client_id.trim())
        creds["client_id"] = http.oauth_client_id.trim();
      if (http.oauth_client_secret.trim())
        creds["client_secret"] = http.oauth_client_secret.trim();
    }
    const payload: CreateConnectorRequest = {
      display_name: http.display_name.trim(),
      transport: http.transport,
      url: http.url.trim(),
      auth_type: http.auth_type,
      headers: headers.length > 0 ? headers : null,
      params: params.length > 0 ? params : null,
      credentials: Object.keys(creds).length > 0 ? creds : undefined,
      oauth_authorization_endpoint:
        http.auth_type === "oauth" && http.oauth_authorization_endpoint.trim()
          ? http.oauth_authorization_endpoint.trim()
          : undefined,
      oauth_token_endpoint:
        http.auth_type === "oauth" && http.oauth_token_endpoint.trim()
          ? http.oauth_token_endpoint.trim()
          : undefined,
      oauth_registration_endpoint:
        http.auth_type === "oauth" && http.oauth_registration_endpoint.trim()
          ? http.oauth_registration_endpoint.trim()
          : undefined,
    };
    setSubmitting(true);
    try {
      await onSubmit(payload);
      close();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("settings.connectors.addFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const submitStdio = async () => {
    if (!stdio.display_name.trim() || !stdio.cmdline.trim()) {
      toast.error(t("settings.connectors.nameAndCmdRequired"));
      return;
    }
    const envDict: Record<string, string> = {};
    for (const { key, value } of stdio.env) {
      if (key.trim()) envDict[key.trim()] = value;
    }
    const payload: CreateConnectorRequest = {
      display_name: stdio.display_name.trim(),
      transport: "stdio",
      command: stdio.cmdline.trim(),
      args: [],
      env: Object.keys(envDict).length > 0 ? envDict : null,
    };
    setSubmitting(true);
    try {
      await onSubmit(payload);
      close();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("settings.connectors.addFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls =
    "w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand";

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => (o ? onOpenChange(true) : close())}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>
            {mode === "http"
              ? t("settings.connectors.addHttpConnector")
              : t("settings.connectors.addStdioConnector")}
          </DialogTitle>
          <DialogDescription>{t("settings.connectors.desc")}</DialogDescription>
        </DialogHeader>

        {mode === "http" ? (
          <div className="space-y-3 py-2">
            <div>
              <div className="mb-1 text-xs text-ink-meta">
                {t("common.name")}
              </div>
              <input
                className={inputCls}
                placeholder="My MCP Server"
                value={http.display_name}
                onChange={(e) =>
                  setHttp((f) => ({ ...f, display_name: e.target.value }))
                }
              />
            </div>
            <div>
              <div className="mb-1 flex items-center gap-2 text-xs text-ink-meta">
                <span>{t("settings.connectors.url")}</span>
                {discovering ? (
                  <span className="flex items-center gap-1">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    {t("settings.connectors.detecting")}
                  </span>
                ) : null}
              </div>
              <input
                className={`${inputCls} font-mono`}
                placeholder="https://example.com/mcp"
                value={http.url}
                onChange={(e) =>
                  setHttp((f) => ({ ...f, url: e.target.value }))
                }
                onBlur={() => void maybeDiscover()}
              />
            </div>
            <div className="flex gap-3">
              <div className="flex-1">
                <div className="mb-1 text-xs text-ink-meta">
                  {t("settings.connectors.transport")}
                </div>
                <Select
                  value={http.transport}
                  onValueChange={(v) =>
                    setHttp((f) => ({ ...f, transport: v as "http" | "sse" }))
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="http">HTTP</SelectItem>
                    <SelectItem value="sse">SSE</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex-1">
                <div className="mb-1 text-xs text-ink-meta">
                  {t("settings.connectors.authType")}
                </div>
                <Select
                  value={http.auth_type}
                  onValueChange={(v) =>
                    setHttp((f) => ({
                      ...f,
                      auth_type: v as "none" | "bearer" | "oauth",
                    }))
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">
                      {t("settings.connectors.authTypeNone")}
                    </SelectItem>
                    <SelectItem value="bearer">
                      {t("settings.connectors.authTypeBearer")}
                    </SelectItem>
                    <SelectItem value="oauth">
                      {t("settings.connectors.authTypeOAuth")}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {http.auth_type === "bearer" ? (
              <div>
                <div className="mb-1 text-xs text-ink-meta">
                  {t("settings.connectors.authTypeBearer")}
                </div>
                <input
                  className={`${inputCls} font-mono`}
                  type="password"
                  placeholder="sk-…"
                  value={http.api_key}
                  onChange={(e) =>
                    setHttp((f) => ({ ...f, api_key: e.target.value }))
                  }
                />
              </div>
            ) : null}

            {http.auth_type === "oauth" ? (
              <div className="space-y-3 rounded-lg border border-surface-border p-3">
                <div className="text-xs text-ink-meta">
                  {t("settings.connectors.oauthCustomHint")}
                </div>
                <div className="flex gap-3">
                  <div className="flex-1">
                    <div className="mb-1 text-xs text-ink-meta">
                      {t("settings.connectors.oauthClientId")}
                    </div>
                    <input
                      className={`${inputCls} font-mono`}
                      value={http.oauth_client_id}
                      onChange={(e) =>
                        setHttp((f) => ({
                          ...f,
                          oauth_client_id: e.target.value,
                        }))
                      }
                    />
                  </div>
                  <div className="flex-1">
                    <div className="mb-1 text-xs text-ink-meta">
                      {t("settings.connectors.oauthClientSecret")}
                    </div>
                    <input
                      className={`${inputCls} font-mono`}
                      type="password"
                      value={http.oauth_client_secret}
                      onChange={(e) =>
                        setHttp((f) => ({
                          ...f,
                          oauth_client_secret: e.target.value,
                        }))
                      }
                    />
                  </div>
                </div>
              </div>
            ) : null}

            <KvEditor
              label={t("settings.connectors.customHeaders")}
              addLabel={t("settings.connectors.addHeader")}
              rows={http.headers}
              onChange={(rows) => setHttp((f) => ({ ...f, headers: rows }))}
            />
            <KvEditor
              label={t("settings.connectors.paramsLabel")}
              addLabel={t("settings.connectors.addParam")}
              rows={http.params}
              onChange={(rows) => setHttp((f) => ({ ...f, params: rows }))}
            />
          </div>
        ) : (
          <div className="space-y-3 py-2">
            <div>
              <div className="mb-1 text-xs text-ink-meta">
                {t("common.name")}
              </div>
              <input
                className={inputCls}
                placeholder="My local server"
                value={stdio.display_name}
                onChange={(e) =>
                  setStdio((f) => ({ ...f, display_name: e.target.value }))
                }
              />
            </div>
            <div>
              <div className="mb-1 text-xs text-ink-meta">
                {t("settings.connectors.cmdline")}
              </div>
              <input
                className={`${inputCls} font-mono`}
                placeholder="npx -y @modelcontextprotocol/server-filesystem"
                value={stdio.cmdline}
                onChange={(e) =>
                  setStdio((f) => ({ ...f, cmdline: e.target.value }))
                }
              />
            </div>
            <EnvEditor
              label={t("settings.connectors.envVars")}
              addLabel={t("settings.connectors.addEnvVar")}
              rows={stdio.env}
              onChange={(rows) => setStdio((f) => ({ ...f, env: rows }))}
            />
          </div>
        )}

        <div className="mt-2 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={close}>
            {t("common.cancel")}
          </Button>
          <Button
            size="sm"
            disabled={submitting}
            onClick={() =>
              void (mode === "http" ? submitHttp() : submitStdio())
            }
          >
            {submitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            {t("common.add")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function KvEditor({
  label,
  addLabel,
  rows,
  onChange,
}: {
  label: string;
  addLabel: string;
  rows: KvRow[];
  onChange: (rows: KvRow[]) => void;
}) {
  const inputCls =
    "rounded-lg border border-surface-border bg-background px-2 py-1 text-xs text-ink-heading outline-none focus:ring-1 focus:ring-brand";
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs text-ink-meta">
        <span>{label}</span>
        <button
          type="button"
          className="text-brand"
          onClick={() =>
            onChange([...rows, { key: "", value: "", secret: false }])
          }
        >
          {addLabel}
        </button>
      </div>
      <div className="flex flex-col gap-1.5">
        {rows.map((row, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <input
              className={`${inputCls} w-1/3 font-mono`}
              placeholder="Key"
              value={row.key}
              onChange={(e) => {
                const next = [...rows];
                next[i] = { ...row, key: e.target.value };
                onChange(next);
              }}
            />
            <input
              className={`${inputCls} flex-1 font-mono`}
              placeholder="Value"
              type={row.secret ? "password" : "text"}
              value={row.value}
              onChange={(e) => {
                const next = [...rows];
                next[i] = { ...row, value: e.target.value };
                onChange(next);
              }}
            />
            <button
              type="button"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-ink-meta hover:bg-surface-soft"
              onClick={() => onChange(rows.filter((_, idx) => idx !== i))}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function EnvEditor({
  label,
  addLabel,
  rows,
  onChange,
}: {
  label: string;
  addLabel: string;
  rows: { key: string; value: string }[];
  onChange: (rows: { key: string; value: string }[]) => void;
}) {
  const inputCls =
    "rounded-lg border border-surface-border bg-background px-2 py-1 text-xs text-ink-heading outline-none focus:ring-1 focus:ring-brand";
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs text-ink-meta">
        <span>{label}</span>
        <button
          type="button"
          className="text-brand"
          onClick={() => onChange([...rows, { key: "", value: "" }])}
        >
          {addLabel}
        </button>
      </div>
      <div className="flex flex-col gap-1.5">
        {rows.map((row, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <input
              className={`${inputCls} w-1/3 font-mono`}
              placeholder="KEY"
              value={row.key}
              onChange={(e) => {
                const next = [...rows];
                next[i] = { ...row, key: e.target.value };
                onChange(next);
              }}
            />
            <input
              className={`${inputCls} flex-1 font-mono`}
              placeholder="value"
              value={row.value}
              onChange={(e) => {
                const next = [...rows];
                next[i] = { ...row, value: e.target.value };
                onChange(next);
              }}
            />
            <button
              type="button"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-ink-meta hover:bg-surface-soft"
              onClick={() => onChange(rows.filter((_, idx) => idx !== i))}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
