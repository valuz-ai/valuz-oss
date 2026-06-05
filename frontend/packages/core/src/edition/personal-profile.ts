import type {
  BrandingProfile,
  EditionProfile,
  FeatureFlags,
  NavItemModule,
  ServiceDescriptor,
} from "./profile";
import {
  personalDesktopRoutes,
  personalSettingsSections,
  personalWorkspacePanels,
} from "./registries";

const personalFeatures: FeatureFlags = {
  conversation: true,
  projects: true,
  skills: true,
  knowledge: true,
  settings: true,
  onboarding: true,
};

const personalServices: ServiceDescriptor[] = [
  {
    name: "agent-server",
    defaultPort: 19100,
    requiredForBoot: true,
  },
];

const personalBranding: BrandingProfile = {
  appName: "Valuz Agent",
};

// v2 IA (PRD-NEXT §3.4): Workspace (Assistant, Automation) → Projects →
// Library (Agents, Skills, Connectors, Knowledge) → Settings. Tasks are no
// longer a top-level entry — they live inside a Project.
const personalNavItems: NavItemModule[] = [
  {
    // Sidebar entry points at the new ADR-021 automation page. The
    // sentinel id stays ``scheduled`` for now so the navigation tests'
    // hard-coded ids keep passing — the visible href + label both
    // moved to the new automation flow.
    id: "scheduled",
    label: "sidebar.automation",
    href: "/automations",
    position: "top",
    navGroup: "workspace",
    edition: "personal",
  },
  {
    id: "activity",
    label: "nav.activity",
    href: "/activity",
    position: "top",
    navGroup: "workspace",
    edition: "personal",
  },
  {
    id: "agents",
    label: "nav.agents",
    href: "/agents",
    position: "bottom",
    navGroup: "library",
    edition: "personal",
  },
  {
    id: "skills",
    label: "sidebar.skills",
    href: "/skills",
    position: "bottom",
    navGroup: "library",
    edition: "personal",
  },
  {
    id: "connectors",
    label: "nav.connectors",
    href: "/connectors",
    position: "bottom",
    navGroup: "library",
    edition: "personal",
  },
  {
    id: "knowledge",
    label: "nav.knowledge",
    href: "/knowledge",
    position: "bottom",
    navGroup: "library",
    edition: "personal",
  },
  {
    id: "settings",
    label: "nav.settings",
    href: "/settings",
    position: "bottom",
    navGroup: "settings",
    edition: "personal",
  },
];

export const personalProfile: EditionProfile = {
  edition: "personal",
  features: personalFeatures,
  services: personalServices,
  desktopRoutes: personalDesktopRoutes,
  settingsSections: personalSettingsSections,
  workspacePanels: personalWorkspacePanels,
  branding: personalBranding,
  navItems: personalNavItems,
};
