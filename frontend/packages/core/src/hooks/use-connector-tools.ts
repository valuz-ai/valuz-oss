/**
 * Connector tool list, probed once per **client session** and cached at module
 * level.
 *
 * Selecting a connected connector runs a real MCP ``test()`` (a reconnect) to
 * fetch its tools. Caching that per connector id in component state only lasts
 * while the Connectors page is mounted — leaving and returning re-probes. This
 * module-level cache instead lives for the whole client session (until reload),
 * so re-selecting a connector — or navigating away from the Connectors page and
 * back — never reconnects again. {@link invalidateConnectorTools} drops one
 * entry after a connect / reauthorize so its tools refresh.
 *
 * Mirrors the module-level singleton pattern of {@link useRunningRuns} /
 * useConnectorAlert (shared state + subscriber set).
 */

import { useEffect, useState } from "react";

import { connectorsApi } from "../api/connectors-api";
import { t } from "@valuz/shared/i18n";
import type { ToolInfo } from "@valuz/shared";

interface ToolsEntry {
  tools: ToolInfo[];
  error: string | null;
}

const _cache = new Map<string, ToolsEntry>();
const _inflight = new Set<string>();
const _subscribers = new Set<() => void>();

const _notify = (): void => {
  _subscribers.forEach((fn) => fn());
};

/**
 * Drop a connector's cached tool probe (e.g. after a connect / reauthorize /
 * reconnect), so the next view re-probes for fresh tools.
 */
export const invalidateConnectorTools = (id: string): void => {
  if (_cache.delete(id)) _notify();
};

export interface ConnectorToolsResult {
  /** ``undefined`` = still probing (loading); array = probe finished. */
  tools: ToolInfo[] | undefined;
  error: string | null;
}

/**
 * Cached tool list for a connected connector. Pass ``enabled=false`` (e.g. the
 * connector isn't connected) to skip probing. The first view probes once and
 * caches for the session; later views reuse the cache.
 */
export const useConnectorTools = (
  connectorId: string | null,
  enabled: boolean,
): ConnectorToolsResult => {
  const [, setTick] = useState(0);
  useEffect(() => {
    const sub = (): void => setTick((n) => n + 1);
    _subscribers.add(sub);
    return () => {
      _subscribers.delete(sub);
    };
  }, []);

  useEffect(() => {
    if (!connectorId || !enabled) return;
    if (_cache.has(connectorId) || _inflight.has(connectorId)) return;
    let cancelled = false;
    _inflight.add(connectorId);
    void connectorsApi
      .test(connectorId)
      .then((res) => {
        _cache.set(connectorId, {
          tools: res.ok ? (res.tool_details ?? []) : [],
          error: res.ok
            ? null
            : (res.error ?? t("settings.connectors.testFailed")),
        });
      })
      .catch((err: unknown) => {
        _cache.set(connectorId, {
          tools: [],
          error: err instanceof Error ? err.message : "unknown",
        });
      })
      .finally(() => {
        _inflight.delete(connectorId);
        if (!cancelled) _notify();
      });
    return () => {
      cancelled = true;
    };
  }, [connectorId, enabled]);

  const entry = connectorId && enabled ? _cache.get(connectorId) : undefined;
  return { tools: entry?.tools, error: entry?.error ?? null };
};
