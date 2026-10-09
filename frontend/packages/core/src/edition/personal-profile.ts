import { DEFAULT_CAPABILITIES } from "./capabilities";
import type {
  BrandingProfile,
  EditionProfile,
  FeatureFlags,
  ServiceDescriptor,
} from "./profile";

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

/**
 * The personal edition's STATIC base: identity, feature flags, branding, the
 * default capabilities and the one service the shell cannot boot without.
 *
 * It declares no pages. Routes, settings sections, sidebar items and project
 * panels are registered by the OSS plugins (``@valuz/app`` ``ossPlugins``) as
 * layers over this base, so each feature can be switched off on its own and
 * hydrating the registry from a profile never wipes them. A registry that has
 * only been hydrated therefore has empty page lists until the plugins load —
 * hosts load them before mounting (see ``loadOssPlugins``).
 */
export const personalProfile: EditionProfile = {
  edition: "personal",
  features: personalFeatures,
  services: personalServices,
  desktopRoutes: [],
  settingsSections: [],
  projectPanels: [],
  branding: personalBranding,
  navItems: [],
  capabilities: DEFAULT_CAPABILITIES,
};
