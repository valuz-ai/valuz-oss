import {
  findSlotSpec,
  useCategoryRegistry,
  useRegistryStore,
} from "@valuz/core";

import { resolveRoutes } from "../../routes/route-registry";

/**
 * Serializable projection of everything the OSS plugins (and the base they sit
 * on) contribute to the shared registries.
 *
 * Moving an OSS feature into a plugin changes HOW it is registered, never WHAT
 * is registered, so the projection of the bare personal edition is pinned by
 * snapshot and must stay byte-identical across that move. Functions and
 * components are recorded as ``"[fn]"`` — their identity is carried by the
 * surrounding ids.
 *
 * ``resolvedRouteIds`` adds what a snapshot of data alone cannot say: which of
 * the registered routes the app can actually render. A route without a
 * component is skipped by the router, so it would be a feature that registered
 * and went blank.
 */
export function captureOssComposition(): unknown {
  const registry = useRegistryStore.getState();
  const categories = useCategoryRegistry.getState().injected;

  const slots = Object.fromEntries(
    Object.keys(registry.slots)
      .sort()
      .map((name) => [name, (registry.slots[name] ?? []).map((reg) => reg.id)]),
  );

  return toPlain({
    edition: registry.edition,
    features: registry.features,
    capabilities: registry.capabilities,
    branding: registry.branding,
    desktopRoutes: registry.desktopRoutes,
    resolvedRouteIds: resolveRoutes(registry.desktopRoutes).map(
      (route) => route.id,
    ),
    settingsSections: registry.settingsSections,
    projectPanels: registry.projectPanels,
    services: registry.services,
    navItems: registry.navItems,
    navGroups: registry.navGroups,
    suppressed: registry.suppressed,
    slots,
    categories: Object.fromEntries(
      Object.keys(categories)
        .sort()
        .map((type) => [type, categories[type]]),
    ),
  });
}

function toPlain(value: unknown): unknown {
  return JSON.parse(
    JSON.stringify(value, (_key, entry: unknown) =>
      typeof entry === "function" ? "[fn]" : entry,
    ),
  );
}

/** Slot names something registered into that no host or overlay declares. */
export function undeclaredOssSlots(): string[] {
  return Object.keys(useRegistryStore.getState().slots)
    .filter((name) => !findSlotSpec(name))
    .sort();
}
