import { create } from "zustand";
import type { Edition, EditionProfile, FeatureFlags } from "./profile";
import type {
  BrandingProfile,
  DesktopRouteModule,
  NavItemModule,
  ServiceDescriptor,
  SettingsSectionModule,
  WorkspacePanelModule,
} from "./profile";
import type { SlotMap, SlotRegistration } from "./registries/slots";
import { personalProfile } from "./personal-profile";
import { getActiveProfile } from "./resolve";

/**
 * Mutable registry — the single source of truth consumed by the app shell.
 * Seeded from the build-time EditionProfile, but routes, sections, panels,
 * and services can be appended/removed at runtime by plugins or code.
 */
interface RegistryState {
  edition: Edition;
  features: FeatureFlags;
  desktopRoutes: DesktopRouteModule[];
  settingsSections: SettingsSectionModule[];
  workspacePanels: WorkspacePanelModule[];
  services: ServiceDescriptor[];
  branding: BrandingProfile;
  navItems: NavItemModule[];
  slots: SlotMap;

  setEdition: (edition: Edition) => void;
  hydrate: (profile: EditionProfile) => void;

  registerRoute: (route: DesktopRouteModule) => () => void;
  unregisterRoute: (id: string) => void;

  registerSettingsSection: (section: SettingsSectionModule) => () => void;
  unregisterSettingsSection: (id: string) => void;

  registerWorkspacePanel: (panel: WorkspacePanelModule) => () => void;
  unregisterWorkspacePanel: (id: string) => void;

  registerService: (descriptor: ServiceDescriptor) => () => void;
  unregisterService: (name: string) => void;

  registerNavItem: (item: NavItemModule) => () => void;
  unregisterNavItem: (id: string) => void;

  registerSlot: (name: string, registration: SlotRegistration) => () => void;
  unregisterSlot: (name: string, id: string) => void;
}

const seed = getActiveProfile();

const upsertById = <T extends { id: string }>(list: T[], item: T): T[] => {
  const existing = list.findIndex((entry) => entry.id === item.id);
  if (existing === -1) {
    return [...list, item];
  }
  const next = list.slice();
  next[existing] = item;
  return next;
};

const upsertByName = <T extends { name: string }>(list: T[], item: T): T[] => {
  const existing = list.findIndex((entry) => entry.name === item.name);
  if (existing === -1) {
    return [...list, item];
  }
  const next = list.slice();
  next[existing] = item;
  return next;
};

export const useRegistryStore = create<RegistryState>((set) => ({
  edition: seed.edition,
  features: seed.features,
  desktopRoutes: [...seed.desktopRoutes],
  settingsSections: [...seed.settingsSections],
  workspacePanels: [...seed.workspacePanels],
  services: [...seed.services],
  branding: seed.branding,
  navItems: [...seed.navItems],
  slots: {},

  // setEdition 现在只能切到 personal——公共骨架不内置 enterprise profile。
  // 如果将来引入 enterprise overlay，调用方应改为直接 hydrate(overlayProfile)。
  // TODO Slice 4: Edition 改 opaque string 后，这个函数签名也要收窄或彻底重做。
  setEdition: (_edition) => {
    set({
      edition: personalProfile.edition,
      features: personalProfile.features,
      desktopRoutes: [...personalProfile.desktopRoutes],
      settingsSections: [...personalProfile.settingsSections],
      workspacePanels: [...personalProfile.workspacePanels],
      services: [...personalProfile.services],
      branding: personalProfile.branding,
      navItems: [...personalProfile.navItems],
    });
  },

  hydrate: (profile) =>
    set({
      edition: profile.edition,
      features: profile.features,
      desktopRoutes: [...profile.desktopRoutes],
      settingsSections: [...profile.settingsSections],
      workspacePanels: [...profile.workspacePanels],
      services: [...profile.services],
      branding: profile.branding,
      navItems: [...profile.navItems],
    }),

  registerRoute: (route) => {
    set((state) => ({ desktopRoutes: upsertById(state.desktopRoutes, route) }));
    return () => {
      set((state) => ({
        desktopRoutes: state.desktopRoutes.filter((r) => r.id !== route.id),
      }));
    };
  },
  unregisterRoute: (id) =>
    set((state) => ({
      desktopRoutes: state.desktopRoutes.filter((r) => r.id !== id),
    })),

  registerSettingsSection: (section) => {
    set((state) => ({
      settingsSections: upsertById(state.settingsSections, section),
    }));
    return () => {
      set((state) => ({
        settingsSections: state.settingsSections.filter(
          (s) => s.id !== section.id,
        ),
      }));
    };
  },
  unregisterSettingsSection: (id) =>
    set((state) => ({
      settingsSections: state.settingsSections.filter((s) => s.id !== id),
    })),

  registerWorkspacePanel: (panel) => {
    set((state) => ({
      workspacePanels: upsertById(state.workspacePanels, panel),
    }));
    return () => {
      set((state) => ({
        workspacePanels: state.workspacePanels.filter((p) => p.id !== panel.id),
      }));
    };
  },
  unregisterWorkspacePanel: (id) =>
    set((state) => ({
      workspacePanels: state.workspacePanels.filter((p) => p.id !== id),
    })),

  registerService: (descriptor) => {
    set((state) => ({ services: upsertByName(state.services, descriptor) }));
    return () => {
      set((state) => ({
        services: state.services.filter((s) => s.name !== descriptor.name),
      }));
    };
  },
  unregisterService: (name) =>
    set((state) => ({
      services: state.services.filter((s) => s.name !== name),
    })),

  registerNavItem: (item) => {
    set((state) => ({ navItems: upsertById(state.navItems, item) }));
    return () => {
      set((state) => ({
        navItems: state.navItems.filter((n) => n.id !== item.id),
      }));
    };
  },
  unregisterNavItem: (id) =>
    set((state) => ({
      navItems: state.navItems.filter((n) => n.id !== id),
    })),

  registerSlot: (name, registration) => {
    set((state) => ({
      slots: {
        ...state.slots,
        [name]: upsertById(state.slots[name] ?? [], registration),
      },
    }));
    return () => {
      set((state) => ({
        slots: {
          ...state.slots,
          [name]: (state.slots[name] ?? []).filter(
            (s) => s.id !== registration.id,
          ),
        },
      }));
    };
  },
  unregisterSlot: (name, id) =>
    set((state) => ({
      slots: {
        ...state.slots,
        [name]: (state.slots[name] ?? []).filter((s) => s.id !== id),
      },
    })),
}));

/**
 * Snapshot getter — for non-React code paths that do not need reactivity.
 * Prefer `useRegistryStore(selector)` in components.
 */
export const getRegistrySnapshot = (): EditionProfile => {
  const state = useRegistryStore.getState();
  return {
    edition: state.edition,
    features: state.features,
    desktopRoutes: state.desktopRoutes,
    settingsSections: state.settingsSections,
    workspacePanels: state.workspacePanels,
    services: state.services,
    branding: state.branding,
    navItems: state.navItems,
  };
};
