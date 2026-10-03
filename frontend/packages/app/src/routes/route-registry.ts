import type { ComponentType } from "react";
import {
  useRegistryStore,
  type DesktopRouteModule,
} from "@valuz/core";

import { createContributions } from "../lib/contributions";

/**
 * The component behind each route id.
 *
 * A route in the edition registry is plain data (id, path, label, layout …) —
 * what a composition snapshot compares across editions. The page it renders is
 * contributed by the plugin that owns the route, through
 * {@link registerRouteComponent}, and withdrawn with it. A route that carries
 * its own ``component`` (overlay and edition routes) never consults this.
 */
const routeComponents = createContributions<ComponentType>();

/**
 * Register the component for route ``id``; the returned disposer withdraws it.
 * Plugins call this inside ``ctx.effect`` right before registering the route.
 */
export const registerRouteComponent = (
  id: string,
  Component: ComponentType,
): (() => void) => routeComponents.register(id, Component);

export interface ResolvedRoute extends DesktopRouteModule {
  Component: ComponentType;
}

const resolveComponent = (route: DesktopRouteModule): ComponentType | null =>
  route.component ?? routeComponents.get(route.id) ?? null;

export const resolveRoutes = (modules: DesktopRouteModule[]): ResolvedRoute[] =>
  modules.flatMap((route) => {
    const Component = resolveComponent(route);
    if (!Component) {
      if (typeof console !== "undefined") {
        console.warn(
          `[routes] No component registered for "${route.id}" (${route.path}); route skipped.`,
        );
      }
      return [];
    }
    return [{ ...route, Component }];
  });

/**
 * The routes the registry holds right now, resolved to components. A snapshot:
 * routes come and go as plugins load and unload, so callers that must follow
 * those changes subscribe to ``useRegistryStore`` and call ``resolveRoutes``
 * themselves (the apps' ``AppRouter`` does).
 */
export const getResolvedDesktopRoutes = (): ResolvedRoute[] =>
  resolveRoutes(useRegistryStore.getState().desktopRoutes);
