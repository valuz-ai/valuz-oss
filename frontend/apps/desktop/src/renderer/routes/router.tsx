import { useEffect, useMemo } from "react";
import {
  Outlet,
  RouterProvider,
  createHashRouter,
  type RouteObject,
} from "react-router-dom";
import { useRegistryStore } from "@valuz/core";
import { DesktopWorkspaceLayout } from "../layouts/DesktopWorkspaceLayout";
import { PageLoader } from "@valuz/ui";
import {
  createAppRouteObjects,
  createAppRouter,
  resolveRoutes,
  resolvedDesktopRoutes,
  useAppSetupReady,
  type ResolvedRoute,
} from "./route-registry";

/**
 * Root layout that:
 * 1. On first load, checks if a credentialed model provider exists.
 *    If not, redirects to /welcome for initial setup.
 * 2. Listens for valuz-oss:// deep-link events.
 */
const DeepLinkRoot = () => {
  const setupReady = useAppSetupReady();

  // Deep-link listener
  useEffect(() => {
    const desktopApi = (
      window as Window & {
        valuzDesktop?: {
          on: (event: string, handler: (payload: unknown) => void) => void;
          off: (event: string, handler: (payload: unknown) => void) => void;
        };
      }
    ).valuzDesktop;
    if (!desktopApi) {
      console.log(
        "[DeepLink] no desktopApi available, window.valuzDesktop =",
        (window as any).valuzDesktop,
        "keys:",
        Object.keys(window).filter(
          (k) =>
            k.toLowerCase().includes("valuz") ||
            k.toLowerCase().includes("desktop"),
        ),
      );
      return;
    }

    console.log("[DeepLink] registering deep-link listener");

    const onDeepLink = (payload: unknown) => {
      // Generic deep-link dispatch. Branch on the parsed ``host``.
      console.log("[DeepLink] received:", payload);
      const parsed = payload as { host?: string; search?: string } | null;
      if (!parsed || typeof parsed !== "object") return;

      // Connector / MCP OAuth callback
      // (``valuz-oss://connector-oauth?ok=1&connector_id=…&slug=…`` or
      // ``…?ok=0&error=…``). The authorize page opens in the system browser
      // (the shell denies in-app ``window.open``), so it has no ``window.opener``
      // to message back — the backend callback hands the result here instead.
      // Re-emit it as the same window message the ConnectorsSection fast-path
      // already listens for (refresh + toast), bridging deep link → handler.
      if (parsed.host === "connector-oauth") {
        const sp = new URLSearchParams(parsed.search ?? "");
        const ok = sp.get("ok") === "1";
        window.postMessage(
          {
            type: ok ? "connector_oauth_success" : "connector_oauth_error",
            connector_id: sp.get("connector_id"),
            slug: sp.get("slug"),
            error: sp.get("error"),
          },
          "*",
        );
      }
    };

    desktopApi.on("deep-link-received", onDeepLink);
    console.log("[DeepLink] listener registered");
    return () => {
      desktopApi.off("deep-link-received", onDeepLink);
    };
  }, []);

  // Keep showing the splash visual while the setup check (providers.list) is
  // in flight. Returning ``null`` here used to leave the screen blank for the
  // duration of that call — perceived by users as a long white flash right
  // after the boot StartupScreen unmounted.
  if (!setupReady) {
    return <PageLoader logo className="h-screen" />;
  }

  return <Outlet />;
};

const buildRouteObjects = (resolved: ResolvedRoute[]): RouteObject[] =>
  createAppRouteObjects({
    routes: resolved,
    Root: DeepLinkRoot,
    layout: DesktopWorkspaceLayout,
  });

/**
 * Static route snapshot built at module load from the build-time profile.
 * Used by tests (router.test.tsx) and any non-reactive consumer.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const routes: RouteObject[] = buildRouteObjects(resolvedDesktopRoutes);
// eslint-disable-next-line react-refresh/only-export-components
export const router = createAppRouter({
  createRouter: createHashRouter,
  routes: resolvedDesktopRoutes,
  Root: DeepLinkRoot,
  layout: DesktopWorkspaceLayout,
});

/**
 * Reactive router. Subscribes to the runtime registry store so that plugins
 * registering new routes (or edition hot-swap) take effect without an app
 * reload. Note: `createHashRouter` is recreated when the route list
 * changes, which resets navigation state — acceptable for plugin load
 * events, which are rare.
 */
export const AppRouter = () => {
  const desktopRoutes = useRegistryStore((state) => state.desktopRoutes);
  const runtimeRouter = useMemo(
    () => createHashRouter(buildRouteObjects(resolveRoutes(desktopRoutes))),
    [desktopRoutes],
  );
  return <RouterProvider router={runtimeRouter} />;
};
