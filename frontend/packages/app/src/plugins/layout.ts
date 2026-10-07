import type { Placement } from "@valuz/core";

/**
 * Where each OSS page sits in the shared registries.
 *
 * The OSS entries are registered by several plugins, but they must read as one
 * list: the order of routes, settings sections and sidebar items is part of
 * what the user sees (and what the composition snapshots pin). These tables
 * ARE that order. Each entry is placed relative to the nearest entry of its
 * table that is actually loaded, so neither the order plugins load in nor a
 * plugin that is off (backend disabled, failed) disturbs the rest.
 *
 * Overlays anchor on these ids too (the commercial layout positions its pages
 * "after the last OSS route"), so a rename here is a contract change.
 */

export const OSS_ROUTE_ORDER = [
  "conversations-home",
  "conversation-detail",
  "projects",
  "project-detail",
  "knowledge",
  "agents",
  "agent-detail",
  "task-detail",
  "skills",
  "marketplace",
  "skill-detail",
  "connectors",
  "plugins",
  "settings",
  "component-gallery",
  "onboarding",
  "first-launch",
  "api-key-config",
  "tool-calls",
  "context-panel",
  "overlays",
  "playbooks",
  "playbook-detail",
  "automation",
  "automation-detail",
  "activity",
] as const;

export const OSS_SETTINGS_SECTION_ORDER = [
  "general",
  "personalization",
  "model",
  "browser",
  "parsing",
  "backup",
  "network",
  "plugins",
  "system-logs",
  "about",
] as const;

export const OSS_NAV_ITEM_ORDER = [
  "scheduled",
  "activity",
  "agents",
  "plugins",
  "knowledge",
  "settings",
] as const;

export type OssRouteId = (typeof OSS_ROUTE_ORDER)[number];
export type OssSettingsSectionId = (typeof OSS_SETTINGS_SECTION_ORDER)[number];
export type OssNavItemId = (typeof OSS_NAV_ITEM_ORDER)[number];

/**
 * After the nearest loaded predecessor in ``order``; failing that, before the
 * nearest loaded successor.
 *
 * The registry resolves a placement whose anchors are all missing by letting
 * the entry wait, so the FIRST entry of a list has to be registered without a
 * placement to start the chain — ``oss-core`` does that for every list, and it
 * loads first.
 */
function placementIn(order: readonly string[], id: string): Placement {
  const at = order.indexOf(id);
  if (at === -1) {
    throw new Error(`"${id}" is not in the OSS layout table`);
  }
  return {
    after: order.slice(0, at).reverse(),
    before: order.slice(at + 1),
  };
}

export const routePlacement = (id: OssRouteId): Placement =>
  placementIn(OSS_ROUTE_ORDER, id);

export const settingsSectionPlacement = (id: OssSettingsSectionId): Placement =>
  placementIn(OSS_SETTINGS_SECTION_ORDER, id);

export const navItemPlacement = (id: OssNavItemId): Placement =>
  placementIn(OSS_NAV_ITEM_ORDER, id);
