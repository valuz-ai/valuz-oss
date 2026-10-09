import type { ComponentType } from "react";
import type {
  DesktopRouteModule,
  NavItemModule,
  Placement,
  SettingsSectionModule,
  ValuzPluginContext,
} from "@valuz/core";

import { registerRouteComponent } from "../routes/route-registry";
import { registerSectionComponent } from "../pages/settings/section-components";

/**
 * Register a page: the route (plain data, in the edition registry) and the
 * component behind it (the app's own contribution), both tied to the plugin's
 * lifetime. The component goes first so a router that re-derives on the route
 * change already finds it.
 */
export function pageRoute(
  ctx: ValuzPluginContext,
  route: DesktopRouteModule,
  Component: ComponentType,
  placement?: Placement,
): void {
  ctx.effect(() => registerRouteComponent(route.id, Component));
  ctx.registry.route(route, placement);
}

/** Register a built-in settings section the same way. */
export function settingsPage(
  ctx: ValuzPluginContext,
  section: SettingsSectionModule,
  Component: ComponentType,
  placement?: Placement,
): void {
  ctx.effect(() => registerSectionComponent(section.id, Component));
  ctx.registry.settingsSection(section, placement);
}

/** Register a sidebar entry. */
export function sidebarItem(
  ctx: ValuzPluginContext,
  item: NavItemModule,
  placement?: Placement,
): void {
  ctx.registry.navItem(item, placement);
}
