import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Link2, Plus, Search, Store, Trash2 } from "lucide-react";
import {
  CategorizedList,
  Button,
  ConnectorDetailPanel,
  ConnectorListItem,
  DeleteConfirmDialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  EmptyState,
  PageLoader,
} from "@valuz/ui";
import {
  ResourceActionSlot,
  ResourceDetailActionSlot,
} from "../components/ResourceActionSlot";
import {
  acknowledgeConnectorAlert,
  connectorsApi,
  invalidateConnectorTools,
  useConnectorTools,
  usePanelStore,
  useResourceCategories,
  useTranslation,
  type CatalogConnector,
  type CatalogEntry,
  type ConnectorItem,
  type CreateConnectorRequest,
  type UpdateConnectorRequest,
} from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
import type { ResourceCategory } from "@valuz/shared";
import { useProjectOutlet } from "@valuz/app/layout";
import {
  ConnectorAddDialog,
  ConnectorConnectDialog,
} from "@valuz/app/components";
import type { ConnectorAddMode } from "@valuz/app/components";
import { reauthorizePayload, shouldReauthorize } from "./connector-reconnect";
import { isCloudOnlyResource } from "./agent-list-state";
import { usePluginMemberships } from "../components/plugins/use-plugin-memberships";

/* ── Status labels ──────────────────────────────────────────────── */

// Connector status → i18n key for the colored list-row pill. The two
// "configured but not connected" states (pending_auth / unknown) read as
// "未连接"; "disabled" stays unlabeled (the user turned it off on purpose).
const STATUS_LABEL_KEY: Record<string, Parameters<typeof _t>[0]> = {
  connected: "connector.statusConnected",
  connecting: "connector.statusConnecting",
  error: "connector.statusError",
  pending_auth: "connector.statusNotConnected",
  unknown: "connector.statusNotConnected",
};

/* ── Catalog flattening ─────────────────────────────────────────── */

interface CatalogFlat {
  connector: CatalogConnector;
  iconUrl: string | null;
}

// CatalogItem (standalone) carries the same connector fields as the
// nested CatalogConnector — strip the wrapper fields so both flavours
// share one shape downstream.
function flattenCatalog(items: CatalogEntry[]): CatalogFlat[] {
  const out: CatalogFlat[] = [];
  for (const item of items) {
    if (item.kind === "group") {
      for (const c of item.connectors)
        out.push({ connector: c, iconUrl: item.icon_url });
    } else {
      out.push({
        iconUrl: item.icon_url,
        connector: {
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
        },
      });
    }
  }
  return out;
}

const catalogNeedsCredentials = (c: CatalogConnector): boolean =>
  c.oauth_credentials_schema.length > 0 ||
  c.header_schema.length > 0 ||
  c.param_schema.length > 0;

/* ── Unified list entry (discriminated union) ────────────────── */

interface InstalledEntry {
  kind: "installed";
  item: ConnectorItem;
  iconUrl: string | null;
}

interface AvailableEntry {
  kind: "available";
  item: CatalogFlat;
}

type ConnectorListEntry = InstalledEntry | AvailableEntry;

const entryKey = (e: ConnectorListEntry): string =>
  e.kind === "installed"
    ? `installed:${e.item.id}`
    : `catalog:${e.item.connector.slug}`;

/* ── Category builder ────────────────────────────────────────── */

function buildConnectorCategories(
  t: ReturnType<typeof useTranslation>["t"],
): ResourceCategory<ConnectorListEntry>[] {
  // Two buckets only: everything the user has added (any connector_type) vs
  // catalog entries not yet installed. Live connection state is shown per-row
  // by the status pill, so the left grouping stays about install state — no
  // more type-based "已连接" group that ignored real status.
  return [
    {
      id: "installed",
      label: t("connector.groupInstalled" as Parameters<typeof t>[0]),
      order: 0,
      filter: (e: ConnectorListEntry) => e.kind === "installed",
    },
    {
      id: "available",
      label: t("connector.groupAvailable" as Parameters<typeof t>[0]),
      order: 1,
      filter: (e: ConnectorListEntry) => e.kind === "available",
      defaultCollapsed: false,
    },
  ];
}

/* ── Page ────────────────────────────────────────────────────── */

export const ConnectorsPage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const {
    setHeader,
    setHideHeader,
    setRightPanel,
    setAsideClassName,
    setMainClassName,
  } = useProjectOutlet();
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);
  const [searchParams] = useSearchParams();
  // ``?connector=<slug>`` deep link (e.g. from a plugin's member list).
  const connectorParam = searchParams.get("connector");

  const [connectors, setConnectors] = useState<ConnectorItem[]>([]);
  const [catalog, setCatalog] = useState<CatalogFlat[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);

  // Add (custom HTTP / Stdio) + connect-with-credentials dialogs.
  const [addMode, setAddMode] = useState<ConnectorAddMode | null>(null);
  const [connectEntry, setConnectEntry] = useState<CatalogConnector | null>(
    null,
  );

  // In-flight connect/disconnect target (drives button spinners).
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ConnectorItem | null>(null);
  const [editTarget, setEditTarget] = useState<ConnectorItem | null>(null);

  const canDeleteConnector = (c: ConnectorItem) =>
    c.connector_type !== "builtin";

  /** Only a connector the user configured themselves. A built-in is
   *  system-managed, and for a ``recommended`` one the catalog entry owns
   *  command/args/identity — PATCHing those returns 422, so offering the
   *  form would be offering a button that fails. */
  const canEditConnector = (c: ConnectorItem) => c.connector_type === "custom";

  const mountedRef = useRef(true);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /* ── Data loading ────────────────────────────────────────────── */

  const loadAll = useCallback(async () => {
    try {
      const [listRes, dirRes] = await Promise.all([
        connectorsApi.list(),
        connectorsApi.listDirectory(),
      ]);
      if (!mountedRef.current) return;
      setConnectors(listRes.connectors);
      setCatalog(flattenCatalog(dirRes.items));
      // Viewing the page clears the connector nav dot for whatever is failing
      // right now (acknowledge against this freshly-loaded list).
      acknowledgeConnectorAlert(listRes.connectors);
    } catch (err) {
      if (mountedRef.current) {
        console.error("[Connectors] load error", err);
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void (async () => {
      await loadAll();
    })();
    return () => {
      mountedRef.current = false;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [loadAll]);

  // Mirror SkillsPage: this page's payload is the right panel, so default
  // it expanded + invert the main/aside proportions (narrow list, wide
  // detail). Restored on unmount so other routes keep their defaults.
  useEffect(() => {
    setHideHeader(true);
    setMainClassName("w-[345px] flex-none");
    setAsideClassName("flex-1 w-auto");
    return () => {
      setHideHeader(false);
      setHeader(null);
      setMainClassName(undefined);
      setAsideClassName(undefined);
    };
  }, [setHideHeader, setHeader, setMainClassName, setAsideClassName]);

  const didInitRightPanel = useRef(false);
  useEffect(() => {
    if (didInitRightPanel.current) return;
    didInitRightPanel.current = true;
    panelSetCollapsed(false);
  }, [panelSetCollapsed]);

  /* ── OAuth callback fast-path (callback page posts a message) ──── */
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.data?.type === "connector_oauth_success") {
        if (pollRef.current) clearTimeout(pollRef.current);
        void loadAll();
      } else if (e.data?.type === "connector_oauth_error") {
        if (pollRef.current) clearTimeout(pollRef.current);
        void loadAll();
        toast.error(
          typeof e.data.error === "string"
            ? e.data.error
            : _t("settings.connectors.authFailed"),
        );
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [loadAll]);

  /* ── Derived data ────────────────────────────────────────────── */

  const iconBySlug = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const c of catalog) m.set(c.connector.slug, c.iconUrl);
    return m;
  }, [catalog]);

  const matches = useCallback(
    (text: string) => text.toLowerCase().includes(searchQuery.toLowerCase()),
    [searchQuery],
  );

  // Build the unified list: installed connectors + uninstalled catalog entries.
  const unifiedList = useMemo<ConnectorListEntry[]>(() => {
    const installedSlugs = new Set(connectors.map((c) => c.slug));

    const installed: ConnectorListEntry[] = connectors
      .filter((c) => matches(c.display_name) || matches(c.description ?? ""))
      .map((c) => ({
        kind: "installed" as const,
        item: c,
        iconUrl: iconBySlug.get(c.slug) ?? null,
      }));

    const available: ConnectorListEntry[] = catalog
      .filter(
        (c) => !c.connector.installed && !installedSlugs.has(c.connector.slug),
      )
      .filter(
        (c) =>
          matches(c.connector.display_name) ||
          matches(c.connector.description ?? ""),
      )
      .map((c) => ({ kind: "available" as const, item: c }));

    return [...installed, ...available];
  }, [connectors, catalog, iconBySlug, matches]);

  // Categories for CategorizedList.
  const categories = useResourceCategories<ConnectorListEntry>(
    "connector",
    buildConnectorCategories(t),
  );

  // Default selection: first item across all categories in display order.
  const firstEntry = useMemo(() => {
    const assigned = new Set<string>();
    for (const cat of categories) {
      const matching = unifiedList.filter(
        (e) =>
          !isCloudOnlyResource(e) &&
          !assigned.has(entryKey(e)) &&
          cat.filter(e),
      );
      if (matching.length > 0) return matching[0];
      for (const e of unifiedList) {
        if (cat.filter(e)) assigned.add(entryKey(e));
      }
    }
    return unifiedList.find((entry) => !isCloudOnlyResource(entry)) ?? null;
  }, [unifiedList, categories]);

  const deepLinkKey = useMemo(() => {
    if (!connectorParam) return null;
    const match = connectors.find((c) => c.slug === connectorParam);
    return match ? `installed:${match.id}` : null;
  }, [connectorParam, connectors]);

  const effectiveKey =
    activeKey &&
    unifiedList.some(
      (e) => entryKey(e) === activeKey && !isCloudOnlyResource(e),
    )
      ? activeKey
      : (deepLinkKey ?? (firstEntry ? entryKey(firstEntry) : null));

  // Plugin ownership badges (D6): one batched lookup per list load.
  const connectorSlugs = useMemo(
    () => connectors.map((c) => c.slug),
    [connectors],
  );
  const pluginBadgeFor = usePluginMemberships("connector", connectorSlugs);

  const selectedEntry = useMemo(
    () => unifiedList.find((e) => entryKey(e) === effectiveKey) ?? null,
    [unifiedList, effectiveKey],
  );

  const selectedInstalled =
    selectedEntry?.kind === "installed" ? selectedEntry.item : null;
  const selectedCatalog =
    selectedEntry?.kind === "available" ? selectedEntry.item : null;

  /* ── Tools probe for the active connected connector ──────────── */
  const activeInstalled = selectedInstalled || null;
  const activeIsConnected =
    !!activeInstalled && activeInstalled.status === "connected";
  const activeInstalledId = activeInstalled?.id ?? null;

  // Probed once per client session and cached at module level, so re-selecting
  // a connector — or leaving the page and coming back — never reconnects again.
  const { tools: activeTools, error: activeToolsError } = useConnectorTools(
    activeInstalledId,
    activeIsConnected,
  );

  /* ── Connect / disconnect ────────────────────────────────────── */

  const pollStatus = useCallback(
    (connectorId: string, timeoutMs = 30_000) => {
      if (pollRef.current) clearTimeout(pollRef.current);
      const deadline = Date.now() + timeoutMs;
      // A connect rarely settles in <1s, and the OAuth flow already has a
      // postMessage fast-path, so this fallback polls calmly (5s) rather than
      // hammering once a status switch is imminent.
      const intervalMs = 5_000;
      const poll = async () => {
        if (Date.now() > deadline) {
          toast.error(_t("settings.connectors.connectTimeout"));
          await loadAll();
          return;
        }
        try {
          const c = await connectorsApi.get(connectorId);
          if (c.status === "connected") {
            toast.success(
              c.tool_count != null
                ? _t("settings.connectors.connectSuccessTools", {
                    count: c.tool_count,
                  })
                : _t("settings.connectors.connectSuccess"),
            );
            await loadAll();
            setActiveKey(`installed:${connectorId}`);
            return;
          }
          if (c.status === "error") {
            toast.error(
              c.error_message || _t("settings.connectors.connectFailed"),
            );
            await loadAll();
            return;
          }
        } catch {
          // transient — keep polling
        }
        pollRef.current = setTimeout(() => void poll(), intervalMs);
      };
      pollRef.current = setTimeout(() => void poll(), intervalMs);
    },
    [loadAll],
  );

  // Marketplace installs navigate here while the backend is still probing the
  // MCP process. Resume that probe on arrival so a stale `connecting` snapshot
  // cannot remain on screen after the backend has already settled.
  useEffect(() => {
    const connecting = connectors.find((c) => c.status === "connecting");
    if (!connecting) return;
    pollStatus(connecting.id);
    return () => {
      if (pollRef.current) {
        clearTimeout(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [connectors, pollStatus]);

  // Page-owned create + poll, shared by direct connect, the credentials
  // dialog, and the custom add dialog.
  const runConnect = useCallback(
    async (payload: CreateConnectorRequest) => {
      const res = await connectorsApi.create(payload);
      invalidateConnectorTools(res.id); // fresh connection → re-probe its tools
      await loadAll();
      if (res.needs_auth && res.authorization_url) {
        window.open(res.authorization_url, "_blank");
        toast.info(_t("settings.connectors.completeInBrowser"));
        pollStatus(res.id, 300_000);
      } else {
        pollStatus(res.id);
      }
      setActiveKey(`installed:${res.id}`);
    },
    [loadAll, pollStatus],
  );

  const runUpdate = useCallback(
    async (id: string, payload: UpdateConnectorRequest) => {
      await connectorsApi.update(id, payload);
      // The command line, env, or headers may all have changed, so whatever
      // tools we probed for the old configuration no longer describe it.
      invalidateConnectorTools(id);
      await loadAll();
      toast.success(_t("connector.updated"));
      pollStatus(id);
    },
    [loadAll, pollStatus],
  );

  const handleConnectCatalog = useCallback(
    (c: CatalogConnector) => {
      // Catalog entries that declare credential/config fields collect them
      // first; field-less ones connect directly (mirrors Settings).
      if (catalogNeedsCredentials(c)) {
        setConnectEntry(c);
        return;
      }
      const payload: CreateConnectorRequest = {
        slug: c.slug,
        display_name: c.display_name,
        transport: c.transport ?? "http",
        url: c.url ?? "",
        auth_type: c.auth_type,
        description: c.description,
        connector_type: "recommended",
        command: c.command ?? undefined,
        args: c.args ?? undefined,
        working_dir: c.working_dir ?? undefined,
        env: c.env ?? undefined,
      };
      setBusyKey(`catalog:${c.slug}`);
      void (async () => {
        try {
          await runConnect(payload);
        } catch (err) {
          toast.error(
            err instanceof Error
              ? err.message
              : _t("settings.connectors.addFailed"),
          );
        } finally {
          if (mountedRef.current) setBusyKey(null);
        }
      })();
    },
    [runConnect],
  );

  // Reconnect an already-added connector that isn't currently connected.
  // Test first — the probe now self-heals an expired OAuth token server-side
  // Disconnecting a built-in means switching it off: the backend refuses to
  // delete one, and the credential is what the owner wants gone anyway. Giving
  // them no disconnect at all was the trap — a built-in whose grant had died
  // still displayed "connected", the one state that hides the Connect button.
  const handleDisableBuiltin = useCallback(
    (connector: ConnectorItem) => {
      setBusyKey(`installed:${connector.id}`);
      invalidateConnectorTools(connector.id);
      void (async () => {
        try {
          await connectorsApi.disable(connector.id);
          await loadAll();
        } catch (err) {
          toast.error(
            err instanceof Error
              ? err.message
              : _t("settings.connectors.operationFailed"),
          );
        } finally {
          setBusyKey(null);
        }
      })();
    },
    [loadAll],
  );

  // (refresh + retry). Only a still-failing OAuth connector escalates to full
  // re-authorization (browser re-consent); see ``connector-reconnect``.
  const handleReconnectInstalled = useCallback(
    (connector: ConnectorItem) => {
      setBusyKey(`installed:${connector.id}`);
      // Tools may have changed on a fresh connect — drop the cached probe.
      invalidateConnectorTools(connector.id);
      void (async () => {
        try {
          const res = await connectorsApi.test(connector.id);
          if (res.ok) {
            await loadAll();
            return;
          }
          if (shouldReauthorize(connector)) {
            await runConnect(reauthorizePayload(connector));
            return;
          }
          toast.error(res.error || _t("settings.connectors.connectFailed"));
          await loadAll();
        } catch (err) {
          toast.error(
            err instanceof Error
              ? err.message
              : _t("settings.connectors.testFailed"),
          );
        } finally {
          if (mountedRef.current) setBusyKey(null);
        }
      })();
    },
    [loadAll, runConnect],
  );

  const handleDisconnect = useCallback(async () => {
    if (!deleteTarget) return;
    const id = deleteTarget.id;
    setBusyKey(`installed:${id}`);
    try {
      await connectorsApi.delete(id);
      toast.success(_t("settings.connectors.connectorDeleted"));
      setDeleteTarget(null);
      setActiveKey(null);
      await loadAll();
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : _t("settings.connectors.deleteFailed"),
      );
    } finally {
      if (mountedRef.current) setBusyKey(null);
    }
  }, [deleteTarget, loadAll]);

  /* ── Right panel ─────────────────────────────────────────────── */

  useEffect(() => {
    if (selectedInstalled) {
      const c = selectedInstalled;
      const connected = c.status === "connected";
      setRightPanel(
        <ConnectorDetailPanel
          name={c.display_name}
          iconUrl={iconBySlug.get(c.slug) ?? null}
          description={c.description}
          connected={connected}
          errorMessage={c.status === "error" ? c.error_message : null}
          tools={activeTools}
          toolsError={activeToolsError}
          busy={busyKey === `installed:${c.id}`}
          onConnect={() => handleReconnectInstalled(c)}
          systemManaged={!canDeleteConnector(c)}
          onDisconnect={() =>
            canDeleteConnector(c) ? setDeleteTarget(c) : handleDisableBuiltin(c)
          }
          onEdit={canEditConnector(c) ? () => setEditTarget(c) : undefined}
          headerActions={
            <ResourceDetailActionSlot
              resourceType="connector"
              resource={c as unknown as Record<string, unknown>}
            />
          }
        />,
      );
    } else if (selectedCatalog) {
      const c = selectedCatalog.connector;
      setRightPanel(
        <ConnectorDetailPanel
          name={c.display_name}
          iconUrl={selectedCatalog.iconUrl}
          description={c.description}
          connected={false}
          busy={busyKey === `catalog:${c.slug}`}
          onConnect={() => handleConnectCatalog(c)}
        />,
      );
    } else {
      setRightPanel(null);
    }
    return () => setRightPanel(null);
  }, [
    selectedInstalled,
    selectedCatalog,
    activeTools,
    activeToolsError,
    busyKey,
    iconBySlug,
    setRightPanel,
    handleConnectCatalog,
    handleReconnectInstalled,
  ]);

  /* ── Render ──────────────────────────────────────────────────── */

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between gap-4 h-15 px-5">
        <div className="flex min-w-0 flex-col justify-center">
          <span className="text-base font-semibold leading-5 text-ink-heading">
            {t("connector.title")}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-brand transition-colors hover:bg-brand-light/60 hover:text-brand"
            onClick={() =>
              navigate("/marketplace?tab=connectors&from=connectors")
            }
          >
            <Store className="h-3.5 w-3.5" />
            {t("marketplace.title" as Parameters<typeof t>[0])}
          </button>
          {searchOpen ? (
            <input
              type="text"
              autoFocus
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onBlur={() => {
                if (!searchQuery) setSearchOpen(false);
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setSearchQuery("");
                  setSearchOpen(false);
                }
              }}
              placeholder={t("connector.searchPlaceholder")}
              className="h-7 w-full min-w-0 max-w-[200px] rounded-none border-0 border-b border-brand bg-transparent px-1 text-xs text-ink-heading placeholder:text-ink-meta outline-none"
            />
          ) : null}
          <button
            type="button"
            aria-label={t("common.search")}
            onClick={() => setSearchOpen((o) => !o)}
            className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
          >
            <Search className="h-3.5 w-3.5" />
          </button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={t("connector.addMenuTitle")}
                className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-[180px]">
              <DropdownMenuItem onSelect={() => setAddMode("http")}>
                <Link2 className="h-4 w-4" />
                {t("connector.addHttp")}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setAddMode("stdio")}>
                <Plus className="h-4 w-4" />
                {t("connector.addStdio")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {loading ? (
        <PageLoader logo />
      ) : (
        <div className="flex-1 overflow-y-auto py-4">
          <div className="mb-4 px-4">
            <CategorizedList
              items={unifiedList}
              categories={categories}
              selectedId={effectiveKey}
              getId={entryKey}
              onSelect={(e: ConnectorListEntry) => {
                if (!isCloudOnlyResource(e)) setActiveKey(entryKey(e));
              }}
              renderItem={(entry: ConnectorListEntry, isSelected: boolean) => {
                if (entry.kind === "installed") {
                  const c = entry.item;
                  const cloudOnly = isCloudOnlyResource(entry);
                  return (
                    <ConnectorListItem
                      name={c.display_name}
                      iconUrl={entry.iconUrl}
                      pluginBadge={pluginBadgeFor(c.slug)}
                      status={c.status}
                      statusLabel={
                        STATUS_LABEL_KEY[c.status]
                          ? t(STATUS_LABEL_KEY[c.status])
                          : null
                      }
                      active={!cloudOnly && isSelected}
                      onClick={() => {
                        if (!cloudOnly) setActiveKey(`installed:${c.id}`);
                      }}
                      actions={
                        <>
                          {!cloudOnly &&
                            c.connector_type !== "builtin" &&
                            c.status !== "connected" && (
                              <button
                                type="button"
                                className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-ink-meta transition-colors hover:bg-error-light hover:text-error-text"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setDeleteTarget(c);
                                }}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            )}
                          <ResourceActionSlot
                            resourceType="connector"
                            resource={c as unknown as Record<string, unknown>}
                          />
                        </>
                      }
                    />
                  );
                }
                // available (uninstalled catalog entry)
                const cf = entry.item;
                const catalogKey = `catalog:${cf.connector.slug}`;
                return (
                  <ConnectorListItem
                    name={cf.connector.display_name}
                    iconUrl={cf.iconUrl}
                    active={isSelected}
                    onClick={() => setActiveKey(catalogKey)}
                    actions={
                      <div className="flex shrink-0 items-center gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="xs"
                          loading={busyKey === catalogKey}
                          className="text-brand hover:bg-brand-light/60 hover:text-brand"
                          onClick={(event) => {
                            event.stopPropagation();
                            handleConnectCatalog(cf.connector);
                          }}
                        >
                          {t("connector.connect")}
                        </Button>
                        <ResourceActionSlot
                          resourceType="connector"
                          resource={
                            cf.connector as unknown as Record<string, unknown>
                          }
                        />
                      </div>
                    }
                  />
                );
              }}
              emptyState={
                <EmptyState
                  className="py-16"
                  icon={
                    connectors.length === 0 && catalog.length === 0 ? (
                      <Link2 />
                    ) : (
                      <Search />
                    )
                  }
                  title={
                    connectors.length === 0 && catalog.length === 0
                      ? t("connector.emptyTitle")
                      : t("connector.noMatch")
                  }
                  message={
                    connectors.length === 0 && catalog.length === 0
                      ? t("connector.emptyDesc")
                      : undefined
                  }
                  action={
                    connectors.length === 0 && catalog.length === 0 ? (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="default" size="sm">
                            <Plus className="h-3 w-3" />
                            {t("connector.emptyAction")}
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent
                          align="center"
                          className="min-w-[180px]"
                        >
                          <DropdownMenuItem onSelect={() => setAddMode("http")}>
                            <Link2 className="h-4 w-4" />
                            {t("connector.addHttp")}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onSelect={() => setAddMode("stdio")}
                          >
                            <Plus className="h-4 w-4" />
                            {t("connector.addStdio")}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    ) : undefined
                  }
                />
              }
            />
          </div>
        </div>
      )}

      <ConnectorAddDialog
        open={addMode !== null}
        mode={addMode ?? "http"}
        onOpenChange={(open) => {
          if (!open) setAddMode(null);
        }}
        onSubmit={runConnect}
      />

      {editTarget && (
        // Keyed by id: the dialog seeds its form in useState initializers, so
        // a reused instance would show the previously edited connector.
        <ConnectorAddDialog
          key={`edit:${editTarget.id}`}
          open
          mode={editTarget.transport === "stdio" ? "stdio" : "http"}
          onOpenChange={(open) => {
            if (!open) setEditTarget(null);
          }}
          onSubmit={runConnect}
          initial={editTarget}
          onUpdate={(payload) => runUpdate(editTarget.id, payload)}
        />
      )}

      <ConnectorConnectDialog
        key={connectEntry?.slug ?? "none"}
        entry={connectEntry}
        onClose={() => setConnectEntry(null)}
        onSubmit={runConnect}
      />

      {deleteTarget && (
        <DeleteConfirmDialog
          open={deleteTarget !== null}
          onOpenChange={(open) => {
            if (!open) setDeleteTarget(null);
          }}
          title={
            deleteTarget
              ? t("connector.disconnectConfirm", {
                  name: deleteTarget.display_name,
                })
              : undefined
          }
          description={t("connector.disconnectConfirmDesc")}
          confirmLabel={t("connector.disconnect")}
          onConfirm={handleDisconnect}
        />
      )}
    </div>
  );
};
