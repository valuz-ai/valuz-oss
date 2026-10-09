/** A plugin's own translations: ``locales/<lang>.json`` of its package. */
export type LocaleTree = Record<string, unknown>;
export type PluginLocales = Record<string, LocaleTree>;

export type Translate = (
  key: string,
  params?: Record<string, string | number>,
) => string;

const FALLBACK_LOCALE = "en-US";

/** ``{ a: { b: "x" } }`` -> ``{ "a.b": "x" }``. */
export function flattenLocale(
  tree: LocaleTree,
  prefix = "",
  into: Record<string, string> = {},
): Record<string, string> {
  for (const [key, value] of Object.entries(tree)) {
    const full = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") into[full] = value;
    else if (value && typeof value === "object" && !Array.isArray(value)) {
      flattenLocale(value as LocaleTree, full, into);
    }
  }
  return into;
}

function interpolate(
  template: string,
  params?: Record<string, string | number>,
): string {
  if (!params) return template;
  return template.replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (whole, name: string) =>
    params[name] !== undefined ? String(params[name]) : whole,
  );
}

/** The locale keys to try, best first: exact, same language, en-US. */
function candidates(locale: string, available: string[]): string[] {
  const language = locale.split("-")[0]?.toLowerCase() ?? "";
  const sameLanguage = available.filter(
    (name) => name !== locale && name.split("-")[0]?.toLowerCase() === language,
  );
  return [locale, ...sameLanguage, FALLBACK_LOCALE];
}

/**
 * A translator over the plugin's own locale files. Resolution: the host's
 * locale, then another variant of its language, then ``en-US``, then the key
 * itself. ``{{name}}`` placeholders are replaced from ``params``.
 */
export function createTranslator(
  locales: PluginLocales,
  getLocale: () => string,
): Translate {
  const flat = new Map<string, Record<string, string>>();
  for (const [name, tree] of Object.entries(locales ?? {})) {
    flat.set(name, flattenLocale(tree ?? {}));
  }
  const available = [...flat.keys()];

  return (key, params) => {
    for (const name of candidates(getLocale(), available)) {
      const value = flat.get(name)?.[key];
      if (value !== undefined) return interpolate(value, params);
    }
    return interpolate(key, params);
  };
}

/** Whether ``key`` is a key of any of the plugin's locale files. */
export function hasLocaleKey(locales: PluginLocales, key: string): boolean {
  return Object.values(locales ?? {}).some(
    (tree) => flattenLocale(tree ?? {})[key] !== undefined,
  );
}
