import { personalProfile } from "./personal-profile";
import type {
  DesktopRouteModule,
  EditionProfile,
  NavItemModule,
  SettingsSectionModule,
} from "./profile";

/**
 * A populated base for registry and plugin-host tests.
 *
 * The personal profile no longer declares any pages (the OSS plugins in
 * ``@valuz/app`` register them), so tests of the layered registry that need
 * "a base with a settings route, a few sections and nav items" build one
 * here instead of reaching into the app package.
 */
const route = (id: string, path: string): DesktopRouteModule => ({
  id,
  path,
  label: id,
  description: id,
  layout: "project",
  showInNav: id === "settings",
  edition: "personal",
});

const section = (id: string): SettingsSectionModule => ({
  id,
  label: id,
  description: id,
  edition: "personal",
});

const nav = (id: string): NavItemModule => ({
  id,
  label: id,
  href: `/${id}`,
  position: "top",
  edition: "personal",
});

export const fixtureProfile: EditionProfile = {
  ...personalProfile,
  desktopRoutes: [
    route("conversations-home", "/"),
    route("projects", "/projects"),
    route("settings", "/settings"),
    route("about", "/about"),
  ],
  settingsSections: [
    section("general"),
    section("model"),
    section("network"),
    section("about"),
  ],
  navItems: [nav("projects"), nav("knowledge"), nav("settings")],
  projectPanels: [
    { id: "conversations", label: "Conversations", edition: "personal" },
  ],
};
