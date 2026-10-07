import { lazy } from "react";
import { definePlugin } from "@valuz/core";

import {
  ApiKeyConfigPage,
  ContextPanelPage,
  ConversationPage,
  ConversationsHomePage,
  OnboardingFlow,
  OnboardingPage,
  OverlaysPage,
  ProjectDetailPage,
  ProjectsPage,
  SettingsPage,
  ToolCallsPage,
} from "../pages";
import { AboutSection } from "../pages/settings/AboutSection";
import { PluginSettingsSection } from "../pages/settings/PluginSettingsSection";
import { GeneralSection } from "../pages/settings/GeneralSection";
import { ModelSection } from "../pages/settings/ModelSection";
import { NetworkSection } from "../pages/settings/NetworkSection";
import { SystemLogsSettingsSection } from "../pages/settings/SystemLogsSection";
import { pageRoute, settingsPage, sidebarItem } from "./define";
import { routePlacement, settingsSectionPlacement } from "./layout";

const A2UIGalleryPage = lazy(() =>
  import("../pages/A2UIGalleryPage").then(({ A2UIGalleryPage }) => ({
    default: A2UIGalleryPage,
  })),
);

/**
 * The OSS app itself: conversations, projects, the settings shell with the
 * model / network / plugins / logs / about panels, first-run onboarding and
 * the developer galleries. Required — without it there is nothing to open.
 *
 * Loads first, and starts every registry list with an UNPLACED entry (the
 * first route, the first settings section, the settings nav item): the layered
 * registry places a new entry relative to entries already in the list, so the
 * chain every other OSS plugin hangs its pages on begins here.
 *
 * Embedded surfaces with no registry entry of their own — notifications,
 * citations, feedback and the IM-channel bindings inside the agent page — stay
 * part of the pages that render them, i.e. of this plugin.
 */
export const ossCorePlugin = definePlugin({
  id: "oss-core",
  apply(ctx) {
    // ── Routes ──────────────────────────────────────────────────────────
    pageRoute(
      ctx,
      {
        id: "conversations-home",
        path: "/",
        label: "nav.home",
        description: "Recent chats, drafts, and local session entrypoints.",
        layout: "project",
        showInNav: true,
        edition: "personal",
      },
      ConversationsHomePage,
    );
    pageRoute(
      ctx,
      {
        id: "conversation-detail",
        path: "/conversation/:id",
        label: "nav.chat",
        description: "Active conversation with tool calls and streaming output.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      ConversationPage,
      routePlacement("conversation-detail"),
    );
    pageRoute(
      ctx,
      {
        id: "projects",
        path: "/projects",
        label: "sidebar.projects",
        description:
          "Project lists with files, notes, and scoped agent context.",
        layout: "project",
        showInNav: true,
        edition: "personal",
      },
      ProjectsPage,
      routePlacement("projects"),
    );
    pageRoute(
      ctx,
      {
        id: "project-detail",
        path: "/projects/:id",
        label: "route.projectDetail",
        description:
          "Files, knowledge, and conversations scoped to one project.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      ProjectDetailPage,
      routePlacement("project-detail"),
    );
    pageRoute(
      ctx,
      {
        id: "settings",
        path: "/settings",
        label: "nav.settings",
        description: "Models, credentials, and local desktop preferences.",
        layout: "project",
        showInNav: true,
        edition: "personal",
      },
      SettingsPage,
      routePlacement("settings"),
    );
    pageRoute(
      ctx,
      {
        id: "component-gallery",
        path: "/developer/components",
        label: "route.componentGallery",
        description:
          "Shared component catalog with distribution extension groups.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      A2UIGalleryPage,
      routePlacement("component-gallery"),
    );
    pageRoute(
      ctx,
      {
        id: "onboarding",
        path: "/onboarding",
        label: "route.onboarding",
        description: "First-run experience for new desktop users.",
        layout: "standalone",
        showInNav: false,
        edition: "personal",
      },
      OnboardingPage,
      routePlacement("onboarding"),
    );
    // /welcome — the first-run entry. The full-screen editorial flow
    // (OnboardingFlow) owns welcome → connect (paste API key / CLI login) →
    // team. There is no hosted-account OAuth path.
    pageRoute(
      ctx,
      {
        id: "first-launch",
        path: "/welcome",
        label: "onboarding.welcome",
        description: "Connection selector for first-time setup.",
        layout: "standalone",
        showInNav: false,
        edition: "personal",
      },
      OnboardingFlow,
      routePlacement("first-launch"),
    );
    pageRoute(
      ctx,
      {
        id: "api-key-config",
        path: "/auth/api-key",
        label: "route.apiKeyConfig",
        description: "Manual API key configuration.",
        layout: "standalone",
        showInNav: false,
        edition: "personal",
      },
      ApiKeyConfigPage,
      routePlacement("api-key-config"),
    );
    pageRoute(
      ctx,
      {
        id: "tool-calls",
        path: "/tool-calls",
        label: "route.toolCalls",
        description: "Prototype gallery for tool invocation states.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      ToolCallsPage,
      routePlacement("tool-calls"),
    );
    pageRoute(
      ctx,
      {
        id: "context-panel",
        path: "/context-panel",
        label: "route.contextPanel",
        description: "Right-rail examples for chat and project context.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      ContextPanelPage,
      routePlacement("context-panel"),
    );
    pageRoute(
      ctx,
      {
        id: "overlays",
        path: "/overlays",
        label: "route.overlays",
        description: "Prototype command palette and confirmation surfaces.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      OverlaysPage,
      routePlacement("overlays"),
    );

    // ── Settings sections ───────────────────────────────────────────────
    settingsPage(
      ctx,
      {
        id: "general",
        label: "settings.tab.general.label",
        description: "settings.tab.general.desc",
        icon: "palette",
        group: { id: "personal", label: "settings.group.personal" },
        edition: "personal",
      },
      GeneralSection,
    );
    settingsPage(
      ctx,
      {
        id: "model",
        label: "settings.tab.model.label",
        description: "settings.tab.model.desc",
        icon: "cpu",
        group: { id: "runtime", label: "settings.group.runtime" },
        edition: "personal",
      },
      ModelSection,
      settingsSectionPlacement("model"),
    );
    settingsPage(
      ctx,
      {
        id: "network",
        label: "settings.tab.network.label",
        description: "settings.tab.network.desc",
        icon: "network",
        group: { id: "system", label: "settings.group.system" },
        edition: "personal",
      },
      NetworkSection,
      settingsSectionPlacement("network"),
    );
    settingsPage(
      ctx,
      {
        id: "plugins",
        label: "pluginSettings.title",
        description: "pluginSettings.navDesc",
        icon: "puzzle",
        group: { id: "personal", label: "settings.group.personal" },
        edition: "personal",
      },
      PluginSettingsSection,
      settingsSectionPlacement("plugins"),
    );
    settingsPage(
      ctx,
      {
        id: "system-logs",
        label: "settings.tab.systemLogs.label",
        description: "settings.tab.systemLogs.desc",
        icon: "activity",
        group: { id: "system", label: "settings.group.system" },
        edition: "personal",
      },
      SystemLogsSettingsSection,
      settingsSectionPlacement("system-logs"),
    );
    settingsPage(
      ctx,
      {
        id: "about",
        label: "settings.tab.about.label",
        description: "settings.tab.about.desc",
        icon: "info",
        group: { id: "system", label: "settings.group.system" },
        edition: "personal",
      },
      AboutSection,
      settingsSectionPlacement("about"),
    );

    // ── Sidebar ─────────────────────────────────────────────────────────
    // Unplaced: it starts the nav list; the optional plugins' entries go
    // before / after it (see ./layout).
    sidebarItem(ctx, {
      id: "settings",
      label: "nav.settings",
      href: "/settings",
      position: "bottom",
      navGroup: "settings",
      edition: "personal",
    });

    // ── Project panels ──────────────────────────────────────────────────
    ctx.registry.projectPanel({
      id: "conversations",
      label: "Conversations",
      edition: "personal",
    });
    ctx.registry.projectPanel({
      id: "projects",
      label: "Projects",
      edition: "personal",
    });
  },
});
