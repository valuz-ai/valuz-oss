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
  getResolvedDesktopRoutes,
  resolveRoutes,
  type ResolvedRoute,
} from "@valuz/app/routes";
import { KnowledgePage, ProjectsPage } from "@valuz/app/pages";
import { WebProjectLayout } from "./project-layout";

const WebProjectsPage = () => <ProjectsPage directoryFieldMode="managed" />;
const WebKnowledgePage = () => <KnowledgePage directoryFieldMode="managed" />;
const routeOverrides = {
  projects: WebProjectsPage,
  knowledge: WebKnowledgePage,
};

/**
 * Route objects for the routes the registry holds now (the OSS plugins and an
 * overlay register them — there is no static list).
 */
export const buildRouteObjects = (
  resolved: ResolvedRoute[] = getResolvedDesktopRoutes(),
): RouteObject[] =>
  createAppRouteObjects({
    routes: resolved,
    Root: AppSetupRoot,
    layout: WebProjectLayout,
    routeOverrides,
  });

export const AppRouter = () => {
  const desktopRoutes = useRegistryStore((state) => state.desktopRoutes);
  const runtimeRouter = useMemo(
    () =>
      createBrowserRouter(
        createAppRouteObjects({
          routes: resolveRoutes(desktopRoutes),
          Root: AppSetupRoot,
          layout: WebProjectLayout,
          routeOverrides,
        }),
      ),
    [desktopRoutes],
  );
  return <RouterProvider router={runtimeRouter} />;
};
