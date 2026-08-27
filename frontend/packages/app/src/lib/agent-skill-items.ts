import type { SkillView } from "@valuz/core";

/** A composer `/`-picker item (structurally matches `@valuz/ui`'s
 *  `SkillSearchItem`, kept dependency-free here). */
export interface AgentSkillItem {
  id: string;
  name: string;
  slug?: string;
  description?: string;
}

/** The shape the pickers need off a member agent. Structurally satisfied by
 *  `@valuz/core`'s `AgentSummary`, kept local so this module stays pure. */
export interface SkillBearingAgent {
  skills?: readonly string[] | null;
  resource_policy?: "explicit" | "all_available";
}

function toItem(s: SkillView): AgentSkillItem {
  return { id: s.id, name: s.name, slug: s.slug, description: s.description };
}

/** Append `items` to `into`, skipping slugs already present. */
function mergeBySlug(
  into: AgentSkillItem[],
  seen: Set<string>,
  items: readonly AgentSkillItem[],
): void {
  for (const item of items) {
    const key = item.slug ?? item.id;
    if (seen.has(key)) continue;
    seen.add(key);
    into.push(item);
  }
}

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
    items.push(toItem(s));
  }
  return items;
}

/**
 * The always-on baseline: skills the host materializes into EVERY session,
 * whatever the agent binds (`capability_resolver.always_on_skill_paths` —
 * `valuz-project-docs`, `citation`, `skill-creator`, and `browser` where the
 * engine is available). The backend flags them; deriving the set client-side
 * would drift the moment that list changes.
 */
export function alwaysOnSkillItems(
  catalog: readonly SkillView[],
): AgentSkillItem[] {
  const items: AgentSkillItem[] = [];
  const seen = new Set<string>();
  for (const s of catalog) {
    if (!s.always_on) continue;
    const key = s.slug ?? s.id;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push(toItem(s));
  }
  return items;
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
    items.push(meta ? toItem(meta) : { id: slug, name: slug, slug });
  }
  return items;
}

/**
 * What `/` offers in a PROJECT conversation — the one rule, shared by the
 * conversation composer and the project-detail draft composer.
 *
 * The list answers "what can this conversation actually run", so it mirrors
 * what the host materializes into the session rather than any single stored
 * field:
 *
 * - an `all_available` agent (Valurion) binds nothing and receives the owner's
 *   live library at session-creation time, so it gets the library;
 * - every other agent gets exactly what it bound — "bound to nothing" stays
 *   distinguishable from "bound to everything";
 * - and BOTH get the always-on baseline on top, because the host injects it
 *   into every session no matter what the agent declares.
 *
 * What deliberately does NOT appear: library skills an explicit agent has not
 * bound. The host does not materialize those, so offering them would insert a
 * `/slug` the runtime cannot resolve — worse than not offering it at all. Bind
 * the skill to the agent (or use an `all_available` one) to get it.
 */
export function projectComposerSkillItems(
  agent: SkillBearingAgent | null | undefined,
  catalog: readonly SkillView[],
): AgentSkillItem[] {
  const items: AgentSkillItem[] = [];
  const seen = new Set<string>();
  mergeBySlug(
    items,
    seen,
    agent?.resource_policy === "all_available"
      ? libraryEnabledSkillItems(catalog)
      : resolveAgentSkillItems(agent?.skills, [catalog]),
  );
  mergeBySlug(items, seen, alwaysOnSkillItems(catalog));
  return items;
}
