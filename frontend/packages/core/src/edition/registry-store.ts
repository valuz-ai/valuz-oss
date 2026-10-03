import { create } from "zustand";
import type { Capabilities } from "./capabilities";
import type { Edition, EditionProfile, FeatureFlags } from "./profile";
import type {
  BrandingProfile,
  DesktopRouteModule,
  NavGroupModule,
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
  navGroups: NavGroupModule[];
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
  /**
   * Surfaces an overlay has temporarily suppressed, keyed by surface id.
   *
   * Some overlay modes take the page over — a selection mode, for instance —
   * and leaving the composer live during one invites an action that makes no
   * sense in that mode. A slot cannot express this: slots ADD nodes, and what
   * is needed is the host declining to render its own.
   */
  suppressed: Record<string, boolean>;
  setSuppressed: (surface: string, on: boolean) => void;
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

/**
 * What each registered item replaced (by id) when it went in — the restore
 * target its disposer puts back. Keyed by the registered object itself, so a
 * registration and its disposer agree on identity without extra bookkeeping.
 */
const restoreTargets = new WeakMap<object, object | undefined>();

/**
 * Upsert ``item`` by id and return a disposer that undoes exactly this
 * registration.
 *
 * - If ``item`` replaced an existing entry (a plugin overriding a host page),
 *   disposing puts that entry back at the same position instead of deleting
 *   the id outright.
 * - If ``item`` was itself replaced by a later registration, disposing leaves
 *   the newer entry alone and splices ``item`` out of the restore chain, so the
 *   newer one later restores what ``item`` replaced rather than resurrecting a
 *   disposed registration.
 */
function registerReplaceable<T extends { id: string }>(
  read: () => T[],
  write: (next: T[]) => void,
  item: T,
): () => void {
  const list = read();
  const index = list.findIndex((entry) => entry.id === item.id);
  restoreTargets.set(item, index === -1 ? undefined : list[index]);
  write(upsertById(list, item));

  return () => {
    if (!restoreTargets.has(item)) return; // already disposed
    const replaced = restoreTargets.get(item) as T | undefined;
    restoreTargets.delete(item);

    const current = read();
    const at = current.findIndex((entry) => entry.id === item.id);
    if (at !== -1 && current[at] === item) {
      if (replaced) {
        const next = current.slice();
        next[at] = replaced;
        write(next);
      } else {
        write(current.filter((entry) => entry !== item));
      }
      return;
    }

    // Superseded: whoever replaced ``item`` should now restore ``replaced``.
    if (at === -1) return;
    let cursor: object | undefined = current[at];
    while (cursor && restoreTargets.has(cursor)) {
      const target = restoreTargets.get(cursor);
      if (target === item) {
        restoreTargets.set(cursor, replaced);
        return;
      }
      cursor = target;
    }
  };
}

/** Stable sort by ``priority`` (default 0); equal priorities keep insertion order. */
const byPriority = <T extends { priority?: number }>(list: T[]): T[] =>
  list
    .map((entry, index) => ({ entry, index }))
    .sort(
      (a, b) =>
        (a.entry.priority ?? 0) - (b.entry.priority ?? 0) || a.index - b.index,
    )
    .map(({ entry }) => entry);

const upsertByName = <T extends { name: string }>(list: T[], item: T): T[] => {
  const existing = list.findIndex((entry) => entry.name === item.name);
  if (existing === -1) {
    return [...list, item];
  }
  const next = list.slice();
  next[existing] = item;
  return next;
};

export const useRegistryStore = create<RegistryState>((set, get) => ({
  edition: seed.edition,
  features: seed.features,
  desktopRoutes: [...seed.desktopRoutes],
  settingsSections: [...seed.settingsSections],
  projectPanels: [...seed.projectPanels],
  services: [...seed.services],
  branding: seed.branding,
  navItems: [...seed.navItems],
  navGroups: [...(seed.navGroups ?? [])],
  capabilities: { ...seed.capabilities },
  slots: {},

  // setEdition 现在只能切到 personal——公共骨架不内置 enterprise profile。
  // 如果将来引入 enterprise overlay，调用方应改为直接 hydrate(overlayProfile)。
  // TODO Slice 4: Edition 改 opaque string 后，这个函数签名也要收窄或彻底重做。
  setEdition: (_edition) => {
    void _edition;
    set({
      edition: personalProfile.edition,
      features: personalProfile.features,
      desktopRoutes: [...personalProfile.desktopRoutes],
      settingsSections: [...personalProfile.settingsSections],
      projectPanels: [...personalProfile.projectPanels],
      services: [...personalProfile.services],
      branding: personalProfile.branding,
      navItems: [...personalProfile.navItems],
      navGroups: [...(personalProfile.navGroups ?? [])],
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
      navGroups: [...(profile.navGroups ?? [])],
      capabilities: { ...profile.capabilities },
    }),

  setCapabilities: (patch) =>
    set((state) => ({
      capabilities: { ...state.capabilities, ...patch },
    })),

  registerRoute: (route) =>
    registerReplaceable(
      () => get().desktopRoutes,
      (desktopRoutes) => set({ desktopRoutes }),
      route,
    ),
  unregisterRoute: (id) =>
    set((state) => ({
      desktopRoutes: state.desktopRoutes.filter((r) => r.id !== id),
    })),

  registerSettingsSection: (section) =>
    registerReplaceable(
      () => get().settingsSections,
      (settingsSections) => set({ settingsSections }),
      section,
    ),
  unregisterSettingsSection: (id) =>
    set((state) => ({
      settingsSections: state.settingsSections.filter((s) => s.id !== id),
    })),

  registerProjectPanel: (panel) =>
    registerReplaceable(
      () => get().projectPanels,
      (projectPanels) => set({ projectPanels }),
      panel,
    ),
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

  registerNavItem: (item) =>
    registerReplaceable(
      () => get().navItems,
      (navItems) => set({ navItems }),
      item,
    ),
  unregisterNavItem: (id) =>
    set((state) => ({
      navItems: state.navItems.filter((n) => n.id !== id),
    })),

  suppressed: {},
  setSuppressed: (surface, on) =>
    set((state) => ({ suppressed: { ...state.suppressed, [surface]: on } })),

  registerSlot: (name, registration) => {
    set((state) => ({
      slots: {
        ...state.slots,
        [name]: byPriority(upsertById(state.slots[name] ?? [], registration)),
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
    navGroups: state.navGroups,
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
