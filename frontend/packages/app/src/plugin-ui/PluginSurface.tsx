import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  uiApi,
  useUiBusStore,
  type UiItem,
  type UiRenderRequest,
  type UiSiteKind,
} from "@valuz/core";

import { surfaceRequest } from "./surface-request";
import { UiTree } from "./UiTree";

/**
 * One occurrence of a UI slot that backend plugins draw into: fetches the
 * drawings (``POST /v1/ui/render``), draws them, sends clicks back
 * (``/v1/ui/action``) and redraws on ``ui.invalidate``.
 *
 * - ``list`` / ``keyed`` sites draw each plugin's item (only ``slotKey``'s for
 *   keyed) and nothing while loading;
 * - a ``single`` site draws the one chained drawing, whose ``Default`` is the
 *   rest of the slot (``renderDefault``) — and the rest of the slot itself
 *   while loading or when every plugin passes.
 */

/** Redraw at most every 100ms per surface (10/s, as Claude Code mods). */
const REDRAW_THROTTLE_MS = 100;

interface PluginSurfaceProps {
  site: string;
  kind: UiSiteKind;
  slotKey?: string;
  /** The slot's context, as the host passed it to the contribution. */
  context: Record<string, unknown>;
  renderDefault?: (overrides?: Record<string, unknown>) => ReactNode;
}

export function PluginSurface({
  site,
  kind,
  slotKey,
  context,
  renderDefault,
}: PluginSurfaceProps) {
  // Rebuilt only when the wire request changes, not on every host render.
  const requestKey = JSON.stringify(surfaceRequest(site, slotKey, context));
  const stableRequest = useMemo(
    () => JSON.parse(requestKey) as UiRenderRequest,
    [requestKey],
  );
  const generation = useUiBusStore(
    (s) => (s.generation[site] ?? 0) + (s.generation["*"] ?? 0),
  );
  const [items, setItems] = useState<UiItem[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(
      () => {
        uiApi
          .render(stableRequest)
          .then((next) => {
            if (!cancelled) setItems(next);
          })
          .catch(() => {
            if (!cancelled) setItems([]);
          });
      },
      generation === 0 ? 0 : REDRAW_THROTTLE_MS,
    );
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [stableRequest, generation]);

  const act = useCallback(
    (item: UiItem, action: string, value?: unknown) => {
      setBusy(true);
      uiApi
        .action({
          ...stableRequest,
          owner: item.owner,
          action,
          value,
          generation: item.generation,
        })
        .then((result) => setItems(result.items))
        .catch(() => {})
        .finally(() => setBusy(false));
    },
    [stableRequest],
  );

  const visible =
    items === null
      ? null
      : kind === "keyed"
        ? items.filter((item) => item.key === slotKey)
        : items;

  if (kind === "single") {
    const item = visible?.[0];
    if (!item) return <>{renderDefault?.() ?? null}</>;
    return (
      <UiTree
        tree={item.tree}
        busy={busy}
        renderDefault={renderDefault}
        onAction={(action, value) => act(item, action, value)}
      />
    );
  }
  if (!visible || visible.length === 0) return null;
  return (
    <>
      {visible.map((item) => (
        <div
          key={`${item.owner}:${item.key ?? ""}`}
          data-slot="plugin-surface"
          data-plugin={item.owner}
          className="min-w-0"
        >
          <UiTree
            tree={item.tree}
            busy={busy}
            onAction={(action, value) => act(item, action, value)}
          />
        </div>
      ))}
    </>
  );
}
