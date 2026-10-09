import type { ComponentType } from "react";

import type {
  NavItemSpec,
  PluginContext,
  PluginDefinition,
  RouteSpec,
  SettingsSectionSpec,
} from "./types";

/** Define a plugin. ``id`` must equal the ``id`` of ``valuz-plugin.json``. */
export function definePlugin(definition: PluginDefinition): PluginDefinition {
  return definition;
}

/**
 * Use an ordinary component as a slot contribution. Slot components receive
 * the slot's props (``SlotProps<"…">``); this is the identity function that
 * names the intent and keeps the component's own prop type.
 */
export function slotComponent<P extends object>(
  component: ComponentType<P>,
): ComponentType<P> {
  return component;
}

/**
 * Add a page. ``route.path`` must be ``/x/<plugin id>`` or below it. With
 * ``nav: true`` the page also gets a sidebar entry.
 */
export function pageRoute(
  ctx: PluginContext,
  route: RouteSpec,
  component: ComponentType,
  options: { nav?: boolean } = {},
): void {
  ctx.registry.route(route, component);
  if (options.nav) {
    ctx.registry.navItem({
      id: route.id,
      label: route.title,
      icon: route.icon,
      path: route.path,
    });
  }
}

/** Add a settings section under the Plugins group. */
export function settingsPage(
  ctx: PluginContext,
  section: SettingsSectionSpec,
  component: ComponentType,
): void {
  ctx.registry.settingsSection(section, component);
}

/** Add a sidebar entry that points at one of the plugin's own pages. */
export function sidebarItem(ctx: PluginContext, item: NavItemSpec): void {
  ctx.registry.navItem(item);
}
