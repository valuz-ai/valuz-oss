/**
 * Process-wide registry of frontend parser plugin UI registrations.
 *
 * Plugin packages call ``registerParserPluginUI`` from their
 * ``register()`` entry point, which the host app invokes once at
 * bootstrap before ``createRoot(...).render(...)``. This ordering
 * matters: ``useSyncExternalStore`` subscribers (i.e. ``useTranslation``)
 * read i18n state synchronously during render, so plugin locales must
 * be merged into ``state.translations`` before the React tree mounts.
 *
 * The registry is intentionally a flat Map — no async loading, no
 * version negotiation. Phase 2 (out-of-tree plugin packages) can layer
 * those concerns on top without changing this surface.
 */

import { registerLocaleNamespace } from "@valuz/shared/i18n";
import type { LocaleCode } from "@valuz/shared/i18n";
import type { ParserPluginUI } from "./types";

const registry = new Map<string, ParserPluginUI>();

/**
 * Register a parser plugin's frontend metadata.
 *
 * Side effects:
 *  - Merges every entry in ``spec.locales`` into the i18n runtime
 *    state under the resolved namespace (``spec.i18nNamespace`` or
 *    ``parser_<id>`` as fallback).
 *  - Stores the spec in the registry, keyed by ``spec.id``. Re-
 *    registering the same id replaces the previous entry — useful for
 *    hot-reload and tests.
 *
 * Returns a disposer that removes the entry from the registry. Note
 * that the disposer does NOT currently un-merge the locale strings —
 * un-merging i18n is a more invasive operation than the current call
 * sites need. If a future use case requires it, extend
 * ``registerLocaleNamespace`` with an inverse and call it from here.
 */
export function registerParserPluginUI(spec: ParserPluginUI): () => void {
  const namespace = spec.i18nNamespace ?? `parser_${spec.id}`;
  if (spec.locales) {
    for (const [locale, data] of Object.entries(spec.locales)) {
      if (data) {
        registerLocaleNamespace(
          namespace,
          locale as LocaleCode,
          data as Record<string, unknown>,
        );
      }
    }
  }
  registry.set(spec.id, spec);
  return () => {
    registry.delete(spec.id);
  };
}

export function getParserPluginUI(id: string): ParserPluginUI | undefined {
  return registry.get(id);
}

export function listParserPluginUIs(): ParserPluginUI[] {
  return Array.from(registry.values());
}
