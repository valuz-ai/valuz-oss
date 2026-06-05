import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Link2, Plus, Search, Trash2 } from "lucide-react";
import {
  CategorizedList,
  ConnectorDetailPanel,
  ConnectorListItem,
  DeleteConfirmDialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  PageLoader,
} from "@valuz/ui";
import { ResourceActionSlot } from "../components/ResourceActionSlot";
import {
  connectorsApi,
  usePanelStore,
  useResourceCategories,
  useTranslation,
  type CatalogConnector,
  type CatalogEntry,
  type ConnectorItem,
  type CreateConnectorRequest,
  type ToolInfo,
} from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
import type { ResourceCategory } from "@valuz/shared";
import { useWorkspaceOutlet } from "@valuz/app/layout";
import {
  ConnectorAddDialog,
  ConnectorConnectDialog,
} from "@valuz/app/components";
import type { ConnectorAddMode } from "@valuz/app/components";

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
  return [
    {
      id: "builtin",
      label: t("connector.groupOfficial" as Parameters<typeof t>[0]),
      order: 0,
      filter: (e: ConnectorListEntry) =>
        e.kind === "installed" && e.item.connector_type === "builtin",
    },
    {
      id: "custom",
      label: t("connector.groupCustom" as Parameters<typeof t>[0]),
      order: 1,
      filter: (e: ConnectorListEntry) =>
        e.kind === "installed" && e.item.connector_type === "custom",
    },
    {
      id: "connected",
      label: t("connector.groupConnected" as Parameters<typeof t>[0]),
      order: 2,
      filter: (e: ConnectorListEntry) =>
        e.kind === "installed" && e.item.connector_type === "recommended",
    },
    {
      id: "available",
      label: t("connector.groupAvailable" as Parameters<typeof t>[0]),
      order: 3,
      filter: (e: ConnectorListEntry) => e.kind === "available",
      defaultCollapsed: false,
    },
  ];
}

/* ── Page ────────────────────────────────────────────────────── */

export const ConnectorsPage = () => {
  const { t } = useTranslation();
  const {
    setHeader,
    setHideHeader,
    setRightPanel,
    setAsideClassName,
    setMainClassName,
  } = useWorkspaceOutlet();
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);

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

  // Connected-view tool probe result, keyed by connector id so the value
  // can be derived during render (tools for any *other* id read as
  // "loading"). Keying avoids a synchronous reset-setState in the probe
  // effect — the effect only writes inside its async callbacks.
  const [toolsState, setToolsState] = useState<{
    id: string;
    tools: ToolInfo[];
    error: string | null;
  } | null>(null);

  // In-flight connect/disconnect target (drives button spinners).
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ConnectorItem | null>(null);

  const canDeleteConnector = (c: ConnectorItem) =>
    c.connector_type !== "builtin";

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
        (e) => !assigned.has(entryKey(e)) && cat.filter(e),
      );
      if (matching.length > 0) return matching[0];
      for (const e of unifiedList) {
        if (cat.filter(e)) assigned.add(entryKey(e));
      }
    }
    return unifiedList[0] ?? null;
  }, [unifiedList, categories]);

  const effectiveKey =
    activeKey && unifiedList.some((e) => entryKey(e) === activeKey)
      ? activeKey
      : firstEntry
        ? entryKey(firstEntry)
        : null;

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

  useEffect(() => {
    if (!activeInstalledId || !activeIsConnected) return;
    let cancelled = false;
    connectorsApi
      .test(activeInstalledId)
      .then((res) => {
        if (cancelled) return;
        setToolsState({
          id: activeInstalledId,
          tools: res.ok ? (res.tool_details ?? []) : [],
          error: res.ok
            ? null
            : (res.error ?? _t("settings.connectors.testFailed")),
        });
      })
      .catch((err) => {
        if (cancelled) return;
        setToolsState({
          id: activeInstalledId,
          tools: [],
          error: err instanceof Error ? err.message : "unknown",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [activeInstalledId, activeIsConnected]);

  // Derive the active connector's tools during render: a probe result only
  // counts when it matches the current connector id; otherwise it reads as
  // ``undefined`` (loading) — no reset-setState needed on connector switch.
  const activeTools: ToolInfo[] | undefined =
    activeIsConnected && toolsState && toolsState.id === activeInstalledId
      ? toolsState.tools
      : undefined;
  const activeToolsError: string | null =
    activeIsConnected && toolsState && toolsState.id === activeInstalledId
      ? toolsState.error
      : null;

  /* ── Connect / disconnect ────────────────────────────────────── */

  const pollStatus = useCallback(
    (connectorId: string, timeoutMs = 30_000) => {
      if (pollRef.current) clearTimeout(pollRef.current);
      const deadline = Date.now() + timeoutMs;
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
        pollRef.current = setTimeout(() => void poll(), 2000);
      };
      pollRef.current = setTimeout(() => void poll(), 2000);
    },
    [loadAll],
  );

  // Page-owned create + poll, shared by direct connect, the credentials
  // dialog, and the custom add dialog.
  const runConnect = useCallback(
    async (payload: CreateConnectorRequest) => {
      const res = await connectorsApi.create(payload);
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

  // Re-probe an already-added connector that isn't currently connected.
  const handleReconnectInstalled = useCallback(
    (connector: ConnectorItem) => {
      setBusyKey(`installed:${connector.id}`);
      void (async () => {
        try {
          const res = await connectorsApi.test(connector.id);
          if (!res.ok) {
            toast.error(res.error || _t("settings.connectors.connectFailed"));
          }
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
    [loadAll],
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
          onDisconnect={() => canDeleteConnector(c) && setDeleteTarget(c)}
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
      <header className="flex h-12 shrink-0 items-center gap-2 px-5">
        <span className="shrink-0 whitespace-nowrap text-base font-semibold text-ink-heading">
          {t("connector.title")}
        </span>
        <div className="flex min-w-0 flex-1 items-center justify-end gap-1">
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
              onSelect={(e: ConnectorListEntry) => setActiveKey(entryKey(e))}
              renderItem={(entry: ConnectorListEntry, isSelected: boolean) => {
                if (entry.kind === "installed") {
                  const c = entry.item;
                  return (
                    <ConnectorListItem
                      name={c.display_name}
                      iconUrl={entry.iconUrl}
                      badge={
                        c.status === "connected"
                          ? t(
                              "connector.statusConnected" as Parameters<
                                typeof t
                              >[0],
                            )
                          : c.status === "connecting"
                            ? t("connector.statusConnecting")
                            : c.status === "error"
                              ? t("connector.statusError")
                              : null
                      }
                      active={isSelected}
                      onClick={() => setActiveKey(`installed:${c.id}`)}
                      actions={
                        <>
                          {c.connector_type !== "builtin" &&
                            c.status !== "connected" && (
                              <button
                                type="button"
                                className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-ink-meta transition-colors hover:bg-[#f54b4b]/10 hover:text-[#f54b4b]"
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
                return (
                  <ConnectorListItem
                    name={cf.connector.display_name}
                    iconUrl={cf.iconUrl}
                    active={isSelected}
                    onClick={() => setActiveKey(`catalog:${cf.connector.slug}`)}
                    actions={
                      <ResourceActionSlot
                        resourceType="connector"
                        resource={
                          cf.connector as unknown as Record<string, unknown>
                        }
                      />
                    }
                  />
                );
              }}
              emptyState={
                <div className="flex flex-col items-center justify-center py-16 text-center">
                  <Link2 className="mb-3 h-10 w-10 text-ink-muted" />
                  <div className="text-sm text-ink-body">
                    {connectors.length === 0 && catalog.length === 0
                      ? t("connector.empty")
                      : t("connector.noMatch")}
                  </div>
                </div>
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
