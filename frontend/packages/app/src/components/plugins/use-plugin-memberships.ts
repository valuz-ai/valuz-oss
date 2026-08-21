import { useEffect, useMemo, useState } from "react";
import { pluginsApi } from "@valuz/core";
import type { AgentPluginMemberKind, AgentPluginMembershipMap } from "@valuz/core";
import type { PluginBadgeInfo } from "@valuz/ui";

/**
 * Batch ``slug → owning plugins`` lookup for library cards (D6 badges).
 * One request per distinct slug set (i.e. per list load); failures degrade
 * to "no badges" rather than an error surface. Returns a lookup that maps a
 * slug to the ``PluginBadge`` props, or ``null`` when the resource belongs
 * to no plugin.
 */
export function usePluginMemberships(
  kind: AgentPluginMemberKind,
  slugs: readonly (string | null | undefined)[],
): (slug: string | null | undefined) => PluginBadgeInfo | null {
  const key = useMemo(
    () =>
      Array.from(
        new Set(slugs.filter((s): s is string => typeof s === "string" && !!s)),
      )
        .sort()
        .join(","),
    [slugs],
  );
  const [map, setMap] = useState<AgentPluginMembershipMap>({});

  useEffect(() => {
    if (!key) {
      setMap({});
      return;
    }
    let cancelled = false;
    pluginsApi
      .memberships(kind, key.split(","))
      .then((res) => {
        if (!cancelled) setMap(res ?? {});
      })
      .catch(() => {
        if (!cancelled) setMap({});
      });
    return () => {
      cancelled = true;
    };
  }, [kind, key]);

  return useMemo(
    () => (slug: string | null | undefined) => {
      if (!slug) return null;
      const owners = map[slug];
      if (!owners || owners.length === 0) return null;
      return { name: owners[0].name, more: owners.length - 1 };
    },
    [map],
  );
}
