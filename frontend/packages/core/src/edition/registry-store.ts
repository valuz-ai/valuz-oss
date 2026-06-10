import { create } from "zustand";
import type { Capabilities } from "./capabilities";
import type { Edition, EditionProfile, FeatureFlags } from "./profile";
import type {
  BrandingProfile,
  DesktopRouteModule,
  NavItemModule,
  ServiceDescriptor,
  SettingsSectionModule,
  ProjectPanelModule,
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
  projectPanels: ProjectPanelModule[];
  services: ServiceDescriptor[];
  branding: BrandingProfile;
  navItems: NavItemModule[];
  capabilities: Capabilities;
  slots: SlotMap;

  setEdition: (edition: Edition) => void;
  hydrate: (profile: EditionProfile) => void;
  /**
   * Partially update capabilities at runtime. Overlay editions call this
   * after fetching org policy to flip individual entries; the personal
   * default stays on for everything not explicitly overridden.
   */
  setCapabilities: (patch: Partial<Capabilities>) => void;

  registerRoute: (route: DesktopRouteModule) => () => void;
  unregisterRoute: (id: string) => void;

  registerSettingsSection: (section: SettingsSectionModule) => () => void;
  unregisterSettingsSection: (id: string) => void;

  registerProjectPanel: (panel: ProjectPanelModule) => () => void;
  unregisterProjectPanel: (id: string) => void;

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
  projectPanels: [...seed.projectPanels],
  services: [...seed.services],
  branding: seed.branding,
  navItems: [...seed.navItems],
  capabilities: { ...seed.capabilities },
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
      projectPanels: [...personalProfile.projectPanels],
      services: [...personalProfile.services],
      branding: personalProfile.branding,
      navItems: [...personalProfile.navItems],
      capabilities: { ...personalProfile.capabilities },
    });
  },

  hydrate: (profile) =>
    set({
      edition: profile.edition,
      features: profile.features,
      desktopRoutes: [...profile.desktopRoutes],
      settingsSections: [...profile.settingsSections],
      projectPanels: [...profile.projectPanels],
      services: [...profile.services],
      branding: profile.branding,
      navItems: [...profile.navItems],
      capabilities: { ...profile.capabilities },
    }),

  setCapabilities: (patch) =>
    set((state) => ({
      capabilities: { ...state.capabilities, ...patch },
    })),

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

  registerProjectPanel: (panel) => {
    set((state) => ({
      projectPanels: upsertById(state.projectPanels, panel),
    }));
    return () => {
      set((state) => ({
        projectPanels: state.projectPanels.filter((p) => p.id !== panel.id),
      }));
    };
  },
  unregisterProjectPanel: (id) =>
    set((state) => ({
      projectPanels: state.projectPanels.filter((p) => p.id !== id),
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
    projectPanels: state.projectPanels,
    services: state.services,
    branding: state.branding,
    navItems: state.navItems,
    capabilities: state.capabilities,
  };
};

/**
 * React hook returning the current capability set. Re-renders subscribers
 * when any capability flips. Lives here rather than in `./capabilities` to
 * keep that file dependency-free (capabilities.ts ← registry-store would
 * complete a cycle through personal-profile and break module init order
 * depending on which file the consumer imports first).
 */
export function useCapabilities(): Capabilities {
  return useRegistryStore((s) => s.capabilities);
}
