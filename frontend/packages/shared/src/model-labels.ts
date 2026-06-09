/**
 * Model id → human-friendly display name.
 *
 * Upstream ``/v1/models`` only exposes a friendly name for the
 * Anthropic protocol; OpenAI-compatible providers (OpenAI / DeepSeek /
 * GLM / …) return bare ids. Rather than a half-working per-protocol
 * solution we map everything here. Misses fall back to the raw id
 * (never throws, never hides the model) — when a new model ships it
 * just shows its id until a line is added below.
 *
 * Not i18n: model names are brand-neutral and identical across
 * locales (``Opus 4.7`` in both zh-CN and en-US).
 */
export const MODEL_LABELS: Record<string, string> = {
  // ── Anthropic ──
  "claude-opus-4-8": "Opus 4.8",
  "claude-opus-4-7": "Opus 4.7",
  "claude-opus-4-6": "Opus 4.6",
  "claude-opus-4-5": "Opus 4.5",
  "claude-opus-4-1": "Opus 4.1",
  "claude-opus-4": "Opus 4",
  "claude-sonnet-4-6": "Sonnet 4.6",
  "claude-sonnet-4-5": "Sonnet 4.5",
  "claude-sonnet-4": "Sonnet 4",
  "claude-haiku-4-6": "Haiku 4.6",
  "claude-haiku-4-5": "Haiku 4.5",
  "claude-3-7-sonnet": "Sonnet 3.7",
  "claude-3-5-haiku": "Haiku 3.5",
  // ── DeepSeek ──
  "deepseek-v4-flash": "DeepSeek V4 Flash",
  "deepseek-v4-pro": "DeepSeek V4 Pro",
  "deepseek-v4-pro[1m]": "DeepSeek V4 Pro (1M)",
  "deepseek-chat": "DeepSeek Chat",
  "deepseek-reasoner": "DeepSeek Reasoner",
  // ── OpenAI ── (no hyphen in the label: "GPT 5.5", not "GPT-5.5")
  "gpt-5.5": "GPT 5.5",
  "gpt-5.4": "GPT 5.4",
  "gpt-5.4-mini": "GPT 5.4 Mini",
  "gpt-5.3-codex": "GPT 5.3 Codex",
  "gpt-5.3-codex-spark": "GPT 5.3 Codex Spark",
  "gpt-5.2": "GPT 5.2",
  // ── 智谱 GLM ── (glm-5.x current; the glm-* family rule also
  // auto-formats future versions, these are just the headline ids)
  "glm-5.1": "GLM 5.1",
  "glm-5": "GLM 5",
  "glm-4.7": "GLM 4.7",
  "glm-4.6": "GLM 4.6",
  "glm-4-plus": "GLM 4 Plus",
  "glm-4-flash": "GLM 4 Flash",
  // ── Kimi (Moonshot) ──
  "kimi-k2.6": "Kimi K2.6",
  "kimi-k2.5": "Kimi K2.5",
  "kimi-k2": "Kimi K2",
  "moonshot-v1-8k": "Moonshot v1 8K",
  "moonshot-v1-32k": "Moonshot v1 32K",
  "moonshot-v1-128k": "Moonshot v1 128K",
  // ── MiniMax ──
  "MiniMax-M2.7": "MiniMax M2.7",
  "MiniMax-M2.5": "MiniMax M2.5",
  "MiniMax-M2.1": "MiniMax M2.1",
  "MiniMax-M2": "MiniMax M2",
  // ── Reportify (Valuz built-in) ──
  "reportify-pro": "Reportify Pro",
  "reportify-lite": "Reportify Lite",
};

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
 * Family-aware fallback for ids not in {@link MODEL_LABELS}. Only the
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
 * Friendly model name. Four tiers, first hit wins:
 *  1. runtime overlay (overlay-contributed display_name; see
 *     {@link registerDynamicModelLabels})
 *  2. exact entry in {@link MODEL_LABELS}
 *  3. known-family rule ({@link prettifyKnownFamily}) — new versions of
 *     existing series auto-format without a table edit
 *  4. raw id (unknown vendor — never guessed, never hidden)
 */
export function modelLabel(id: string | null | undefined): string {
  if (!id) return "";
  const dyn = _dynamicLabels.get(id);
  if (dyn) return dyn;
  return MODEL_LABELS[id] ?? prettifyKnownFamily(id) ?? id;
}
