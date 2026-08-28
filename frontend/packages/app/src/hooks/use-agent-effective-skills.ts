import { useEffect, useMemo, useState } from "react";
import { agentsApi, type SkillView } from "@valuz/core";

import type { AgentSkillItem } from "../lib/agent-skill-items";

/**
 * The skills a session for `agentSlug` will actually be created with.
 *
 * Asks the backend rather than deriving it here. Session composition has one
 * owner — `EffectiveResourceResolver`, the same code the session builder runs —
 * and every client-side re-derivation of it has been wrong in a different way:
 * reading `agent.skills` misses the always-on baseline the host injects into
 * every session, and mirroring the library predicate here drifts the moment the
 * backend's changes.
 *
 * `catalog` is used for DISPLAY only: the manifest carries ids and names but no
 * description, and the `/` picker searches on description. The backend says
 * WHICH skills; the catalog says what they look like.
 *
 * @param agentSlug library slug (a project member's `source_agent_slug`), or
 *   `null` when no agent is selected — the list is then empty.
 * @param baseUrl execution target for an agent that lives on another backend.
 */
export function useAgentEffectiveSkills(
  agentSlug: string | null | undefined,
  catalog: readonly SkillView[],
  baseUrl?: string,
): AgentSkillItem[] {
  const [resolved, setResolved] = useState<{
    slug: string;
    skills: { id: string; slug: string; name: string }[];
  } | null>(null);
  // The fetch deliberately does NOT depend on the catalog: it is a display
  // join only, and a fresh array identity from a parent re-render would turn
  // every render into a refetch.
  useEffect(() => {
    if (!agentSlug) {
      setResolved(null);
      return;
    }
    let cancelled = false;
    agentsApi
      .getEffectiveResources(agentSlug, baseUrl ? { baseUrl } : {})
      .then((manifest) => {
        if (cancelled) return;
        setResolved({ slug: agentSlug, skills: manifest.skills });
      })
      .catch(() => {
        // A picker that cannot reach the backend shows nothing rather than a
        // guess — a guessed entry inserts a ``/slug`` the runtime may not have.
        if (!cancelled) setResolved(null);
      });
    return () => {
      cancelled = true;
    };
  }, [agentSlug, baseUrl]);

  return useMemo(() => {
    // Ignore a manifest still describing the previously selected agent, so
    // switching agents never shows the old one's skills for a frame.
    if (!resolved || !agentSlug || resolved.slug !== agentSlug) return [];
    const bySlug = new Map<string, SkillView>();
    for (const s of catalog) {
      if (s.slug && !bySlug.has(s.slug)) bySlug.set(s.slug, s);
    }
    return resolved.skills.map((item) => ({
      id: item.id,
      name: bySlug.get(item.slug)?.name ?? item.name,
      slug: item.slug,
      description: bySlug.get(item.slug)?.description,
    }));
  }, [resolved, agentSlug, catalog]);
}
