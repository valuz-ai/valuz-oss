import {
  createPluginHost,
  personalProfile,
  useRegistryStore,
  type PluginHost,
} from "@valuz/core";

import { loadOssPlugins, type LoadOssPluginsOptions } from "../boot";

const composed: PluginHost[] = [];

/**
 * Compose the OSS app into the shared registry the way a real boot does: an
 * empty layer stack over the personal base, then the OSS plugins on a fresh
 * plugin host (so ``host.unload(id)`` withdraws one feature). For tests that
 * render a page or read the registry and need the OSS pages to be there.
 *
 * ``inactive`` stands in for ``GET /v1/extensions/backend/state``: plugins
 * named there are skipped. It defaults to "nothing is off" so a test never
 * reaches for the network.
 */
export async function composeOss(
  options: LoadOssPluginsOptions = {},
): Promise<PluginHost> {
  const store = useRegistryStore.getState();
  store.clearLayers();
  store.hydrate(personalProfile);
  const host = createPluginHost();
  composed.push(host);
  await loadOssPlugins(host, { inactive: [], ...options });
  return host;
}

/**
 * Unload every plugin of every host ``composeOss`` made, so the component
 * contributions of one test's plugins do not stack under the next test's.
 */
export async function disposeComposedOss(): Promise<void> {
  for (const host of composed.splice(0)) {
    for (const record of [...host.list()].reverse()) {
      await host.unload(record.id);
    }
  }
}
