import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Link2, Search, Trash2 } from "lucide-react";
import {
  Button as UiButton,
  CategorizedList,
  ConnectorDetailPanel,
  ConnectorListItem,
  DeleteConfirmDialog,
  EmptyState,
  PageLoader,
} from "@valuz/ui";
import {
  connectorsApi,
  invalidateConnectorTools,
  useConnectorTools,
  useResourceCategories,
  useTranslation,
  type CatalogConnector,
  type CatalogEntry,
  type ConnectorItem,
  type CreateConnectorRequest,
} from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
import type { ResourceCategory } from "@valuz/shared";
import {
  ConnectorAddDialog,
  ConnectorConnectDialog,
  type ConnectorAddMode,
} from "@valuz/app/components";
import { reauthorizePayload, shouldReauthorize } from "./connector-reconnect";

/** Add mode driven from the shared header dropdown (null = closed). */
export type ConnectorAddModeOrNull = ConnectorAddMode | null;

/** Raw connector status → localized status-pill i18n key. */
const STATUS_LABEL_KEY: Record<string, Parameters<typeof _t>[0]> = {
  connected: "connector.statusConnected",
  connecting: "connector.statusConnecting",
  error: "connector.statusError",
  pending_auth: "connector.statusNotConnected",
  unknown: "connector.statusNotConnected",
};

interface CatalogFlat {
  connector: CatalogConnector;
  iconUrl: string | null;
}

/** Flatten the mixed recommended catalog (groups + standalone) to a flat list. */
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

const canDeleteConnector = (c: ConnectorItem) => c.connector_type !== "builtin";

function buildConnectorCategories(
  t: ReturnType<typeof useTranslation>["t"],
): ResourceCategory<ConnectorListEntry>[] {
  return [
    {
      id: "installed",
      label: t("connector.groupInstalled" as Parameters<typeof t>[0]),
      order: 0,
      filter: (e) => e.kind === "installed",
    },
    {
      id: "available",
      label: t("connector.groupAvailable" as Parameters<typeof t>[0]),
      order: 1,
      filter: (e) => e.kind === "available",
      defaultCollapsed: false,
    },
  ];
}

/**
 * Connectors body for the unified resource page — a self-contained
 * list (left, grouped 已安装 / 可添加) + detail (right) fragment. Ports
 * ConnectorsPage's unified installed+catalog model, the test-first
 * reconnect / delete flow, and the custom-add / credentials dialogs
 * without the host-layout right-panel plumbing. The header add dropdown
 * drives the custom-add mode down through `addMode` / `onAddModeChange`.
 */
export function ConnectorsPane({
  query,
  addMode,
  onAddModeChange,
}: {
  query: string;
  addMode: ConnectorAddModeOrNull;
  onAddModeChange: (mode: ConnectorAddModeOrNull) => void;
}) {
  const { t } = useTranslation();
  const mountedRef = useRef(true);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, []);

  const [connectors, setConnectors] = useState<ConnectorItem[]>([]);
  const [catalog, setCatalog] = useState<CatalogFlat[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ConnectorItem | null>(null);
  const [connectEntry, setConnectEntry] = useState<CatalogConnector | null>(
    null,
  );

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
      if (mountedRef.current) console.error("[Connectors] load error", err);
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const iconBySlug = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const c of catalog) m.set(c.connector.slug, c.iconUrl);
    return m;
  }, [catalog]);

  const matches = useCallback(
    (text: string) => text.toLowerCase().includes(query.toLowerCase()),
    [query],
  );

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

  const categories = useResourceCategories<ConnectorListEntry>(
    "connector",
    buildConnectorCategories(t),
  );

  const firstEntry = useMemo(() => {
    const assigned = new Set<string>();
    for (const cat of categories) {
      const matching = unifiedList.filter(
        (e) => !assigned.has(entryKey(e)) && cat.filter(e),
      );
      if (matching.length > 0) return matching[0];
      for (const e of unifiedList) if (cat.filter(e)) assigned.add(entryKey(e));
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

  const isConnected =
    !!selectedInstalled && selectedInstalled.status === "connected";
  const { tools, error: toolsError } = useConnectorTools(
    selectedInstalled?.id ?? null,
    isConnected,
  );

  const pollStatus = useCallback(
    (connectorId: string, timeoutMs = 30_000) => {
      if (pollRef.current) clearTimeout(pollRef.current);
      const deadline = Date.now() + timeoutMs;
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
          /* transient — keep polling */
        }
        pollRef.current = setTimeout(() => void poll(), intervalMs);
      };
      pollRef.current = setTimeout(() => void poll(), intervalMs);
    },
    [loadAll],
  );

  const runConnect = useCallback(
    async (payload: CreateConnectorRequest) => {
      const res = await connectorsApi.create(payload);
      invalidateConnectorTools(res.id);
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
      // Credentialed entries collect fields first; field-less ones connect
      // directly (mirrors ConnectorsPage / Settings).
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

  // Disconnecting a built-in means switching it off: the backend refuses to
  // delete one, and the credential is what the owner actually wants gone.
  // Leaving them no disconnect at all was the trap — a built-in whose grant
  // had died still displayed "connected", which is the one state that hides
  // the Connect button.
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

  // Reconnect an installed connector: test first (server self-heals an
  // expired OAuth token), escalate to re-authorization only for OAuth.
  const handleReconnect = useCallback(
    (connector: ConnectorItem) => {
      setBusyKey(`installed:${connector.id}`);
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

  const handleDelete = useCallback(async () => {
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

  // OAuth popup posts back on success/error — refresh eagerly.
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

  return (
    <>
      <div className="w-[345px] shrink-0 overflow-y-auto border-r border-surface-border">
        {loading ? (
          <PageLoader logo className="py-16" />
        ) : (
          <div className="px-4 pt-6 pb-2">
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
                      status={c.status}
                      statusLabel={
                        STATUS_LABEL_KEY[c.status]
                          ? t(STATUS_LABEL_KEY[c.status])
                          : null
                      }
                      active={isSelected}
                      onClick={() => setActiveKey(`installed:${c.id}`)}
                      actions={
                        canDeleteConnector(c) && c.status !== "connected" ? (
                          <button
                            type="button"
                            aria-label={t("common.delete")}
                            className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-ink-meta transition-colors hover:bg-error-light hover:text-error-text"
                            onClick={(e) => {
                              e.stopPropagation();
                              setDeleteTarget(c);
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        ) : undefined
                      }
                    />
                  );
                }
                const cf = entry.item;
                const catalogKey = `catalog:${cf.connector.slug}`;
                return (
                  <ConnectorListItem
                    name={cf.connector.display_name}
                    iconUrl={cf.iconUrl}
                    active={isSelected}
                    onClick={() => setActiveKey(catalogKey)}
                    actions={
                      <UiButton
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
                      </UiButton>
                    }
                  />
                );
              }}
              emptyState={
                <EmptyState
                  className="py-16"
                  icon={<Search />}
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
                />
              }
            />
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1">
        {selectedInstalled ? (
          <ConnectorDetailPanel
            key={`installed:${selectedInstalled.id}`}
            name={selectedInstalled.display_name}
            iconUrl={iconBySlug.get(selectedInstalled.slug) ?? null}
            description={selectedInstalled.description}
            connected={selectedInstalled.status === "connected"}
            errorMessage={
              selectedInstalled.status === "error"
                ? selectedInstalled.error_message
                : null
            }
            tools={tools}
            toolsError={toolsError}
            busy={busyKey === `installed:${selectedInstalled.id}`}
            systemManaged={!canDeleteConnector(selectedInstalled)}
            onConnect={() => handleReconnect(selectedInstalled)}
            onDisconnect={() => {
              if (canDeleteConnector(selectedInstalled)) {
                setDeleteTarget(selectedInstalled);
              } else {
                handleDisableBuiltin(selectedInstalled);
              }
            }}
          />
        ) : selectedCatalog ? (
          <ConnectorDetailPanel
            key={`catalog:${selectedCatalog.connector.slug}`}
            name={selectedCatalog.connector.display_name}
            iconUrl={selectedCatalog.iconUrl}
            description={selectedCatalog.connector.description}
            connected={false}
            busy={busyKey === `catalog:${selectedCatalog.connector.slug}`}
            onConnect={() => handleConnectCatalog(selectedCatalog.connector)}
          />
        ) : (
          <div className="flex justify-center pt-24">
            <EmptyState icon={<Link2 />} message={t("resource.emptyDetail")} />
          </div>
        )}
      </div>

      <ConnectorAddDialog
        open={addMode !== null}
        mode={addMode ?? "http"}
        onOpenChange={(open) => {
          if (!open) onAddModeChange(null);
        }}
        onSubmit={runConnect}
      />

      <ConnectorConnectDialog
        key={connectEntry?.slug ?? "none"}
        entry={connectEntry}
        onClose={() => setConnectEntry(null)}
        onSubmit={runConnect}
      />

      <DeleteConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        itemName={deleteTarget?.display_name}
        onConfirm={() => void handleDelete()}
      />
    </>
  );
}
