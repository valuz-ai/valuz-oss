import type { ComponentType } from "react";

import type { Capabilities } from "./capabilities";

// Edition 类型的单点定义在 @valuz/shared/constants/editions.ts；这里只 re-export 以便
// 同模块的其他 interface（FeatureFlags / EditionProfile）继续在 profile.ts 内自洽引用。
export type { Edition } from "@valuz/shared";
import type { Edition } from "@valuz/shared";

// 公共骨架只声明个人版关心的 feature key。
// 未来若实际引入 enterprise overlay，由 overlay 通过 declaration merging
// 增补自己的 key（参见 reportify-prd FRONTEND-ARCH-ELECTRON.md §4.3 模式），
// 公共仓 source tree 永远不会出现具体企业 feature 字面量。
export interface FeatureFlags {
  conversation: boolean;
  projects: boolean;
  skills: boolean;
  knowledge: boolean;
  settings: boolean;
  onboarding: boolean;
}

// 不带 edition——Slice 3 删掉企业概念后，公共骨架的 service 不需要按 edition 分类。
// Slice 6 会与 apps/desktop/src/main/services/descriptors.ts 的
// DesktopServiceDescriptor 合并为单一类型。
export interface ServiceDescriptor {
  name: string;
  defaultPort: number;
  requiredForBoot: boolean;
}

export type DesktopRouteLayout = "project" | "standalone";
export type DesktopRoutePresentation = "page" | "overlay";

export interface DesktopRouteModule {
  id: string;
  path: string;
  label: string;
  description: string;
  /** Which layout shell wraps this route. Project routes nest inside DesktopProjectLayout. */
  layout: DesktopRouteLayout;
  /**
   * How a project-layout route is presented.
   *
   * `overlay` keeps the current page mounted underneath the destination, so
   * transient detail readers can close back without losing page state,
   * loaded data, or nested scroll positions. A direct deep link has no
   * background page and therefore renders as a normal full page.
   */
  presentation?: DesktopRoutePresentation;
  /** Whether the route appears in the project sidebar nav. */
  showInNav: boolean;
  /** Optional inline component. If omitted, the app resolves via its local COMPONENT_MAP by id. */
  component?: ComponentType;
  edition: Edition;
}

export interface SettingsSectionModule {
  id: string;
  /** i18n key for sidebar label */
  label: string;
  /** i18n key for sidebar description */
  description: string;
  /** Icon identifier — mapped to Lucide component in SettingsPage */
  icon?: string;
  /**
   * Optional sidebar group. Groups are rendered in first-appearance order, so
   * an edition can add a section without changing a central settings enum.
   * ``label`` is an i18n key, consistent with the section label/description.
   */
  group?: {
    id: string;
    label: string;
  };
  /** Optional content component for overlay/plugin sections. Built-in tabs use dedicated rendering. */
  component?: ComponentType;
  edition: Edition;
}

export interface ProjectPanelModule {
  id: string;
  label: string;
  edition: Edition;
}

export interface BrandingProfile {
  appName: string;
  logoPath?: string;
  brandColor?: string;
  brandColorHover?: string;
  brandColorLight?: string;
}

export interface NavItemModule {
  id: string;
  label: string;
  href: string;
  position: "top" | "bottom";
  /**
   * Sidebar group the item renders in (v2 IA — PRD-NEXT §3.4):
   * ``main`` items sit at the top (Assistant, Automation), ``library``
   * items sit under the Library section, ``settings`` is bottom-pinned.
   * Any other string refers to a custom group declared in
   * ``EditionProfile.navGroups`` — rendered as a labeled section between
   * the top main area and the project list. Defaults to ``main``.
   */
  navGroup?: "main" | "library" | "settings" | (string & {});
  /**
   * Sidebar icon id (key of DesktopSidebar's icon map, e.g. "star").
   * When omitted the app shell falls back to its per-item-id map, then to
   * a generic gear icon.
   */
  icon?: string;
  /**
   * Extra route prefixes that keep this item highlighted, for pages reached
   * through the item's own page rather than the sidebar (e.g. 自动化 stays
   * active on ``/playbooks``, which its title switch opens).
   */
  activePaths?: string[];
  edition: Edition;
}

/**
 * A custom labeled sidebar group declared by an edition/plugin (e.g. a
 * finance edition's "市场" group holding 关注/发现). Rendered with the same
 * section-heading treatment as the built-in Library group; nav items opt in
 * via ``NavItemModule.navGroup === id``.
 */
export interface NavGroupModule {
  id: string;
  /** i18n key for the group heading. */
  label: string;
}

export interface EditionProfile {
  edition: Edition;
  features: FeatureFlags;
  services: ServiceDescriptor[];
  desktopRoutes: DesktopRouteModule[];
  settingsSections: SettingsSectionModule[];
  projectPanels: ProjectPanelModule[];
  branding: BrandingProfile;
  navItems: NavItemModule[];
  /** Custom labeled sidebar groups (order = render order). Optional. */
  navGroups?: NavGroupModule[];
  capabilities: Capabilities;
}
