import type { SkillView } from "@valuz/core";

/** A composer `/`-picker item (structurally matches `@valuz/ui`'s
 *  `SkillSearchItem`, kept dependency-free here). */
export interface AgentSkillItem {
  id: string;
  name: string;
  slug?: string;
  description?: string;
}

/**
 * Resolve an agent's stored skill entries to composer `/`-picker items.
 *
 * Agents persist skills as either a slug (`"sector-overview"`) or an absolute
 * path (`"/Users/.../skills/weather-query-v2"`) — the directory basename is the
 * slug. Each entry is matched against the provided skill catalogs (first match
 * wins, so pass higher-priority catalogs first) to recover a display
 * name/description; an entry the catalogs don't know still resolves to its bare
 * slug so nothing silently disappears. Deduped by slug, order preserved.
 */
/**
 * The catalog entries an `all_available` agent can actually run.
 *
 * Mirrors the backend's `EffectiveResourceResolver` predicate — library switch
 * on, entitled, materialized — so the `/` picker offers exactly what the
 * session will be created with. A skill failing any of these is dropped rather
 * than shown-and-then-silently-absent at run time.
 *
 * Deduped by slug: one slug can appear in the catalog more than once (a user
 * copy shadowing the official package, say), and the picker must list it once.
 */
export function libraryEnabledSkillItems(
  catalog: readonly SkillView[],
): AgentSkillItem[] {
  const items: AgentSkillItem[] = [];
  const seen = new Set<string>();
  for (const s of catalog) {
    if (s.library_enabled === false) continue;
    if (s.is_locked) continue;
    if ((s.status ?? "available") !== "available") continue;
    const key = s.slug ?? s.id;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({
      id: s.id,
      name: s.name,
      slug: s.slug,
      description: s.description,
    });
  }
  return items;
}

export function resolveAgentSkillItems(
  entries: readonly string[] | null | undefined,
  catalogs: readonly (readonly SkillView[])[],
): AgentSkillItem[] {
  if (!entries?.length) return [];
  const bySlug = new Map<string, SkillView>();
  for (const cat of catalogs) {
    for (const s of cat) {
      if (s.slug && !bySlug.has(s.slug)) bySlug.set(s.slug, s);
    }
  }
  const items: AgentSkillItem[] = [];
  const seen = new Set<string>();
  for (const entry of entries) {
    const slug = entry.includes("/")
      ? (entry.split("/").filter(Boolean).pop() ?? entry)
      : entry;
    if (!slug || seen.has(slug)) continue;
    seen.add(slug);
    const meta = bySlug.get(slug);
    items.push(
      meta
        ? {
            id: meta.id,
            name: meta.name,
            slug: meta.slug,
            description: meta.description,
          }
        : { id: slug, name: slug, slug },
    );
  }
  return items;
}
