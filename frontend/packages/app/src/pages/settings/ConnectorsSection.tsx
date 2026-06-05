import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { toast } from "sonner";
import {
  ArrowUpRight,
  Check,
  Link2,
  Plus,
  FilePenLine,
  Trash2,
  Loader2,
  RefreshCw,
  Lock,
  LockOpen,
} from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  Badge,
  Button,
  Card,
  CardContent,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  SettingsSection,
  Switch,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  cn,
} from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { useSettingsStore } from "@valuz/core";
import {
  connectorsApi,
  type ConnectorItem as ConnectorItemType,
  type CatalogEntry,
  type CatalogConnector,
  type CatalogField,
  type UpdateConnectorRequest,
} from "@valuz/core";

// Connector directory display types -- used by the recommended-
// connectors section to render every catalog entry uniformly as
// ``H4 label + card grid``. Loose ungrouped entries get rolled into
// a synthetic "通用" group at the head of the list.
type GroupConnector = CatalogConnector & { icon_url?: string | null };
type DisplayGroup = {
  slug: string;
  display_name: string;
  description: string | null;
  icon_url: string | null;
  connectors: GroupConnector[];
};

const ConnectorIcon = ({
  iconUrl,
  name,
  size = "md",
}: {
  iconUrl: string | null;
  name: string;
  size?: "sm" | "md";
}) => {
  const [failed, setFailed] = useState(false);
  if (iconUrl && !failed) {
    return (
      <img
        src={iconUrl}
        alt={name}
        className={size === "sm" ? "h-4 w-4 rounded" : "h-5 w-5 rounded"}
        onError={() => setFailed(true)}
      />
    );
  }
  return <Link2 className="h-4 w-4" />;
};

export const ConnectorsSection = () => {
  const { t } = useTranslation();
  const { locale } = useSettingsStore();

  // -- Connector state --
  const [connectorsList, setConnectorsList] = useState<ConnectorItemType[]>([]);
  const [directoryItems, setDirectoryItems] = useState<CatalogEntry[]>([]);
  // Recommended-connectors layout: collapse all ungrouped catalog
  // entries into a single virtual "通用" group at the head of the
  // list, then render every section uniformly as
  // ``H4 label + same-grid cards``. Avoids the mixed visual where
  // grouped Valuz cards and loose GitHub/Linear/Notion cards lived
  // in the same flat grid with inconsistent rhythm.
  const groupedDirectoryItems: DisplayGroup[] = useMemo(() => {
    const loose: GroupConnector[] = [];
    const groups: DisplayGroup[] = [];
    for (const item of directoryItems) {
      if (item.kind === "group") {
        groups.push({
          slug: item.slug,
          display_name: item.display_name,
          description: item.description,
          icon_url: item.icon_url,
          connectors: item.connectors,
        });
      } else {
        loose.push({
          slug: item.slug,
          display_name: item.display_name,
          description: item.description,
          url: item.url,
          auth_type: item.auth_type,
          transport: item.transport,
          installed: item.installed,
          oauth_credentials_schema: item.oauth_credentials_schema,
          header_schema: item.header_schema,
          param_schema: item.param_schema,
          credentials_help_url: item.credentials_help_url,
          command: item.command,
          args: item.args,
          working_dir: item.working_dir,
          env: item.env,
          icon_url: item.icon_url,
        });
      }
    }
    const virtualGeneral: DisplayGroup | null =
      loose.length > 0
        ? {
            slug: "_general",
            display_name: t(
              "settings.connectors.generalGroup" as Parameters<typeof t>[0],
            ),
            description: null,
            icon_url: null,
            connectors: loose,
          }
        : null;
    return virtualGeneral ? [virtualGeneral, ...groups] : groups;
  }, [directoryItems, t]);
  const [connectorsLoading, setConnectorsLoading] = useState(false);
  const [connectorTesting, setConnectorTesting] = useState<string | null>(null);
  const [activeCustomForm, setActiveCustomForm] = useState<
    "http" | "stdio" | null
  >(null);
  const showAddHttpDialog = activeCustomForm === "http";
  const showAddStdioDialog = activeCustomForm === "stdio";
  const [addHttpForm, setAddHttpForm] = useState({
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
    headers: [] as { key: string; value: string; secret: boolean }[],
    params: [] as { key: string; value: string; secret: boolean }[],
  });
  const [httpDiscovering, setHttpDiscovering] = useState(false);
  const lastDiscoveredUrlRef = useRef<string>("");
  const [addStdioForm, setAddStdioForm] = useState({
    display_name: "",
    cmdline: "",
    env: [] as { key: string; value: string }[],
  });
  const [editingConnectorId, setEditingConnectorId] = useState<string | null>(
    null,
  );
  const [editForm, setEditForm] = useState<{
    display_name: string;
    url: string;
    transport: "http" | "sse" | "stdio";
    auth_type: string;
    api_key: string;
    headers: { key: string; value: string; secret: boolean }[];
    params: { key: string; value: string; secret: boolean }[];
    cmdline: string;
    env: { key: string; value: string }[];
    hasExistingKey: boolean;
  }>({
    display_name: "",
    url: "",
    transport: "http",
    auth_type: "none",
    api_key: "",
    headers: [],
    params: [],
    cmdline: "",
    env: [],
    hasExistingKey: false,
  });
  const [oauthPending, setOauthPending] = useState<string | null>(null);
  const oauthPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  type OAuthCredentialsEntry = {
    slug: string;
    display_name: string;
    description: string | null;
    url: string;
    auth_type: string;
    transport: string;
    oauth_credentials_schema: CatalogConnector["oauth_credentials_schema"];
    credentials_help_url: string | null;
    command?: string | null;
    args?: string[];
    working_dir?: string | null;
    env?: Record<string, string> | null;
  };
  const [oauthCredentialsEntry, setOauthCredentialsEntry] =
    useState<OAuthCredentialsEntry | null>(null);
  const [oauthCredentialsForm, setOauthCredentialsForm] = useState<
    Record<string, string>
  >({});

  type CatalogFieldsEntry = {
    slug: string;
    display_name: string;
    description: string | null;
    url: string;
    auth_type: string;
    transport: string;
    // Combined header_schema + param_schema, each tagged with the target
    // it was declared under (the catalog model has no per-entry target).
    fields: (CatalogField & { target: "header" | "param" })[];
    credentials_help_url: string | null;
  };
  const [catalogFieldsEntry, setCatalogFieldsEntry] =
    useState<CatalogFieldsEntry | null>(null);
  // keyed by field.key (the unique identifier), value is the input string
  const [catalogFieldsForm, setCatalogFieldsForm] = useState<
    Record<string, string>
  >({});

  const loadConnectors = useCallback(async () => {
    setConnectorsLoading(true);
    try {
      const [listRes, dirRes] = await Promise.all([
        connectorsApi.list(),
        connectorsApi.listDirectory(),
      ]);
      setConnectorsList(listRes.connectors);
      setDirectoryItems(dirRes.items);
    } catch {
      // silently fail
    } finally {
      setConnectorsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConnectors();
  }, [loadConnectors]);

  // Cancel polls on unmount
  useEffect(() => {
    return () => {
      if (oauthPollRef.current) clearTimeout(oauthPollRef.current);
      if (connectPollRef.current) clearTimeout(connectPollRef.current);
    };
  }, []);

  // Fast path: OAuth callback page posts connector_oauth_success/error
  useEffect(() => {
    const handleMessage = (e: MessageEvent) => {
      if (e.data?.type === "connector_oauth_success") {
        if (oauthPollRef.current) {
          clearTimeout(oauthPollRef.current);
          oauthPollRef.current = null;
        }
        if (connectPollRef.current) {
          clearTimeout(connectPollRef.current);
          connectPollRef.current = null;
        }
        setOauthPending(null);
        void loadConnectors();
      } else if (e.data?.type === "connector_oauth_error") {
        if (oauthPollRef.current) {
          clearTimeout(oauthPollRef.current);
          oauthPollRef.current = null;
        }
        if (connectPollRef.current) {
          clearTimeout(connectPollRef.current);
          connectPollRef.current = null;
        }
        setOauthPending(null);
        void loadConnectors();
        toast.error(
          typeof e.data.error === "string"
            ? e.data.error
            : t("settings.connectors.authFailed"),
        );
      }
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [loadConnectors]);

  const handleToggleConnector = async (connector: ConnectorItemType) => {
    try {
      if (connector.enabled) {
        await connectorsApi.disable(connector.id);
      } else {
        await connectorsApi.enable(connector.id);
      }
      await loadConnectors();
    } catch {
      toast.error(t("settings.connectors.operationFailed"));
    }
  };

  const handleTestConnector = async (connectorId: string) => {
    setConnectorTesting(connectorId);
    try {
      const result = await connectorsApi.test(connectorId);
      if (result.ok) {
        toast.success(
          result.tool_count != null
            ? t("settings.connectors.connectSuccessTools", {
                count: result.tool_count,
              })
            : t("settings.connectors.connectSuccess"),
        );
      } else {
        toast.error(result.error || t("settings.connectors.connectFailed"));
      }
      await loadConnectors();
    } catch {
      toast.error(t("settings.connectors.testFailed"));
    } finally {
      setConnectorTesting(null);
    }
  };

  const handleDeleteConnector = async (connectorId: string) => {
    try {
      await connectorsApi.delete(connectorId);
      toast.success(t("settings.connectors.connectorDeleted"));
      await loadConnectors();
    } catch {
      toast.error(t("settings.connectors.deleteFailed"));
    }
  };

  const handleOAuthButtonClick = (entry: OAuthCredentialsEntry) => {
    if (entry.oauth_credentials_schema.length > 0) {
      const initial: Record<string, string> = {};
      for (const f of entry.oauth_credentials_schema) initial[f.key] = "";
      setOauthCredentialsForm(initial);
      setOauthCredentialsEntry(entry);
    } else {
      // No credentials needed -- go straight through the unified create endpoint.
      void (async () => {
        try {
          const res = await connectorsApi.create({
            slug: entry.slug,
            display_name: entry.display_name,
            transport: entry.transport ?? "http",
            url: entry.url ?? "",
            auth_type: entry.auth_type,
            description: entry.description,
            connector_type: "recommended",
            command: entry.command ?? undefined,
            args: entry.args ?? undefined,
            working_dir: entry.working_dir ?? undefined,
            env: entry.env ?? undefined,
          });
          await loadConnectors();
          if (res.needs_auth && res.authorization_url) {
            window.open(res.authorization_url, "_blank");
            toast.info(t("settings.connectors.completeInBrowser"));
            setOauthPending(res.slug);
            pollConnectorStatus(res.id, 300_000);
          } else {
            toast.success(t("settings.connectors.httpConnectorAdded"));
            pollConnectorStatus(res.id);
          }
        } catch (err) {
          toast.error(
            err instanceof Error
              ? err.message
              : t("settings.connectors.addFailed"),
          );
        }
      })();
    }
  };

  const handleCatalogFieldsClick = (
    connector: GroupConnector & { credentials_help_url: string | null },
  ) => {
    const fields = [
      ...connector.header_schema.map((f) => ({
        ...f,
        target: "header" as const,
      })),
      ...connector.param_schema.map((f) => ({
        ...f,
        target: "param" as const,
      })),
    ];
    const initial: Record<string, string> = {};
    for (const f of fields) {
      initial[f.key] = f.prefix ?? "";
    }
    setCatalogFieldsForm(initial);
    setCatalogFieldsEntry({
      slug: connector.slug,
      display_name: connector.display_name,
      description: connector.description,
      url: connector.url,
      auth_type: connector.auth_type,
      transport: connector.transport,
      fields,
      credentials_help_url: connector.credentials_help_url,
    });
  };

  // Poll GET /{id} until status is "connected" or "error".
  // timeoutMs defaults to 30 s for non-OAuth; pass 300_000 for OAuth flows
  // where the user needs time to complete authorization in the browser.
  const pollConnectorStatus = useCallback(
    (connectorId: string, timeoutMs = 30_000) => {
      if (connectPollRef.current) clearTimeout(connectPollRef.current);
      const deadline = Date.now() + timeoutMs;
      const poll = async () => {
        if (Date.now() > deadline) {
          toast.error(
            t("settings.connectors.connectTimeout" as Parameters<typeof t>[0]),
          );
          setOauthPending(null);
          await loadConnectors();
          return;
        }
        try {
          const c = await connectorsApi.get(connectorId);
          if (c.status === "connected") {
            toast.success(
              c.tool_count != null
                ? t("settings.connectors.connectSuccessTools", {
                    count: c.tool_count,
                  })
                : t("settings.connectors.connectSuccess"),
            );
            setOauthPending(null);
            await loadConnectors();
            return;
          }
          if (c.status === "error") {
            toast.error(
              c.error_message || t("settings.connectors.connectFailed"),
            );
            setOauthPending(null);
            await loadConnectors();
            return;
          }
        } catch {
          // network blip -- retry
        }
        connectPollRef.current = setTimeout(() => void poll(), 2000);
      };
      connectPollRef.current = setTimeout(() => void poll(), 2000);
    },
    [loadConnectors, t],
  );

  // Proactively probe OAuth metadata once the URL is filled in, but only
  // while the auth method is still unknown (auth_type === "none"). Best-effort:
  // a miss or error stays silent -- auth_type="none" remains correct and the
  // backend Site-1 probe is the create-time backstop.
  const maybeAutoDiscoverHttp = async () => {
    const url = addHttpForm.url.trim();
    if (
      !url ||
      addHttpForm.auth_type !== "none" ||
      httpDiscovering ||
      url === lastDiscoveredUrlRef.current
    ) {
      return;
    }
    lastDiscoveredUrlRef.current = url;
    setHttpDiscovering(true);
    try {
      const res = await connectorsApi.discover({
        url,
        transport: addHttpForm.transport,
      });
      if (res.discovered && res.auth_type === "oauth") {
        setAddHttpForm((f) =>
          // Guard: the user may have edited the URL while the probe was
          // in flight -- don't clobber a now-stale form.
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
            (res.oauth_registration_endpoint
              ? "settings.connectors.detectOAuthFound"
              : "settings.connectors.detectOAuthFoundManual") as Parameters<
              typeof t
            >[0],
          ),
        );
      }
    } catch {
      // Best-effort proactive probe -- swallow errors silently.
    } finally {
      setHttpDiscovering(false);
    }
  };

  const handleAddHttpConnector = async () => {
    if (!addHttpForm.display_name || !addHttpForm.url) {
      toast.error(t("settings.connectors.nameAndUrlRequired"));
      return;
    }
    if (addHttpForm.auth_type === "bearer" && !addHttpForm.api_key) {
      toast.error(
        t("settings.connectors.apiKeyRequired" as Parameters<typeof t>[0]),
      );
      return;
    }
    // OAuth without a (discovered) registration endpoint = no DCR available;
    // server requires a pre-registered Client ID. Mirror the backend 422.
    if (
      addHttpForm.auth_type === "oauth" &&
      !addHttpForm.oauth_registration_endpoint.trim() &&
      !addHttpForm.oauth_client_id.trim()
    ) {
      toast.error(
        t(
          "settings.connectors.clientIdRequiredNoDcr" as Parameters<
            typeof t
          >[0],
        ),
      );
      return;
    }
    try {
      const headerEntries = addHttpForm.headers
        .filter((h) => h.key.trim())
        .map((h) => ({ key: h.key.trim(), secret: h.secret, value: h.value }));
      const paramEntries = addHttpForm.params
        .filter((p) => p.key.trim())
        .map((p) => ({ key: p.key.trim(), secret: p.secret, value: p.value }));
      // Phase B: a bearer token is an explicit secret Authorization entry --
      // there is no api_key on the connector contract anymore.
      if (addHttpForm.auth_type === "bearer" && addHttpForm.api_key.trim()) {
        const tok = addHttpForm.api_key.trim();
        headerEntries.push({
          key: "Authorization",
          secret: true,
          value: tok.toLowerCase().startsWith("bearer ")
            ? tok
            : `Bearer ${tok}`,
        });
      }
      // OAuth: when the server has no discoverable metadata / no DCR
      // (e.g. GitHub MCP), the user supplies a registered client_id/secret
      // and optionally explicit endpoints -- the backend uses these instead
      // of auto-discovery.
      const oauthCreds: Record<string, string> = {};
      if (addHttpForm.auth_type === "oauth") {
        if (addHttpForm.oauth_client_id.trim())
          oauthCreds["client_id"] = addHttpForm.oauth_client_id.trim();
        if (addHttpForm.oauth_client_secret.trim())
          oauthCreds["client_secret"] = addHttpForm.oauth_client_secret.trim();
      }
      const res = await connectorsApi.create({
        display_name: addHttpForm.display_name,
        transport: addHttpForm.transport,
        url: addHttpForm.url,
        auth_type: addHttpForm.auth_type,
        headers: headerEntries.length > 0 ? headerEntries : null,
        params: paramEntries.length > 0 ? paramEntries : null,
        credentials:
          Object.keys(oauthCreds).length > 0 ? oauthCreds : undefined,
        oauth_authorization_endpoint:
          addHttpForm.auth_type === "oauth" &&
          addHttpForm.oauth_authorization_endpoint.trim()
            ? addHttpForm.oauth_authorization_endpoint.trim()
            : undefined,
        oauth_token_endpoint:
          addHttpForm.auth_type === "oauth" &&
          addHttpForm.oauth_token_endpoint.trim()
            ? addHttpForm.oauth_token_endpoint.trim()
            : undefined,
        oauth_registration_endpoint:
          addHttpForm.auth_type === "oauth" &&
          addHttpForm.oauth_registration_endpoint.trim()
            ? addHttpForm.oauth_registration_endpoint.trim()
            : undefined,
      });
      setAddHttpForm({
        display_name: "",
        url: "",
        transport: "http",
        auth_type: "none",
        api_key: "",
        oauth_client_id: "",
        oauth_client_secret: "",
        oauth_authorization_endpoint: "",
        oauth_token_endpoint: "",
        oauth_registration_endpoint: "",
        headers: [],
        params: [],
      });
      setActiveCustomForm(null);
      if (res.needs_auth && res.authorization_url) {
        window.open(res.authorization_url, "_blank");
        toast.info(t("settings.connectors.completeInBrowser"));
        setOauthPending(res.slug);
        await loadConnectors();
        pollConnectorStatus(res.id, 300_000);
      } else {
        toast.success(t("settings.connectors.httpConnectorAdded"));
        await loadConnectors();
        pollConnectorStatus(res.id);
      }
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("settings.connectors.addFailed"),
      );
    }
  };

  const handleAddStdioConnector = async () => {
    if (!addStdioForm.display_name || !addStdioForm.cmdline.trim()) {
      toast.error(t("settings.connectors.nameAndCmdRequired"));
      return;
    }
    try {
      const envDict: Record<string, string> = {};
      for (const { key, value } of addStdioForm.env) {
        if (key.trim()) envDict[key.trim()] = value;
      }
      const res = await connectorsApi.create({
        display_name: addStdioForm.display_name,
        transport: "stdio",
        command: addStdioForm.cmdline.trim(),
        args: [],
        env: Object.keys(envDict).length > 0 ? envDict : null,
      });
      setAddStdioForm({ display_name: "", cmdline: "", env: [] });
      setActiveCustomForm(null);
      await loadConnectors();
      pollConnectorStatus(res.id);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("settings.connectors.addFailed"),
      );
    }
  };

  const handleStartEditConnector = (connector: ConnectorItemType) => {
    const headersEntries = (connector.headers ?? []).map((h) => ({
      key: h.key,
      value: h.value ?? "",
      secret: h.secret,
    }));
    const paramsEntries = (connector.params ?? []).map((p) => ({
      key: p.key,
      value: p.value ?? "",
      secret: p.secret,
    }));
    if (connector.transport === "stdio") {
      const cmdline = [connector.command, ...(connector.args ?? [])]
        .filter(Boolean)
        .join(" ");
      const envEntries = Object.entries(connector.env ?? {}).map(
        ([key, value]) => ({ key, value }),
      );
      setEditForm({
        display_name: connector.display_name,
        url: "",
        transport: "stdio",
        auth_type: "none",
        api_key: "",
        headers: [],
        params: [],
        cmdline,
        env: envEntries,
        hasExistingKey: false,
      });
    } else {
      setEditForm({
        display_name: connector.display_name,
        url: connector.url ?? "",
        transport: (connector.transport as "http" | "sse") ?? "http",
        auth_type:
          connector.auth_type === "api_key"
            ? "bearer"
            : (connector.auth_type ?? "none"),
        api_key: "",
        headers: headersEntries,
        params: paramsEntries,
        cmdline: "",
        env: [],
        hasExistingKey: connector.has_api_key,
      });
    }
    setEditingConnectorId(connector.id);
  };

  const handleSaveEditConnector = async () => {
    if (!editingConnectorId) return;
    if (!editForm.display_name) {
      toast.error(t("settings.connectors.nameAndUrlRequired"));
      return;
    }
    if (
      editForm.auth_type === "bearer" &&
      !editForm.hasExistingKey &&
      !editForm.api_key
    ) {
      toast.error(
        t("settings.connectors.apiKeyRequired" as Parameters<typeof t>[0]),
      );
      return;
    }
    try {
      // Recommended stdio: only env is mutable (backend mirrors with a 422).
      // Don't send display_name / command / args at all.
      const payload: UpdateConnectorRequest =
        editingConnectorIsRecommended && editForm.transport === "stdio"
          ? {}
          : { display_name: editForm.display_name };
      if (editForm.transport === "stdio") {
        if (!editingConnectorIsRecommended) {
          const parts = editForm.cmdline.trim().split(/\s+/);
          payload.command = parts[0] ?? null;
          payload.args = parts.slice(1);
        }
        const envDict: Record<string, string> = {};
        for (const { key, value } of editForm.env) {
          if (key.trim()) envDict[key.trim()] = value;
        }
        payload.env = Object.keys(envDict).length > 0 ? envDict : null;
      } else {
        payload.url = editForm.url || null;
        payload.auth_type = editForm.auth_type || null;
        // Desired-state: send EVERY row with a non-empty key.
        // Secret rows with empty value are preserved (backend treats "" as "keep").
        // Only rows with an empty/whitespace key are dropped.
        const headerEntries = editForm.headers
          .filter((h) => h.key.trim())
          .map((h) => ({
            key: h.key.trim(),
            secret: h.secret,
            value: h.value,
          }));
        // Phase B: a freshly entered bearer token rotates the Authorization
        // secret entry (replacing any hydrated one -- no api_key field).
        if (editForm.auth_type === "bearer" && editForm.api_key.trim()) {
          const tok = editForm.api_key.trim();
          const withoutAuth = headerEntries.filter(
            (h) => h.key.toLowerCase() !== "authorization",
          );
          withoutAuth.push({
            key: "Authorization",
            secret: true,
            value: tok.toLowerCase().startsWith("bearer ")
              ? tok
              : `Bearer ${tok}`,
          });
          payload.headers = withoutAuth;
        } else {
          payload.headers = headerEntries.length > 0 ? headerEntries : null;
        }
        const paramEntries = editForm.params
          .filter((p) => p.key.trim())
          .map((p) => ({
            key: p.key.trim(),
            secret: p.secret,
            value: p.value,
          }));
        payload.params = paramEntries.length > 0 ? paramEntries : null;
      }
      const updated = await connectorsApi.update(editingConnectorId, payload);
      toast.success(t("settings.connectors.httpConnectorAdded"));
      setEditingConnectorId(null);
      await loadConnectors();
      if (updated.status === "connecting") {
        pollConnectorStatus(updated.id);
      }
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("settings.connectors.addFailed"),
      );
    }
  };

  const editingConnector = editingConnectorId
    ? (connectorsList.find(
        (connector) => connector.id === editingConnectorId,
      ) ?? null)
    : null;
  const editingConnectorIsRecommended =
    editingConnector?.connector_type === "recommended";

  return (
    <>
      <SettingsSection
        title={t("settings.connectors.title")}
        desc={t("settings.connectors.desc")}
        contentClassName="pt-2"
      >
        {/* Directory section */}
        <div>
          <div className="mb-3 flex items-center justify-between">
            <div>
              <div className="text-sm font-medium text-ink-heading">
                {t("settings.connectors.directorySection")}
              </div>
              <div className="mt-0.5 text-xs text-ink-meta">
                {t("settings.connectors.directoryDesc")}
              </div>
            </div>
          </div>
          {/* Every section now follows the same shape:
              H4 label row + uniform card grid below. Loose
              catalog entries get rolled into a synthetic
              "通用" group (kept at the head of the list) so
              there's no second visual unit in the page. */}
          <div className="space-y-5">
            {groupedDirectoryItems.map((group) => (
              <div key={group.slug}>
                <div className="mb-2 flex items-center gap-2">
                  {group.icon_url && (
                    <ConnectorIcon
                      iconUrl={group.icon_url}
                      name={group.display_name}
                      size="sm"
                    />
                  )}
                  <span className="text-sm font-medium text-ink-heading">
                    {group.display_name}
                  </span>
                  {group.description && (
                    <span className="text-xs text-ink-meta">
                      {group.description}
                    </span>
                  )}
                </div>
                <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
                  {group.connectors.map((connector) => {
                    const installedConnector = connectorsList.find(
                      (c) => c.slug === connector.slug,
                    );
                    const installed = !!installedConnector;
                    const connectorIcon = connector.icon_url ?? group.icon_url;
                    return (
                      <Card
                        key={connector.slug}
                        className="rounded-xl shadow-xs"
                      >
                        <CardContent className="py-3">
                          <div className="flex items-start gap-3">
                            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-soft text-brand">
                              <ConnectorIcon
                                iconUrl={connectorIcon}
                                name={connector.display_name}
                              />
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5">
                                <span className="text-sm font-medium text-ink-heading">
                                  {connector.display_name}
                                </span>
                                {installed && installedConnector && (
                                  <Badge
                                    variant={
                                      installedConnector.status === "connected"
                                        ? "success"
                                        : "outline"
                                    }
                                    className="text-[10px]"
                                  >
                                    {installedConnector.status === "connected"
                                      ? t("settings.connectors.connected")
                                      : t("settings.connectors.installed")}
                                  </Badge>
                                )}
                              </div>
                              {connector.description && (
                                <div className="mt-1 text-xs leading-4 text-ink-body line-clamp-2">
                                  {connector.description}
                                </div>
                              )}
                            </div>
                          </div>
                          <div className="mt-3 flex items-center gap-2">
                            {installed ? (
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-7 flex-1 text-xs"
                                disabled
                              >
                                {t("settings.connectors.added")}
                              </Button>
                            ) : (
                              <Button
                                size="sm"
                                className="h-7 flex-1 text-xs"
                                disabled={oauthPending === connector.slug}
                                onClick={() => {
                                  // Non-oauth connectors that declare
                                  // header/param schema use the new dialog.
                                  if (
                                    connector.header_schema.length +
                                      connector.param_schema.length >
                                      0 &&
                                    connector.auth_type !== "oauth"
                                  ) {
                                    handleCatalogFieldsClick(connector);
                                  } else {
                                    handleOAuthButtonClick({
                                      slug: connector.slug,
                                      display_name: connector.display_name,
                                      description: connector.description,
                                      url: connector.url,
                                      auth_type: connector.auth_type,
                                      transport: connector.transport,
                                      oauth_credentials_schema:
                                        connector.oauth_credentials_schema,
                                      credentials_help_url:
                                        connector.credentials_help_url,
                                      command: connector.command,
                                      args: connector.args,
                                      working_dir: connector.working_dir,
                                      env: connector.env,
                                    });
                                  }
                                }}
                              >
                                {oauthPending === connector.slug ? (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                ) : (
                                  t("common.connect")
                                )}
                              </Button>
                            )}
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              </div>
            ))}
            {connectorsLoading && directoryItems.length === 0 && (
              <div className="flex items-center justify-center py-8 text-ink-meta">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
            )}
          </div>
        </div>

        {/* Installed connectors */}
        {connectorsList.length > 0 && (
          <div className="mt-6">
            <div className="mb-3 text-sm font-medium text-ink-heading">
              {t("settings.connectors.installedConnectors")}
            </div>
            <Card className="rounded-xl shadow-xs">
              <CardContent className="px-5 py-0">
                {connectorsList.map((connector) => {
                  const dirItem = directoryItems.find((d) =>
                    d.kind === "connector"
                      ? d.slug === connector.slug
                      : d.connectors.some((c) => c.slug === connector.slug),
                  );
                  const iconUrl =
                    dirItem?.kind === "connector"
                      ? dirItem.icon_url
                      : (dirItem?.icon_url ?? null);
                  return (
                    <div
                      key={connector.id}
                      className="border-b border-surface-border last:border-b-0"
                    >
                      <div className="flex items-center gap-3 py-3">
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-soft text-brand">
                          <ConnectorIcon
                            iconUrl={iconUrl}
                            name={connector.display_name}
                          />
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-ink-heading">
                              {connector.display_name}
                            </span>
                            <Badge variant="outline" className="text-[10px]">
                              {connector.transport === "http"
                                ? "HTTP"
                                : connector.transport === "sse"
                                  ? "SSE"
                                  : "Stdio"}
                            </Badge>
                            {connector.connector_type === "recommended" && (
                              <Badge variant="outline" className="text-[10px]">
                                {t(
                                  "settings.connectors.recommended" as Parameters<
                                    typeof t
                                  >[0],
                                )}
                              </Badge>
                            )}
                          </div>
                          <div className="mt-0.5 flex items-center gap-2 text-2xs text-ink-meta">
                            {connector.url && (
                              <span className="truncate max-w-[200px]">
                                {connector.url}
                              </span>
                            )}
                            {connector.command && (
                              <span className="font-mono">
                                {connector.command}
                              </span>
                            )}
                            {connector.tool_count != null && (
                              <span>
                                {t("settings.connectors.toolsCount", {
                                  count: connector.tool_count,
                                })}
                              </span>
                            )}
                          </div>
                        </div>
                        {connector.status === "connected" ? (
                          <Badge variant="success" className="gap-1">
                            <Check className="h-2.5 w-2.5" />{" "}
                            {t("settings.connectors.connected")}
                          </Badge>
                        ) : connector.status === "error" ? (
                          <Badge variant="error">
                            {t("settings.connectors.error")}
                          </Badge>
                        ) : connector.status === "connecting" ||
                          connector.status === "pending_auth" ? (
                          <Badge variant="outline" className="gap-1">
                            <Loader2 className="h-2.5 w-2.5 animate-spin" />
                            {t(
                              "settings.connectors.connecting" as Parameters<
                                typeof t
                              >[0],
                            )}
                          </Badge>
                        ) : (
                          <Badge variant="outline">
                            {t("common.notTested")}
                          </Badge>
                        )}
                        <Switch
                          checked={connector.enabled}
                          onCheckedChange={() =>
                            void handleToggleConnector(connector)
                          }
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0 hover:bg-[#f7f8fa] hover:text-current dark:hover:bg-surface-soft"
                          disabled={connectorTesting === connector.id}
                          onClick={() => void handleTestConnector(connector.id)}
                        >
                          {connectorTesting === connector.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <RefreshCw className="h-3 w-3" />
                          )}
                        </Button>
                        {connector.auth_type !== "oauth" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 p-0 hover:bg-[#f7f8fa] hover:text-current dark:hover:bg-surface-soft"
                            onClick={() => handleStartEditConnector(connector)}
                          >
                            <FilePenLine className="h-3 w-3" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0 text-[#f54b4b] hover:bg-[#f54b4b]/10 hover:text-[#f54b4b]"
                          onClick={() =>
                            void handleDeleteConnector(connector.id)
                          }
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          </div>
        )}

        {/* Add custom connectors */}
        <div className="mt-6">
          <div className="mb-3 text-sm font-medium text-ink-heading">
            {t("settings.connectors.customConnectors")}
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              className="gap-1.5 text-xs"
              onClick={() => setActiveCustomForm("http")}
            >
              <Plus className="h-3.5 w-3.5" />
              {t("settings.connectors.addHttpConnector")}
            </Button>
            <Button
              variant="outline"
              className="gap-1.5 text-xs"
              onClick={() => setActiveCustomForm("stdio")}
            >
              <Plus className="h-3.5 w-3.5" />
              {t("settings.connectors.addStdioConnector")}
            </Button>
          </div>
        </div>
      </SettingsSection>

      {/* Connector Edit Dialog */}
      <Dialog
        open={editingConnector !== null}
        onOpenChange={(open) => {
          if (!open) setEditingConnectorId(null);
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{t("common.edit")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div>
              <div className="mb-1 text-xs text-ink-meta">
                {t("common.name")}
              </div>
              <input
                className={cn(
                  "w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand",
                  editingConnectorIsRecommended &&
                    "cursor-not-allowed bg-zinc-200 text-ink-meta focus:ring-0 dark:bg-zinc-800",
                )}
                value={editForm.display_name}
                readOnly={editingConnectorIsRecommended}
                onChange={
                  editingConnectorIsRecommended
                    ? undefined
                    : (e) =>
                        setEditForm((f) => ({
                          ...f,
                          display_name: e.target.value,
                        }))
                }
              />
            </div>
            {editForm.transport !== "stdio" ? (
              <>
                <div>
                  <div className="mb-1 text-xs text-ink-meta">
                    {t("settings.connectors.url")}
                  </div>
                  <input
                    className={cn(
                      "w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand",
                      editingConnectorIsRecommended &&
                        "cursor-not-allowed bg-zinc-200 text-ink-meta focus:ring-0 dark:bg-zinc-800",
                    )}
                    value={editForm.url}
                    readOnly={editingConnectorIsRecommended}
                    onChange={(e) =>
                      setEditForm((f) => ({ ...f, url: e.target.value }))
                    }
                  />
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <div className="mb-1 text-xs text-ink-meta">
                      {t("settings.connectors.transport")}
                    </div>
                    <Select
                      value={editForm.transport}
                      disabled={editingConnectorIsRecommended}
                      onValueChange={(value) =>
                        setEditForm((f) => ({
                          ...f,
                          transport: value as "http" | "sse",
                        }))
                      }
                    >
                      <SelectTrigger size="sm" className="h-8 w-full text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="http">HTTP</SelectItem>
                        <SelectItem value="sse">SSE</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <div className="mb-1 text-xs text-ink-meta">
                      {t(
                        "settings.connectors.authType" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </div>
                    <Select
                      value={editForm.auth_type}
                      disabled={editingConnectorIsRecommended}
                      onValueChange={(value) =>
                        setEditForm((f) => ({ ...f, auth_type: value }))
                      }
                    >
                      <SelectTrigger size="sm" className="h-8 w-full text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">
                          {t(
                            "settings.connectors.authTypeNone" as Parameters<
                              typeof t
                            >[0],
                          )}
                        </SelectItem>
                        <SelectItem value="bearer">
                          {t(
                            "settings.connectors.authTypeBearer" as Parameters<
                              typeof t
                            >[0],
                          )}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                {editForm.auth_type === "bearer" && (
                  <div>
                    <div className="mb-1 text-xs text-ink-meta">API Key</div>
                    <input
                      className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                      type="password"
                      placeholder={
                        editForm.hasExistingKey && !editForm.api_key
                          ? "••••••••"
                          : t(
                              "settings.connectors.apiKeyPlaceholder" as Parameters<
                                typeof t
                              >[0],
                            )
                      }
                      value={editForm.api_key}
                      onChange={(e) =>
                        setEditForm((f) => ({
                          ...f,
                          api_key: e.target.value,
                        }))
                      }
                    />
                  </div>
                )}
                <div>
                  <div className="mb-1 flex items-center justify-between text-xs text-ink-meta">
                    <span>{t("settings.connectors.customHeaders")}</span>
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 rounded-lg border border-surface-border px-2 py-1 text-xs text-ink-meta transition-colors hover:border-brand hover:text-brand disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-surface-border disabled:hover:text-ink-meta"
                      disabled={
                        editForm.headers.length > 0 &&
                        editForm.headers[
                          editForm.headers.length - 1
                        ].key.trim() === ""
                      }
                      onClick={() =>
                        setEditForm((f) => ({
                          ...f,
                          headers: [
                            ...f.headers,
                            { key: "", value: "", secret: false },
                          ],
                        }))
                      }
                    >
                      {t("settings.connectors.addHeader")}
                    </button>
                  </div>
                  {editForm.headers.length > 0 && (
                    <div className="space-y-1.5">
                      {editForm.headers.map((h, i) => (
                        <div key={i} className="space-y-0.5">
                          <div className="flex items-center gap-1.5">
                            <input
                              className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                              placeholder="Header-Name"
                              value={h.key}
                              onChange={(e) =>
                                setEditForm((f) => {
                                  const headers = [...f.headers];
                                  headers[i] = {
                                    ...headers[i],
                                    key: e.target.value,
                                  };
                                  return { ...f, headers };
                                })
                              }
                            />
                            <input
                              className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                              type={h.secret ? "password" : "text"}
                              placeholder={
                                h.secret && !h.value
                                  ? t(
                                      "settings.connectors.secretKept" as Parameters<
                                        typeof t
                                      >[0],
                                    )
                                  : "value"
                              }
                              value={h.value}
                              onChange={(e) =>
                                setEditForm((f) => {
                                  const headers = [...f.headers];
                                  headers[i] = {
                                    ...headers[i],
                                    value: e.target.value,
                                  };
                                  return { ...f, headers };
                                })
                              }
                            />
                            <button
                              type="button"
                              aria-pressed={h.secret}
                              title={t(
                                "settings.connectors.encrypt" as Parameters<
                                  typeof t
                                >[0],
                              )}
                              aria-label={t(
                                "settings.connectors.encrypt" as Parameters<
                                  typeof t
                                >[0],
                              )}
                              onClick={() =>
                                setEditForm((f) => {
                                  const headers = [...f.headers];
                                  headers[i] = {
                                    ...headers[i],
                                    secret: !headers[i].secret,
                                  };
                                  return { ...f, headers };
                                })
                              }
                              className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors ${
                                h.secret
                                  ? "border-brand bg-brand/10 text-brand"
                                  : "border-surface-border text-ink-meta hover:text-ink-heading"
                              }`}
                            >
                              {h.secret ? (
                                <Lock className="h-3.5 w-3.5" />
                              ) : (
                                <LockOpen className="h-3.5 w-3.5" />
                              )}
                            </button>
                            <button
                              type="button"
                              className="px-1 text-ink-meta hover:text-red-500"
                              onClick={() =>
                                setEditForm((f) => ({
                                  ...f,
                                  headers: f.headers.filter((_, j) => j !== i),
                                }))
                              }
                            >
                              ×
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between text-xs text-ink-meta">
                    <span>
                      {t(
                        "settings.connectors.paramsLabel" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </span>
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 rounded-lg border border-surface-border px-2 py-1 text-xs text-ink-meta transition-colors hover:border-brand hover:text-brand disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-surface-border disabled:hover:text-ink-meta"
                      disabled={
                        editForm.params.length > 0 &&
                        editForm.params[
                          editForm.params.length - 1
                        ].key.trim() === ""
                      }
                      onClick={() =>
                        setEditForm((f) => ({
                          ...f,
                          params: [
                            ...f.params,
                            { key: "", value: "", secret: false },
                          ],
                        }))
                      }
                    >
                      {t(
                        "settings.connectors.addParam" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </button>
                  </div>
                  {editForm.params.length > 0 && (
                    <div className="space-y-1.5">
                      {editForm.params.map((p, i) => (
                        <div key={i} className="space-y-0.5">
                          <div className="flex items-center gap-1.5">
                            <input
                              className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                              placeholder="param-name"
                              value={p.key}
                              onChange={(e) =>
                                setEditForm((f) => {
                                  const params = [...f.params];
                                  params[i] = {
                                    ...params[i],
                                    key: e.target.value,
                                  };
                                  return { ...f, params };
                                })
                              }
                            />
                            <input
                              className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                              type={p.secret ? "password" : "text"}
                              placeholder={
                                p.secret && !p.value
                                  ? t(
                                      "settings.connectors.secretKept" as Parameters<
                                        typeof t
                                      >[0],
                                    )
                                  : "value"
                              }
                              value={p.value}
                              onChange={(e) =>
                                setEditForm((f) => {
                                  const params = [...f.params];
                                  params[i] = {
                                    ...params[i],
                                    value: e.target.value,
                                  };
                                  return { ...f, params };
                                })
                              }
                            />
                            <button
                              type="button"
                              aria-pressed={p.secret}
                              title={t(
                                "settings.connectors.encrypt" as Parameters<
                                  typeof t
                                >[0],
                              )}
                              aria-label={t(
                                "settings.connectors.encrypt" as Parameters<
                                  typeof t
                                >[0],
                              )}
                              onClick={() =>
                                setEditForm((f) => {
                                  const params = [...f.params];
                                  params[i] = {
                                    ...params[i],
                                    secret: !params[i].secret,
                                  };
                                  return { ...f, params };
                                })
                              }
                              className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors ${
                                p.secret
                                  ? "border-brand bg-brand/10 text-brand"
                                  : "border-surface-border text-ink-meta hover:text-ink-heading"
                              }`}
                            >
                              {p.secret ? (
                                <Lock className="h-3.5 w-3.5" />
                              ) : (
                                <LockOpen className="h-3.5 w-3.5" />
                              )}
                            </button>
                            <button
                              type="button"
                              className="px-1 text-ink-meta hover:text-red-500"
                              onClick={() =>
                                setEditForm((f) => ({
                                  ...f,
                                  params: f.params.filter((_, j) => j !== i),
                                }))
                              }
                            >
                              ×
                            </button>
                          </div>
                          {p.secret && (
                            <p className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-[11px] leading-4 text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                              {t(
                                "settings.connectors.secretParamWarning" as Parameters<
                                  typeof t
                                >[0],
                              )}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <>
                <div>
                  <div className="mb-1 text-xs text-ink-meta">
                    {t("settings.connectors.cmdline")}
                  </div>
                  <input
                    className={cn(
                      "w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand",
                      editingConnectorIsRecommended &&
                        "cursor-not-allowed bg-zinc-200 text-ink-meta focus:ring-0 dark:bg-zinc-800",
                    )}
                    value={editForm.cmdline}
                    readOnly={editingConnectorIsRecommended}
                    onChange={
                      editingConnectorIsRecommended
                        ? undefined
                        : (e) =>
                            setEditForm((f) => ({
                              ...f,
                              cmdline: e.target.value,
                            }))
                    }
                  />
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between text-xs text-ink-meta">
                    <span>
                      {t(
                        "settings.connectors.envVars" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </span>
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 rounded-lg border border-surface-border px-2 py-1 text-xs text-ink-meta transition-colors hover:border-brand hover:text-brand disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-surface-border disabled:hover:text-ink-meta"
                      disabled={
                        editForm.env.length > 0 &&
                        editForm.env[editForm.env.length - 1].key.trim() === ""
                      }
                      onClick={() =>
                        setEditForm((f) => ({
                          ...f,
                          env: [...f.env, { key: "", value: "" }],
                        }))
                      }
                    >
                      {t(
                        "settings.connectors.addEnvVar" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </button>
                  </div>
                  {editForm.env.length > 0 && (
                    <div className="space-y-1.5">
                      {editForm.env.map((e, i) => (
                        <div key={i} className="flex items-center gap-1.5">
                          <input
                            className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                            placeholder="KEY"
                            value={e.key}
                            onChange={(ev) =>
                              setEditForm((f) => {
                                const env = [...f.env];
                                env[i] = { ...env[i], key: ev.target.value };
                                return { ...f, env };
                              })
                            }
                          />
                          <input
                            className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                            placeholder="value"
                            value={e.value}
                            onChange={(ev) =>
                              setEditForm((f) => {
                                const env = [...f.env];
                                env[i] = { ...env[i], value: ev.target.value };
                                return { ...f, env };
                              })
                            }
                          />
                          <button
                            type="button"
                            className="px-1 text-ink-meta hover:text-red-500"
                            onClick={() =>
                              setEditForm((f) => ({
                                ...f,
                                env: f.env.filter((_, j) => j !== i),
                              }))
                            }
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setEditingConnectorId(null)}
            >
              {t("common.cancel")}
            </Button>
            <Button onClick={() => void handleSaveEditConnector()}>
              {t("common.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add HTTP Connector Dialog */}
      <Dialog
        open={showAddHttpDialog}
        onOpenChange={(open) => {
          if (open) {
            setActiveCustomForm("http");
            return;
          }
          setActiveCustomForm(null);
          setAddHttpForm({
            display_name: "",
            url: "",
            transport: "http",
            auth_type: "none",
            api_key: "",
            oauth_client_id: "",
            oauth_client_secret: "",
            oauth_authorization_endpoint: "",
            oauth_token_endpoint: "",
            oauth_registration_endpoint: "",
            headers: [],
            params: [],
          });
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {t("settings.connectors.addHttpConnector")}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div>
              <div className="mb-1 text-xs text-ink-meta">
                {t("common.name")}
              </div>
              <input
                className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                placeholder="My MCP Server"
                value={addHttpForm.display_name}
                onChange={(e) =>
                  setAddHttpForm((f) => ({
                    ...f,
                    display_name: e.target.value,
                  }))
                }
              />
            </div>
            <div>
              <div className="mb-1 flex items-center gap-2 text-xs text-ink-meta">
                <span>{t("settings.connectors.url")}</span>
                {httpDiscovering && (
                  <span className="flex items-center gap-1">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    {t(
                      "settings.connectors.detecting" as Parameters<
                        typeof t
                      >[0],
                    )}
                  </span>
                )}
              </div>
              <input
                className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                placeholder="https://example.com/mcp"
                value={addHttpForm.url}
                onChange={(e) =>
                  setAddHttpForm((f) => ({
                    ...f,
                    url: e.target.value,
                  }))
                }
                onBlur={() => void maybeAutoDiscoverHttp()}
              />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <div className="mb-1 text-xs text-ink-meta">
                  {t("settings.connectors.transport")}
                </div>
                <Select
                  value={addHttpForm.transport}
                  onValueChange={(value) =>
                    setAddHttpForm((f) => ({
                      ...f,
                      transport: value as "http" | "sse",
                    }))
                  }
                >
                  <SelectTrigger size="sm" className="h-8 w-full text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="http">HTTP</SelectItem>
                    <SelectItem value="sse">SSE</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <div className="mb-1 text-xs text-ink-meta">
                  {t("settings.connectors.authType" as Parameters<typeof t>[0])}
                </div>
                <Select
                  value={addHttpForm.auth_type}
                  onValueChange={(value) =>
                    setAddHttpForm((f) => ({
                      ...f,
                      auth_type: value as "none" | "bearer" | "oauth",
                    }))
                  }
                >
                  <SelectTrigger size="sm" className="h-8 w-full text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">
                      {t(
                        "settings.connectors.authTypeNone" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </SelectItem>
                    <SelectItem value="bearer">
                      {t(
                        "settings.connectors.authTypeBearer" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </SelectItem>
                    <SelectItem value="oauth">
                      {t(
                        "settings.connectors.authTypeOAuth" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            {addHttpForm.auth_type === "oauth" && (
              <div className="space-y-2">
                <div className="rounded-lg border border-surface-border bg-surface-muted/40 px-3 py-2 text-xs text-ink-meta">
                  {t(
                    "settings.connectors.oauthCustomHint" as Parameters<
                      typeof t
                    >[0],
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <div className="mb-1 text-xs text-ink-meta">
                      {t(
                        "settings.connectors.oauthAuthEndpoint" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </div>
                    <input
                      className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                      placeholder="https://…/authorize"
                      value={addHttpForm.oauth_authorization_endpoint}
                      onChange={(e) =>
                        setAddHttpForm((f) => ({
                          ...f,
                          oauth_authorization_endpoint: e.target.value,
                        }))
                      }
                    />
                  </div>
                  <div>
                    <div className="mb-1 text-xs text-ink-meta">
                      {t(
                        "settings.connectors.oauthTokenEndpoint" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </div>
                    <input
                      className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                      placeholder="https://…/token"
                      value={addHttpForm.oauth_token_endpoint}
                      onChange={(e) =>
                        setAddHttpForm((f) => ({
                          ...f,
                          oauth_token_endpoint: e.target.value,
                        }))
                      }
                    />
                  </div>
                  <div>
                    <div className="mb-1 text-xs text-ink-meta">
                      {t(
                        "settings.connectors.oauthRegistrationEndpoint" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </div>
                    <input
                      className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                      placeholder="https://…/register"
                      value={addHttpForm.oauth_registration_endpoint}
                      onChange={(e) =>
                        setAddHttpForm((f) => ({
                          ...f,
                          oauth_registration_endpoint: e.target.value,
                        }))
                      }
                    />
                  </div>
                  <div>
                    <div className="mb-1 text-xs text-ink-meta">
                      {t(
                        (addHttpForm.oauth_registration_endpoint.trim()
                          ? "settings.connectors.oauthClientId"
                          : "settings.connectors.oauthClientIdRequired") as Parameters<
                          typeof t
                        >[0],
                      )}
                    </div>
                    <input
                      className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                      placeholder="Ov23li..."
                      value={addHttpForm.oauth_client_id}
                      onChange={(e) =>
                        setAddHttpForm((f) => ({
                          ...f,
                          oauth_client_id: e.target.value,
                        }))
                      }
                    />
                  </div>
                  <div>
                    <div className="mb-1 text-xs text-ink-meta">
                      {t(
                        "settings.connectors.oauthClientSecret" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </div>
                    <input
                      type="password"
                      className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                      placeholder="••••••••"
                      value={addHttpForm.oauth_client_secret}
                      onChange={(e) =>
                        setAddHttpForm((f) => ({
                          ...f,
                          oauth_client_secret: e.target.value,
                        }))
                      }
                    />
                  </div>
                </div>
              </div>
            )}
            {addHttpForm.auth_type === "bearer" && (
              <div>
                <div className="mb-1 text-xs text-ink-meta">API Key</div>
                <input
                  className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                  placeholder="sk-..."
                  type="password"
                  value={addHttpForm.api_key}
                  onChange={(e) =>
                    setAddHttpForm((f) => ({
                      ...f,
                      api_key: e.target.value,
                    }))
                  }
                />
              </div>
            )}
            <div>
              <div className="mb-1 flex items-center justify-between text-xs text-ink-meta">
                <span>{t("settings.connectors.customHeaders")}</span>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-lg border border-surface-border px-2 py-1 text-xs text-ink-meta transition-colors hover:border-brand hover:text-brand disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-surface-border disabled:hover:text-ink-meta"
                  disabled={
                    addHttpForm.headers.length > 0 &&
                    addHttpForm.headers[
                      addHttpForm.headers.length - 1
                    ].key.trim() === ""
                  }
                  onClick={() =>
                    setAddHttpForm((f) => ({
                      ...f,
                      headers: [
                        ...f.headers,
                        { key: "", value: "", secret: false },
                      ],
                    }))
                  }
                >
                  {t("settings.connectors.addHeader")}
                </button>
              </div>
              {addHttpForm.headers.length > 0 && (
                <div className="space-y-1.5">
                  {addHttpForm.headers.map((h, i) => (
                    <div key={i} className="flex items-center gap-1.5">
                      <input
                        className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                        placeholder="Header-Name"
                        value={h.key}
                        onChange={(e) =>
                          setAddHttpForm((f) => {
                            const headers = [...f.headers];
                            headers[i] = { ...headers[i], key: e.target.value };
                            return { ...f, headers };
                          })
                        }
                      />
                      <input
                        className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                        type={h.secret ? "password" : "text"}
                        placeholder="value"
                        value={h.value}
                        onChange={(e) =>
                          setAddHttpForm((f) => {
                            const headers = [...f.headers];
                            headers[i] = {
                              ...headers[i],
                              value: e.target.value,
                            };
                            return { ...f, headers };
                          })
                        }
                      />
                      <button
                        type="button"
                        aria-pressed={h.secret}
                        title={t(
                          "settings.connectors.encrypt" as Parameters<
                            typeof t
                          >[0],
                        )}
                        aria-label={t(
                          "settings.connectors.encrypt" as Parameters<
                            typeof t
                          >[0],
                        )}
                        onClick={() =>
                          setAddHttpForm((f) => {
                            const headers = [...f.headers];
                            headers[i] = {
                              ...headers[i],
                              secret: !headers[i].secret,
                            };
                            return { ...f, headers };
                          })
                        }
                        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors ${
                          h.secret
                            ? "border-brand bg-brand/10 text-brand"
                            : "border-surface-border text-ink-meta hover:text-ink-heading"
                        }`}
                      >
                        {h.secret ? (
                          <Lock className="h-3.5 w-3.5" />
                        ) : (
                          <LockOpen className="h-3.5 w-3.5" />
                        )}
                      </button>
                      <button
                        type="button"
                        className="px-1 text-ink-meta hover:text-red-500"
                        onClick={() =>
                          setAddHttpForm((f) => ({
                            ...f,
                            headers: f.headers.filter((_, j) => j !== i),
                          }))
                        }
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between text-xs text-ink-meta">
                <span>
                  {t(
                    "settings.connectors.paramsLabel" as Parameters<
                      typeof t
                    >[0],
                  )}
                </span>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-lg border border-surface-border px-2 py-1 text-xs text-ink-meta transition-colors hover:border-brand hover:text-brand disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-surface-border disabled:hover:text-ink-meta"
                  disabled={
                    addHttpForm.params.length > 0 &&
                    addHttpForm.params[
                      addHttpForm.params.length - 1
                    ].key.trim() === ""
                  }
                  onClick={() =>
                    setAddHttpForm((f) => ({
                      ...f,
                      params: [
                        ...f.params,
                        { key: "", value: "", secret: false },
                      ],
                    }))
                  }
                >
                  {t("settings.connectors.addParam" as Parameters<typeof t>[0])}
                </button>
              </div>
              {addHttpForm.params.length > 0 && (
                <div className="space-y-1.5">
                  {addHttpForm.params.map((p, i) => (
                    <div key={i} className="space-y-0.5">
                      <div className="flex items-center gap-1.5">
                        <input
                          className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                          placeholder="param-name"
                          value={p.key}
                          onChange={(e) =>
                            setAddHttpForm((f) => {
                              const params = [...f.params];
                              params[i] = {
                                ...params[i],
                                key: e.target.value,
                              };
                              return { ...f, params };
                            })
                          }
                        />
                        <input
                          className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                          type={p.secret ? "password" : "text"}
                          placeholder="value"
                          value={p.value}
                          onChange={(e) =>
                            setAddHttpForm((f) => {
                              const params = [...f.params];
                              params[i] = {
                                ...params[i],
                                value: e.target.value,
                              };
                              return { ...f, params };
                            })
                          }
                        />
                        <button
                          type="button"
                          aria-pressed={p.secret}
                          title={t(
                            "settings.connectors.encrypt" as Parameters<
                              typeof t
                            >[0],
                          )}
                          aria-label={t(
                            "settings.connectors.encrypt" as Parameters<
                              typeof t
                            >[0],
                          )}
                          onClick={() =>
                            setAddHttpForm((f) => {
                              const params = [...f.params];
                              params[i] = {
                                ...params[i],
                                secret: !params[i].secret,
                              };
                              return { ...f, params };
                            })
                          }
                          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors ${
                            p.secret
                              ? "border-brand bg-brand/10 text-brand"
                              : "border-surface-border text-ink-meta hover:text-ink-heading"
                          }`}
                        >
                          {p.secret ? (
                            <Lock className="h-3.5 w-3.5" />
                          ) : (
                            <LockOpen className="h-3.5 w-3.5" />
                          )}
                        </button>
                        <button
                          type="button"
                          className="px-1 text-ink-meta hover:text-red-500"
                          onClick={() =>
                            setAddHttpForm((f) => ({
                              ...f,
                              params: f.params.filter((_, j) => j !== i),
                            }))
                          }
                        >
                          ×
                        </button>
                      </div>
                      {p.secret && (
                        <p className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-[11px] leading-4 text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                          {t(
                            "settings.connectors.secretParamWarning" as Parameters<
                              typeof t
                            >[0],
                          )}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setActiveCustomForm(null);
                setAddHttpForm({
                  display_name: "",
                  url: "",
                  transport: "http",
                  auth_type: "none",
                  api_key: "",
                  oauth_client_id: "",
                  oauth_client_secret: "",
                  oauth_authorization_endpoint: "",
                  oauth_token_endpoint: "",
                  oauth_registration_endpoint: "",
                  headers: [],
                  params: [],
                });
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button onClick={() => void handleAddHttpConnector()}>
              {t("common.add")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add Stdio Connector Dialog */}
      <Dialog
        open={showAddStdioDialog}
        onOpenChange={(open) => {
          if (open) {
            setActiveCustomForm("stdio");
            return;
          }
          setActiveCustomForm(null);
          setAddStdioForm({
            display_name: "",
            cmdline: "",
            env: [],
          });
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {t("settings.connectors.addStdioConnector")}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div>
              <div className="mb-1 text-xs text-ink-meta">
                {t("common.name")}
              </div>
              <input
                className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                placeholder="My CLI Tool"
                value={addStdioForm.display_name}
                onChange={(e) =>
                  setAddStdioForm((f) => ({
                    ...f,
                    display_name: e.target.value,
                  }))
                }
              />
            </div>
            <div>
              <div className="mb-1 text-xs text-ink-meta">
                {t("settings.connectors.cmdline")}
              </div>
              <input
                className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                placeholder="npx -Y @modelcontextprotocol/server-github"
                value={addStdioForm.cmdline}
                onChange={(e) =>
                  setAddStdioForm((f) => ({
                    ...f,
                    cmdline: e.target.value,
                  }))
                }
              />
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between text-xs text-ink-meta">
                <span>
                  {t("settings.connectors.envVars" as Parameters<typeof t>[0])}
                </span>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-lg border border-surface-border px-2 py-1 text-xs text-ink-meta transition-colors hover:border-brand hover:text-brand disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-surface-border disabled:hover:text-ink-meta"
                  disabled={
                    addStdioForm.env.length > 0 &&
                    addStdioForm.env[addStdioForm.env.length - 1].key.trim() ===
                      ""
                  }
                  onClick={() =>
                    setAddStdioForm((f) => ({
                      ...f,
                      env: [...f.env, { key: "", value: "" }],
                    }))
                  }
                >
                  {t(
                    "settings.connectors.addEnvVar" as Parameters<typeof t>[0],
                  )}
                </button>
              </div>
              {addStdioForm.env.length > 0 && (
                <div className="space-y-1.5">
                  {addStdioForm.env.map((e, i) => (
                    <div key={i} className="flex items-center gap-1.5">
                      <input
                        className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                        placeholder="KEY"
                        value={e.key}
                        onChange={(ev) =>
                          setAddStdioForm((f) => {
                            const env = [...f.env];
                            env[i] = { ...env[i], key: ev.target.value };
                            return { ...f, env };
                          })
                        }
                      />
                      <input
                        className="min-w-0 flex-1 rounded-lg border border-surface-border bg-background px-3 py-1.5 font-mono text-sm text-ink-heading outline-none focus:ring-1 focus:ring-brand"
                        placeholder="value"
                        value={e.value}
                        onChange={(ev) =>
                          setAddStdioForm((f) => {
                            const env = [...f.env];
                            env[i] = { ...env[i], value: ev.target.value };
                            return { ...f, env };
                          })
                        }
                      />
                      <button
                        type="button"
                        className="px-1 text-ink-meta hover:text-red-500"
                        onClick={() =>
                          setAddStdioForm((f) => ({
                            ...f,
                            env: f.env.filter((_, j) => j !== i),
                          }))
                        }
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setActiveCustomForm(null);
                setAddStdioForm({
                  display_name: "",
                  cmdline: "",
                  env: [],
                });
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button onClick={() => void handleAddStdioConnector()}>
              {t("common.add")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* OAuth Client Credentials Dialog -- fields driven by entry.oauth_credentials_schema */}
      <AlertDialog
        open={oauthCredentialsEntry !== null}
        onOpenChange={(open) => {
          if (!open) setOauthCredentialsEntry(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("settings.connectors.configureCredentials", {
                name: oauthCredentialsEntry?.display_name ?? "",
              })}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div>
                {t("settings.connectors.noAutoRegister")}
                {oauthCredentialsEntry?.credentials_help_url && (
                  <a
                    href={oauthCredentialsEntry.credentials_help_url}
                    target="_blank"
                    rel="noreferrer"
                    className="ml-1 inline-flex items-center gap-0.5 text-brand underline underline-offset-2"
                  >
                    {t("settings.connectors.goToGet")}
                    <ArrowUpRight className="h-3 w-3" />
                  </a>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-3 py-2">
            {(oauthCredentialsEntry?.oauth_credentials_schema ?? []).map(
              (field) => (
                <div key={field.key}>
                  <div className="mb-1 text-xs text-ink-meta">
                    {field.label}
                    {field.required && (
                      <span className="ml-0.5 text-error-text">*</span>
                    )}
                  </div>
                  <input
                    className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 text-sm text-ink-heading font-mono outline-none focus:ring-1 focus:ring-brand"
                    placeholder={field.placeholder ?? ""}
                    type={field.secret ? "password" : "text"}
                    value={oauthCredentialsForm[field.key] ?? ""}
                    onChange={(e) =>
                      setOauthCredentialsForm((prev) => ({
                        ...prev,
                        [field.key]: e.target.value,
                      }))
                    }
                  />
                </div>
              ),
            )}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setOauthCredentialsEntry(null)}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (!oauthCredentialsEntry) return;
                const requiredMissing = (
                  oauthCredentialsEntry.oauth_credentials_schema ?? []
                ).some(
                  (f) => f.required && !oauthCredentialsForm[f.key]?.trim(),
                );
                if (requiredMissing) {
                  toast.error(t("settings.connectors.allRequired"));
                  return;
                }
                const entry = oauthCredentialsEntry;
                setOauthCredentialsEntry(null);
                const creds: Record<string, string> = {};
                for (const [k, v] of Object.entries(oauthCredentialsForm)) {
                  if (v.trim()) creds[k] = v.trim();
                }
                if (
                  entry.auth_type === "api_key" ||
                  entry.auth_type === "bearer"
                ) {
                  void (async () => {
                    try {
                      // Phase B: a bearer credential is an explicit secret
                      // Authorization entry, not an api_key field.
                      const tok = creds["api_key"];
                      const res = await connectorsApi.create({
                        slug: entry.slug,
                        display_name: entry.display_name,
                        transport: entry.transport ?? "http",
                        url: entry.url ?? "",
                        auth_type: "bearer",
                        headers: tok
                          ? [
                              {
                                key: "Authorization",
                                secret: true,
                                value: tok.toLowerCase().startsWith("bearer ")
                                  ? tok
                                  : `Bearer ${tok}`,
                              },
                            ]
                          : null,
                        description: entry.description,
                        connector_type: "recommended",
                      });
                      await loadConnectors();
                      if (res.needs_auth && res.authorization_url) {
                        window.open(res.authorization_url, "_blank");
                        toast.info(t("settings.connectors.completeInBrowser"));
                        setOauthPending(res.slug);
                      } else {
                        toast.success(
                          t("settings.connectors.httpConnectorAdded"),
                        );
                        pollConnectorStatus(res.id);
                      }
                    } catch (err) {
                      toast.error(
                        err instanceof Error
                          ? err.message
                          : t("settings.connectors.addFailed"),
                      );
                    }
                  })();
                } else {
                  // OAuth with user-supplied credentials (e.g. GitHub client_id/client_secret).
                  void (async () => {
                    try {
                      const res = await connectorsApi.create({
                        slug: entry.slug,
                        display_name: entry.display_name,
                        transport: entry.transport ?? "http",
                        url: entry.url ?? "",
                        auth_type: "oauth",
                        description: entry.description,
                        connector_type: "recommended",
                        credentials:
                          Object.keys(creds).length > 0 ? creds : undefined,
                      });
                      await loadConnectors();
                      if (res.needs_auth && res.authorization_url) {
                        window.open(res.authorization_url, "_blank");
                        toast.info(t("settings.connectors.completeInBrowser"));
                        setOauthPending(res.slug);
                      }
                    } catch (err) {
                      toast.error(
                        err instanceof Error
                          ? err.message
                          : t("settings.connectors.addFailed"),
                      );
                    }
                  })();
                }
              }}
            >
              {t("settings.connectors.startAuth")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Catalog Fields Dialog -- non-OAuth catalog connectors that declare
          `fields`. Submitted entries use field.name (the ACTUAL header /
          param name) as `key`; field.secret is authoritative server-side. */}
      <AlertDialog
        open={catalogFieldsEntry !== null}
        onOpenChange={(open) => {
          if (!open) setCatalogFieldsEntry(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("settings.connectors.catalogFieldsTitle", {
                name: catalogFieldsEntry?.display_name ?? "",
              })}
            </AlertDialogTitle>
            {catalogFieldsEntry?.credentials_help_url && (
              <AlertDialogDescription asChild>
                <a
                  href={catalogFieldsEntry.credentials_help_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-0.5 text-brand underline underline-offset-2"
                >
                  {t("settings.connectors.goToGet")}
                  <ArrowUpRight className="h-3 w-3" />
                </a>
              </AlertDialogDescription>
            )}
          </AlertDialogHeader>
          <div className="space-y-3 py-2">
            {(catalogFieldsEntry?.fields ?? []).map((field) => {
              const rawLabel = field.label;
              const label =
                typeof rawLabel === "string"
                  ? rawLabel
                  : rawLabel
                    ? (rawLabel[locale] ??
                      rawLabel["en-US"] ??
                      Object.values(rawLabel)[0] ??
                      field.key)
                    : field.key;
              return (
                <div key={field.key}>
                  <div className="mb-1 text-xs text-ink-meta">
                    {label}
                    {field.required && (
                      <span className="ml-0.5 text-error-text">*</span>
                    )}
                  </div>
                  <input
                    className="w-full rounded-lg border border-surface-border bg-background px-3 py-1.5 text-sm text-ink-heading font-mono outline-none focus:ring-1 focus:ring-brand"
                    placeholder={field.placeholder ?? ""}
                    type={field.secret ? "password" : "text"}
                    value={catalogFieldsForm[field.key] ?? ""}
                    onChange={(e) =>
                      setCatalogFieldsForm((prev) => ({
                        ...prev,
                        [field.key]: e.target.value,
                      }))
                    }
                  />
                  {field.target === "param" && field.secret && (
                    <div className="mt-1 text-xs text-error-text">
                      {t(
                        "settings.connectors.secretParamWarning" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setCatalogFieldsEntry(null)}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (!catalogFieldsEntry) return;
                const entry = catalogFieldsEntry;
                const requiredMissing = entry.fields.some(
                  (f) => f.required && !catalogFieldsForm[f.key]?.trim(),
                );
                if (requiredMissing) {
                  toast.error(t("settings.connectors.allRequired"));
                  return;
                }
                setCatalogFieldsEntry(null);
                const headerEntries: {
                  key: string;
                  secret: boolean;
                  value: string;
                }[] = [];
                const paramEntries: {
                  key: string;
                  secret: boolean;
                  value: string;
                }[] = [];
                for (const f of entry.fields) {
                  const value = catalogFieldsForm[f.key] ?? "";
                  if (!value.trim()) continue;
                  const item = { key: f.name, secret: f.secret, value };
                  if (f.target === "param") paramEntries.push(item);
                  else headerEntries.push(item);
                }
                void (async () => {
                  try {
                    const res = await connectorsApi.create({
                      slug: entry.slug,
                      display_name: entry.display_name,
                      transport: entry.transport ?? "http",
                      url: entry.url ?? "",
                      auth_type: entry.auth_type,
                      description: entry.description,
                      connector_type: "recommended",
                      headers: headerEntries.length > 0 ? headerEntries : null,
                      params: paramEntries.length > 0 ? paramEntries : null,
                    });
                    await loadConnectors();
                    if (res.needs_auth && res.authorization_url) {
                      window.open(res.authorization_url, "_blank");
                      toast.info(t("settings.connectors.completeInBrowser"));
                      setOauthPending(res.slug);
                    } else {
                      toast.success(
                        t("settings.connectors.httpConnectorAdded"),
                      );
                      pollConnectorStatus(res.id);
                    }
                  } catch (err) {
                    toast.error(
                      err instanceof Error
                        ? err.message
                        : t("settings.connectors.addFailed"),
                    );
                  }
                })();
              }}
            >
              {t("settings.connectors.installAuth")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
};
