import type { ComponentType, ReactNode } from "react";
import { toast } from "sonner";
import {
  uiApi,
  uiBus,
  useRegistryStore,
  useUiBusStore,
  type UiPushEvent,
  type UiSite,
} from "@valuz/core";

import { PluginSurface } from "./PluginSurface";
import {
  PluginLogLines,
  PluginStatusLine,
  PluginToolNotices,
} from "./push-surfaces";

/**
 * The frontend half of the UI bus (hooks-and-plugin-ui.md §6.3):
 *
 * - when told to (``ui.invalidate`` of ``*``, which the user stream sends on
 *   connect whenever some backend plugin draws UI), asks which UI slots
 *   plugins draw into (``/v1/ui/sites``) and mounts a {@link PluginSurface}
 *   in exactly those, unmounting it when a site goes away;
 * - shows ``ui.toast`` pushes, and mounts the status / log / notice surfaces
 *   on the first push of their kind.
 *
 * Nothing is fetched or mounted until the backend says so, so with no backend
 * plugin drawing UI every slot stays empty and every page renders as before.
 */

type SlotComponent = ComponentType<Record<string, unknown>>;

/** Plugin drawings sit after the host's own contributions in a slot. */
const SURFACE_PRIORITY = 500;
/** Push surfaces sit innermost / last (a tool notice right under its card). */
const PUSH_PRIORITY = 1000;
const RETRY_BASE_MS = 2000;
const RETRY_MAX_MS = 30_000;
const MAX_RETRIES = 12;

export function siteRegistrationId(site: UiSite): string {
  return site.kind === "keyed"
    ? `plugin-ui:${site.site}:${site.key ?? ""}`
    : `plugin-ui:${site.site}`;
}

function surfaceFor(site: UiSite): SlotComponent {
  function SiteSurface(props: Record<string, unknown>) {
    const { renderDefault, ...context } = props as Record<string, unknown> & {
      renderDefault?: (overrides?: Record<string, unknown>) => ReactNode;
    };
    return (
      <PluginSurface
        site={site.site}
        kind={site.kind}
        slotKey={site.key}
        context={context}
        renderDefault={renderDefault}
      />
    );
  }
  SiteSurface.displayName = `PluginSurface(${siteRegistrationId(site)})`;
  return SiteSurface;
}

const PUSH_SURFACES: Record<
  string,
  { slot: string; id: string; component: SlotComponent }
> = {
  status: {
    slot: "conversation.composer.dock",
    id: "plugin-ui:status",
    component: PluginStatusLine as SlotComponent,
  },
  log: {
    slot: "conversation.turn.tail",
    id: "plugin-ui:log",
    component: PluginLogLines as SlotComponent,
  },
  notice: {
    slot: "conversation.tool-call",
    id: "plugin-ui:notice",
    component: PluginToolNotices as SlotComponent,
  },
};

function showToast(event: UiPushEvent): void {
  const text = event.payload.text;
  if (!text) return;
  switch (event.payload.tone) {
    case "success":
      toast.success(text);
      return;
    case "warning":
      toast.warning(text);
      return;
    case "error":
      toast.error(text);
      return;
    default:
      toast(text);
  }
}

/** Start the UI bus frontend; the returned function undoes everything. */
export function startPluginUi(): () => void {
  const registry = () => useRegistryStore.getState();
  const siteDisposers = new Map<string, () => void>();
  const pushDisposers = new Map<string, () => void>();
  let stopped = false;
  let failures = 0;
  let retryTimer: number | undefined;
  let syncFailed = false;

  const applySites = (sites: UiSite[]): void => {
    useUiBusStore.getState().setSites(sites);
    const wanted = new Map(
      sites.map((site) => [siteRegistrationId(site), site]),
    );
    for (const [id, dispose] of siteDisposers) {
      if (!wanted.has(id)) {
        dispose();
        siteDisposers.delete(id);
      }
    }
    for (const [id, site] of wanted) {
      if (siteDisposers.has(id)) continue;
      siteDisposers.set(
        id,
        registry().registerSlot(site.site, {
          id,
          component: surfaceFor(site),
          priority: SURFACE_PRIORITY,
          key: site.key,
          label: site.label,
        }),
      );
    }
  };

  const sync = (): void => {
    window.clearTimeout(retryTimer);
    uiApi
      .sites()
      .then((sites) => {
        if (stopped) return;
        failures = 0;
        syncFailed = false;
        applySites(sites);
      })
      .catch(() => {
        if (stopped) return;
        // The backend may not be up (desktop boot) or the user not signed in
        // yet: back off, then wait for the window to regain focus.
        syncFailed = true;
        failures += 1;
        if (failures > MAX_RETRIES) return;
        retryTimer = window.setTimeout(
          sync,
          Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** (failures - 1)),
        );
      });
  };

  const onFocus = (): void => {
    if (syncFailed && failures > MAX_RETRIES) {
      failures = 0;
      sync();
    }
  };

  const unsubscribe = uiBus.subscribe((event) => {
    if (event.kind === "toast") {
      showToast(event);
      return;
    }
    if (event.kind === "invalidate" && (event.payload.site || "*") === "*") {
      // The set of drawing plugins may have changed.
      sync();
      return;
    }
    const surface = PUSH_SURFACES[event.kind];
    if (surface && !pushDisposers.has(event.kind)) {
      pushDisposers.set(
        event.kind,
        registry().registerSlot(surface.slot, {
          id: surface.id,
          component: surface.component,
          priority: PUSH_PRIORITY,
        }),
      );
    }
  });

  window.addEventListener("focus", onFocus);

  return () => {
    stopped = true;
    window.clearTimeout(retryTimer);
    window.removeEventListener("focus", onFocus);
    unsubscribe();
    for (const dispose of [
      ...siteDisposers.values(),
      ...pushDisposers.values(),
    ]) {
      dispose();
    }
    siteDisposers.clear();
    pushDisposers.clear();
  };
}
