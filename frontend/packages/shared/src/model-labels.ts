/**
 * Frontend model-name fallback.
 *
 * Display names are **server-authoritative**: the backend stamps each model's
 * ``label`` onto the providers list (curated subscription / system catalogs),
 * and ``providersApi.list()`` projects those into the runtime overlay below via
 * {@link registerDynamicModelLabels}. This module no longer hand-maintains a
 * static name table — it only keeps the low-churn, family-aware rules as a
 * fallback for ids the backend didn't label (e.g. a user's own api-key models,
 * which get a richer type-aware label later).
 *
 * Not i18n: model names are brand-neutral and identical across locales.
 */

const cap = (s: string): string =>
  s ? s.charAt(0).toUpperCase() + s.slice(1) : s;

/** Title-case a hyphenated suffix: ``codex-spark`` → ``Codex Spark``,
 * ``v5`` → ``V5``, ``8k`` → ``8K``. */
const prettySuffix = (s: string): string =>
  s
    .split("-")
    .map((w) => (/^\d/.test(w) ? w.toUpperCase() : cap(w)))
    .join(" ");

/**
 * Family-aware fallback for ids the backend didn't label. Only the
 * known families (claude / gpt / glm / deepseek / kimi / minimax /
 * moonshot) get auto-formatted so new versions track the series style
 * with zero maintenance. Anything else returns ``null`` so the caller
 * falls back to the raw id — we never guess on unknown vendors (that
 * produced ugly names like ``Foo Bar Baz``).
 */
function prettifyKnownFamily(id: string): string | null {
  let m = /^claude-(opus|sonnet|haiku)-(\d+)-(\d+)$/.exec(id);
  if (m) return `${cap(m[1])} ${m[2]}.${m[3]}`;

  m = /^gpt-([\d.]+)(?:-(.+))?$/.exec(id);
  if (m) return `GPT ${m[1]}${m[2] ? ` ${prettySuffix(m[2])}` : ""}`;

  m = /^glm-([\d.]+)(?:-(.+))?$/.exec(id);
  if (m) return `GLM ${m[1]}${m[2] ? ` ${prettySuffix(m[2])}` : ""}`;

  m = /^deepseek-(.+)$/.exec(id);
  if (m) return `DeepSeek ${prettySuffix(m[1])}`;

  m = /^kimi-(.+)$/.exec(id);
  if (m) return `Kimi ${prettySuffix(m[1])}`;

  m = /^minimax-(.+)$/i.exec(id);
  if (m) return `MiniMax ${prettySuffix(m[1])}`;

  m = /^moonshot-v1-(\d+k)$/i.exec(id);
  if (m) return `Moonshot v1 ${m[1].toUpperCase()}`;

  return null;
}

/**
 * Process-wide ``{id: label}`` overlay populated at runtime — e.g. by
 * ``providersApi.list()`` after each fetch, projecting every provider's
 * ``model_labels`` into one flat map. Lets overlay-contributed system
 * models (where the admin sets ``display_name`` on the gateway row) show
 * their friendly name in every UI surface without touching the call
 * sites — ``modelLabel(id)`` just hits the overlay first.
 *
 * Same-id collisions across providers are last-write-wins; in practice
 * a single gateway is the authoritative source for system-model ids, so
 * the only realistic collision is "same id, same label" anyway.
 */
const _dynamicLabels = new Map<string, string>();

export function registerDynamicModelLabels(
  labels: Record<string, string> | null | undefined,
): void {
  if (!labels) return;
  for (const [id, label] of Object.entries(labels)) {
    if (typeof label === "string" && label.trim()) {
      _dynamicLabels.set(id, label);
    }
  }
}

/** Test helper — drop the entire overlay. Production code should never call this. */
export function _clearDynamicModelLabels(): void {
  _dynamicLabels.clear();
}

/**
 * Friendly model name. Three tiers, first hit wins:
 *  1. runtime overlay (the backend's server-authoritative ``label``, projected
 *     in by {@link registerDynamicModelLabels} after ``providersApi.list()``)
 *  2. known-family rule ({@link prettifyKnownFamily}) — new versions of existing
 *     series auto-format without any table
 *  3. raw id (unknown vendor — never guessed, never hidden)
 */
export function modelLabel(id: string | null | undefined): string {
  if (!id) return "";
  const dyn = _dynamicLabels.get(id);
  if (dyn) return dyn;
  return prettifyKnownFamily(id) ?? id;
}
