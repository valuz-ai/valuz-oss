/**
 * Frontend-side registration shape for a parser plugin.
 *
 * Pairs with ``ParserPluginDescriptor`` on the backend
 * (``backend/valuz_agent/ports/parser_plugin.py``). Where the backend
 * ships static descriptor metadata + the parsing implementation, the
 * frontend ``ParserPluginUI`` ships display strings (i18n locale
 * resources) plus presentation-only knobs that don't belong on the
 * wire. Each parser plugin lives in BOTH a backend directory (Python)
 * AND a frontend directory (TS); the ``id`` field bridges the two.
 *
 * Phase 1 (this iteration) keeps the surface intentionally narrow —
 * just locales + a sort-weight fallback. Future iterations may add
 * component slots (setup wizards, custom test panels) when a real
 * use case appears; resisting that for now keeps the
 * "zero React code per plugin" property of the data-driven render.
 */

import type { LocaleCode } from "@valuz/shared/i18n";

export interface ParserPluginUI {
  /** Stable plugin id — matches ``PluginDescriptor.id`` from the
   *  backend. Used as the lookup key in ``getParserPluginUI``. */
  id: string;

  /** i18n namespace owned by this plugin. Defaults to
   *  ``parser_<id>`` when omitted. The plugin's locale JSON is
   *  registered under this namespace via ``registerLocaleNamespace``
   *  so the keys don't collide with main-app strings. */
  i18nNamespace?: string;

  /** Locale resources merged into the runtime i18n state at
   *  registration time. Each value is the nested JSON shape that
   *  matches the plugin's ``locale.<code>.json`` file — keys are
   *  added under the plugin's namespace. */
  locales?: Partial<Record<LocaleCode, Record<string, unknown>>>;

  /** Frontend-side sort-weight fallback used only when the backend
   *  descriptor doesn't carry ``sort_weight``. Plugins that ship a
   *  backend descriptor should rely on that field instead — this
   *  exists for completeness (e.g. third-party UI-only registrations
   *  that don't go through the backend at all). */
  sortWeight?: number;
}
