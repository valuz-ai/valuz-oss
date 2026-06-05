import type { ComponentType } from "react";

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

export type DesktopRouteLayout = "workspace" | "standalone";

export interface DesktopRouteModule {
  id: string;
  path: string;
  label: string;
  description: string;
  /** Which layout shell wraps this route. Workspace routes nest inside DesktopWorkspaceLayout. */
  layout: DesktopRouteLayout;
  /** Whether the route appears in the workspace sidebar nav. */
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
  /** Optional content component for overlay/plugin sections. Built-in tabs use dedicated rendering. */
  component?: ComponentType;
  edition: Edition;
}

export interface WorkspacePanelModule {
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
   * ``workspace`` items sit at the top (Assistant, Automation), ``library``
   * items sit under the Library section, ``settings`` is bottom-pinned.
   * Defaults to ``workspace`` when omitted.
   */
  navGroup?: "workspace" | "library" | "settings";
  edition: Edition;
}

export interface EditionProfile {
  edition: Edition;
  features: FeatureFlags;
  services: ServiceDescriptor[];
  desktopRoutes: DesktopRouteModule[];
  settingsSections: SettingsSectionModule[];
  workspacePanels: WorkspacePanelModule[];
  branding: BrandingProfile;
  navItems: NavItemModule[];
}
