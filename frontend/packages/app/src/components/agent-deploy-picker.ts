import { useCallback, useEffect, useState } from "react";
import { agentsApi, type Agent } from "@valuz/core";

/** The built-in runtime Agent. */
export const VALURION_SLUG = "valurion";

function defaultSystemAgentSlug(agents: Agent[]): string | null {
  return agents.find((agent) => agent.slug === VALURION_SLUG)?.slug ?? null;
}

export interface AgentDeployPicker {
  agents: Agent[];
  selected: string[];
  toggle: (slug: string) => void;
  /** Reset the selection to its default (Valurion when present). */
  reset: () => void;
  /** Deploy the selected agents into a freshly-created project. Best-effort:
   *  resolves to the number that failed (membership is mutable, so a partial
   *  failure isn't fatal — the caller surfaces a count). */
  deploy: (projectId: string) => Promise<number>;
}

/** Shared state for the create-project dialogs' "deploy agents" multi-select.
 *  Loads the library and defaults the selection to Valurion when present.
 *  Used by both create entry points (projects page + sidebar) so they can't
 *  drift. Pass the chosen execution target's ``baseUrl`` (multi-target
 *  editions) so a cloud-bound project lists cloud-deployable agents — a cloud
 *  backend can't instantiate a slug that only exists in the local library. */
export function useAgentDeployPicker(baseUrl?: string): AgentDeployPicker {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selected, setSelected] = useState<string[]>([]);

  useEffect(() => {
    void agentsApi
      .listAgents(undefined, baseUrl ? { baseUrl } : undefined)
      .then((d) => {
        // Surface the default assistant first so it reads as the primary pick
        // (stable sort keeps the rest in the library's order).
        const preferredSlug = defaultSystemAgentSlug(d.agents);
        const ordered = [...d.agents].sort((a, b) =>
          a.slug === preferredSlug
            ? -1
            : b.slug === preferredSlug
              ? 1
              : 0,
        );
        setAgents(ordered);
        setSelected(preferredSlug ? [preferredSlug] : []);
      })
      .catch(() => {
        /* non-fatal: the picker just shows no agents to deploy */
      });
  }, [baseUrl]);

  const reset = useCallback(() => {
    const preferredSlug = defaultSystemAgentSlug(agents);
    setSelected(preferredSlug ? [preferredSlug] : []);
  }, [agents]);

  const toggle = useCallback((slug: string) => {
    setSelected((prev) =>
      prev.includes(slug) ? prev.filter((s) => s !== slug) : [...prev, slug],
    );
  }, []);

  const deploy = useCallback(
    async (projectId: string): Promise<number> => {
      if (selected.length === 0) return 0;
      const results = await Promise.allSettled(
        selected.map((slug) =>
          agentsApi.deploy(projectId, { source_agent_slug: slug }),
        ),
      );
      return results.filter((r) => r.status === "rejected").length;
    },
    [selected],
  );

  return { agents, selected, toggle, reset, deploy };
}
