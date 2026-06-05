/**
 * Built-in parser plugin UI registrations.
 *
 * Each subdirectory under ``src/`` is a self-contained plugin: its own
 * locale JSON files and a single ``register()`` export that wires
 * everything through ``registerParserPluginUI``. Adding a new plugin
 * means dropping a new subdir + importing it here.
 *
 * Phase 2 plan: each subdirectory becomes its own npm package
 * (``@valuz/parser-<id>``), this aggregator gets replaced with a
 * build-time scan of ``node_modules/@valuz/parser-*`` for packages
 * marked ``valuz.kind === "parser_plugin"`` in their ``package.json``.
 * For now, the explicit list keeps imports static and tree-shakable.
 *
 * The host app must call ``initParserPlugins()`` ONCE, synchronously,
 * BEFORE ``createRoot(...).render(...)``. Plugin registration mutates
 * the i18n runtime state and ``useSyncExternalStore`` subscribers
 * (``useTranslation``) sample that state during their initial render.
 * Running ``register()`` after mount would produce a torn snapshot.
 */

import { register as registerLightLocal } from "./light_local";
import { register as registerMineru } from "./mineru";
import { register as registerPaddleocr } from "./paddleocr";
import { register as registerValuzOcr } from "./valuz_ocr";

let _initialized = false;

export function initParserPlugins(): void {
  if (_initialized) return;
  _initialized = true;
  registerLightLocal();
  registerMineru();
  registerPaddleocr();
  registerValuzOcr();
}
