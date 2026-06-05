import { useMemo } from "react";
import {
  RouterProvider,
  createBrowserRouter,
  type RouteObject,
} from "react-router-dom";
import { useRegistryStore } from "@valuz/core";
import {
  AppSetupRoot,
  createAppRouteObjects,
  createAppRouter,
  resolveRoutes,
  resolvedDesktopRoutes,
  type ResolvedRoute,
} from "@valuz/app/routes";
import { WebWorkspaceLayout } from "./workspace-layout";

export const buildRouteObjects = (
  resolved: ResolvedRoute[] = resolvedDesktopRoutes,
): RouteObject[] =>
  createAppRouteObjects({
    routes: resolved,
    Root: AppSetupRoot,
    layout: WebWorkspaceLayout,
  });

export const routes: RouteObject[] = buildRouteObjects();
export const router = createAppRouter({
  createRouter: createBrowserRouter,
  routes: resolvedDesktopRoutes,
  Root: AppSetupRoot,
  layout: WebWorkspaceLayout,
});

export const AppRouter = () => {
  const desktopRoutes = useRegistryStore((state) => state.desktopRoutes);
  const runtimeRouter = useMemo(
    () =>
      createBrowserRouter(
        createAppRouteObjects({
          routes: resolveRoutes(desktopRoutes),
          Root: AppSetupRoot,
          layout: WebWorkspaceLayout,
        }),
      ),
    [desktopRoutes],
  );
  return <RouterProvider router={runtimeRouter} />;
};
