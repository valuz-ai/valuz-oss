import { useEffect, useState, type ComponentType } from "react";
import {
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  type RouteObject,
} from "react-router-dom";
import { providersApi } from "@valuz/core";
import { PageLoader } from "@valuz/ui";
import type { ResolvedRoute } from "./route-registry";
import { isOnboarded } from "../lib/onboarding";

const SETUP_BYPASS_PATHS = [
  "/welcome",
  "/onboarding",
  "/first-launch",
  "/auth/",
  "/api-key-config",
];

export function useAppSetupReady() {
  const navigate = useNavigate();
  const location = useLocation();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (checked) return;

    const isStandalone = SETUP_BYPASS_PATHS.some(
      (path) =>
        location.pathname.startsWith(path) || location.pathname === path,
    );
    if (isStandalone) {
      setChecked(true);
      return;
    }

    // The user already completed setup on /welcome (persisted marker). Skip
    // the gate entirely — don't bounce back to /welcome on refresh. This is
    // also the source of truth for subscription logins whose credential lives
    // in the CLI keychain and can't be seen from the providers API here.
    if (isOnboarded()) {
      setChecked(true);
      return;
    }

    let cancelled = false;

    const checkSetup = async () => {
      try {
        const { providers } = await providersApi.list();
        if (cancelled) return;
        const hasProvider = providers.some(
          (provider) =>
            provider.enabled && provider.credential_source !== "none",
        );
        if (!hasProvider) {
          navigate("/welcome", { replace: true });
        }
      } catch {
        // Backend may still be starting; routed pages surface API errors.
      }

      if (!cancelled) setChecked(true);
    };

    void checkSetup();

    return () => {
      cancelled = true;
    };
  }, [checked, location.pathname, navigate]);

  return checked;
}

export const AppSetupRoot = () => {
  const ready = useAppSetupReady();
  return ready ? <Outlet /> : <PageLoader logo className="h-screen" />;
};

export type AppRouteOverrides = Record<string, ComponentType>;
export type ExtraAppRoute = ResolvedRoute | RouteObject;

const withRouteOverride = (
  route: ResolvedRoute,
  routeOverrides?: AppRouteOverrides,
): ResolvedRoute => {
  const Component = routeOverrides?.[route.path] ?? routeOverrides?.[route.id];
  return Component ? { ...route, Component } : route;
};

const isResolvedRoute = (route: ExtraAppRoute): route is ResolvedRoute =>
  "layout" in route && "Component" in route;

const toProjectChild = (
  route: ResolvedRoute,
  routeOverrides?: AppRouteOverrides,
): RouteObject => {
  const resolved = withRouteOverride(route, routeOverrides);
  const Component = resolved.Component;
  if (route.path === "/") {
    return {
      index: true,
      element: <Navigate to="/conversation/new" replace />,
    };
  }
  return { path: route.path.replace(/^\//, ""), element: <Component /> };
};

const toStandaloneRoute = (
  route: ResolvedRoute,
  routeOverrides?: AppRouteOverrides,
): RouteObject => {
  const resolved = withRouteOverride(route, routeOverrides);
  const Component = resolved.Component;
  return { path: route.path, element: <Component /> };
};

export interface CreateAppRouteObjectsOptions {
  routes?: ResolvedRoute[];
  resolved?: ResolvedRoute[];
  routeOverrides?: AppRouteOverrides;
  extraRoutes?: ExtraAppRoute[];
  Root: ComponentType;
  layout?: ComponentType;
  ProjectLayout?: ComponentType;
  fallbackPath?: string;
}

export function createAppRouteObjects({
  routes,
  resolved,
  routeOverrides,
  extraRoutes = [],
  Root,
  layout,
  ProjectLayout,
  fallbackPath = "/",
}: CreateAppRouteObjectsOptions): RouteObject[] {
  const Layout = layout ?? ProjectLayout;
  if (!Layout) {
    throw new Error("createAppRouteObjects requires a layout component");
  }

  const extraResolvedRoutes = extraRoutes.filter(isResolvedRoute);
  const extraRouteObjects = extraRoutes.filter(
    (route): route is RouteObject => !isResolvedRoute(route),
  );
  const appRoutes = [...(routes ?? resolved ?? []), ...extraResolvedRoutes];

  const projectChildren = appRoutes
    .filter((route) => route.layout === "project")
    .map((route) => toProjectChild(route, routeOverrides));
  const standaloneRoutes = appRoutes
    .filter((route) => route.layout === "standalone")
    .map((route) => toStandaloneRoute(route, routeOverrides));

  return [
    {
      element: <Root />,
      children: [
        {
          path: "/",
          element: <Layout />,
          children: projectChildren,
        },
        ...standaloneRoutes,
        ...extraRouteObjects,
        {
          path: "*",
          element: <Navigate to={fallbackPath} replace />,
        },
      ],
    },
  ];
}

export interface CreateAppRouterOptions<
  TRouter,
> extends CreateAppRouteObjectsOptions {
  createRouter: (routes: RouteObject[]) => TRouter;
}

export function createAppRouter<TRouter>({
  createRouter,
  ...options
}: CreateAppRouterOptions<TRouter>): TRouter {
  return createRouter(createAppRouteObjects(options));
}
