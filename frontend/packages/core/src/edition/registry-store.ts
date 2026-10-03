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
import { applyLayers, type LayerOp, type Placement } from "./registries/layers";
import type { SlotMap, SlotRegistration } from "./registries/slots";
import { personalProfile } from "./personal-profile";
import { getActiveProfile } from "./resolve";

/** What each layered list holds. */
interface ListItems {
  desktopRoutes: DesktopRouteModule;
  settingsSections: SettingsSectionModule;
  projectPanels: ProjectPanelModule;
  services: ServiceDescriptor;
  navItems: NavItemModule;
  navGroups: NavGroupModule;
}
type ListKind = keyof ListItems;
type Lists = { [K in ListKind]: ListItems[K][] };

/** The static half of the registry: what an edition profile declares. */
type BaseState = Lists & { capabilities: Capabilities };

/** The live half: ordered layer entries, applied over the base in order. */
type LayerState = { [K in ListKind]: LayerOp<ListItems[K]>[] } & {
  capabilities: Partial<Capabilities>[];
};

/** Identity of an entry within its list. Services are named; the rest have ids. */
const KEY_OF: { [K in ListKind]: (item: ListItems[K]) => string } = {
  desktopRoutes: (item) => item.id,
  settingsSections: (item) => item.id,
  projectPanels: (item) => item.id,
  services: (item) => item.name,
  navItems: (item) => item.id,
  navGroups: (item) => item.id,
};

/**
 * Mutable registry — the single source of truth consumed by the app shell.
 *
 * The lists the shell reads (``desktopRoutes``, ``settingsSections``,
 * ``projectPanels``, ``services``, ``navItems``, ``navGroups``,
 * ``capabilities``) are DERIVED: the BASE (``hydrate`` / ``setEdition`` set it
 * from an EditionProfile) with every live registration — the ``register*``
 * calls below, which is what ``ctx.registry.*`` in a plugin makes — applied on
 * top as ordered LAYER entries. Hydrating therefore replaces the base without
 * wiping what plugins contributed, and disposing a registration removes its
 * layer entry and re-derives, restoring whatever it replaced or hid.
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

  /** @internal The base the visible lists are derived from. */
  base: BaseState;
  /** @internal The layer stack the visible lists are derived through. */
  layers: LayerState;

  /**
   * Replace the whole registry with a personal-edition base. Layers (live
   * plugin registrations) are kept.
   */
  setEdition: (edition: Edition) => void;
  /**
   * Replace the BASE with ``profile``. Live registrations are layers over the
   * base, not part of it, so they survive: a plugin that loaded before the
   * host hydrates still has its routes, sections, nav items and capability
   * overrides afterwards.
   */
  hydrate: (profile: EditionProfile) => void;
  /**
   * Drop every live registration (the layers), leaving the base as is. For
   * tests and hot reload; plugins normally withdraw their own registrations.
   */
  clearLayers: () => void;
  /**
   * Partially update the BASE capabilities at runtime. Overlay editions call
   * this after fetching org policy to flip individual entries; the personal
   * default stays on for everything not explicitly overridden. A plugin's
   * ``registerCapabilities`` override sits above this and wins while loaded.
   */
  setCapabilities: (patch: Partial<Capabilities>) => void;
  /**
   * Override capabilities as a layer; the returned disposer withdraws exactly
   * this override. Survives ``hydrate``.
   */
  registerCapabilities: (patch: Partial<Capabilities>) => () => void;

  /**
   * Add a route, or replace one by id (in place; disposing puts the replaced
   * route back). ``placement`` positions a route whose id is new.
   */
  registerRoute: (
    route: DesktopRouteModule,
    placement?: Placement,
  ) => () => void;
  /** Hide the route with ``id`` while the returned disposer has not run. */
  registerRouteRemoval: (id: string) => () => void;
  /**
   * Imperatively remove ``id``: drops live registrations for it and hides a
   * base entry (this one is not tied to a disposer — prefer
   * ``registerRouteRemoval`` in a plugin).
   */
  unregisterRoute: (id: string) => void;

  registerSettingsSection: (
    section: SettingsSectionModule,
    placement?: Placement,
  ) => () => void;
  registerSettingsSectionRemoval: (id: string) => () => void;
  unregisterSettingsSection: (id: string) => void;

  registerProjectPanel: (panel: ProjectPanelModule) => () => void;
  unregisterProjectPanel: (id: string) => void;

  registerService: (descriptor: ServiceDescriptor) => () => void;
  unregisterService: (name: string) => void;

  registerNavItem: (item: NavItemModule, placement?: Placement) => () => void;
  registerNavItemRemoval: (id: string) => () => void;
  unregisterNavItem: (id: string) => void;

  registerNavGroup: (group: NavGroupModule, placement?: Placement) => () => void;

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

const emptyLayers = (): LayerState => ({
  desktopRoutes: [],
  settingsSections: [],
  projectPanels: [],
  services: [],
  navItems: [],
  navGroups: [],
  capabilities: [],
});

const baseFromProfile = (profile: EditionProfile): BaseState => ({
  desktopRoutes: [...profile.desktopRoutes],
  settingsSections: [...profile.settingsSections],
  projectPanels: [...profile.projectPanels],
  services: [...profile.services],
  navItems: [...profile.navItems],
  navGroups: [...(profile.navGroups ?? [])],
  capabilities: { ...profile.capabilities },
});

// ``K`` is a single key at every call site; TypeScript cannot narrow the
// per-kind mapped types through a generic index, so the accessors do it once.
const baseOf = <K extends ListKind>(base: BaseState, kind: K) =>
  base[kind] as unknown as ListItems[K][];
const layersOf = <K extends ListKind>(layers: LayerState, kind: K) =>
  layers[kind] as unknown as LayerOp<ListItems[K]>[];
const keyOf = <K extends ListKind>(kind: K) =>
  KEY_OF[kind] as unknown as (item: ListItems[K]) => string;

const deriveList = <K extends ListKind>(
  kind: K,
  base: BaseState,
  layers: LayerState,
): Lists[K] =>
  applyLayers(
    baseOf(base, kind),
    layersOf(layers, kind),
    keyOf(kind),
  ) as Lists[K];

const deriveCapabilities = (
  base: Capabilities,
  patches: readonly Partial<Capabilities>[],
): Capabilities => Object.assign({}, base, ...patches) as Capabilities;

/** The visible lists + capabilities for a base and its layers. */
const deriveVisible = (base: BaseState, layers: LayerState) => ({
  desktopRoutes: deriveList("desktopRoutes", base, layers),
  settingsSections: deriveList("settingsSections", base, layers),
  projectPanels: deriveList("projectPanels", base, layers),
  services: deriveList("services", base, layers),
  navItems: deriveList("navItems", base, layers),
  navGroups: deriveList("navGroups", base, layers),
  capabilities: deriveCapabilities(base.capabilities, layers.capabilities),
});

/** State for a freshly set base: profile identity + base + derived lists. */
const stateForProfile = (profile: EditionProfile, layers: LayerState) => {
  const base = baseFromProfile(profile);
  return {
    edition: profile.edition,
    features: profile.features,
    branding: profile.branding,
    base,
    layers,
    ...deriveVisible(base, layers),
  };
};

const upsertById = <T extends { id: string }>(list: T[], item: T): T[] => {
  const existing = list.findIndex((entry) => entry.id === item.id);
  if (existing === -1) {
    return [...list, item];
  }
  const next = list.slice();
  next[existing] = item;
  return next;
};

/** Stable sort by ``priority`` (default 0); equal priorities keep insertion order. */
const byPriority = <T extends { priority?: number }>(list: T[]): T[] =>
  list
    .map((entry, index) => ({ entry, index }))
    .sort(
      (a, b) =>
        (a.entry.priority ?? 0) - (b.entry.priority ?? 0) || a.index - b.index,
    )
    .map(({ entry }) => entry);

export const useRegistryStore = create<RegistryState>((set) => {
  /** Write a new layer stack for ``kind`` and re-derive that list. */
  const commit = <K extends ListKind>(
    state: RegistryState,
    kind: K,
    next: LayerOp<ListItems[K]>[],
  ): Partial<RegistryState> => {
    const layers = { ...state.layers, [kind]: next } as LayerState;
    return {
      layers,
      [kind]: deriveList(kind, state.base, layers),
    } as Partial<RegistryState>;
  };

  /** Append a layer entry; the disposer removes exactly that entry. */
  const addLayer = <K extends ListKind>(
    kind: K,
    op: LayerOp<ListItems[K]>,
  ): (() => void) => {
    set((state) =>
      commit(state, kind, [...layersOf(state.layers, kind), op]),
    );
    return () => {
      set((state) => {
        const current = layersOf(state.layers, kind);
        if (!current.includes(op)) return {}; // already disposed
        return commit(
          state,
          kind,
          current.filter((entry) => entry !== op),
        );
      });
    };
  };

  const put = <K extends ListKind>(
    kind: K,
    item: ListItems[K],
    placement?: Placement,
  ) => addLayer<K>(kind, { op: "put", item, placement });

  const removal = <K extends ListKind>(kind: K, key: string) =>
    addLayer<K>(kind, { op: "remove", key });

  /**
   * Imperative removal: forget live registrations of ``key`` and, when a base
   * entry (or a live one that is not ours to drop) still shows it, hide it
   * with a removal entry. Idempotent — a key that is already gone adds
   * nothing, so repeated unregister/register cycles do not grow the stack.
   */
  const unregister = <K extends ListKind>(kind: K, key: string) =>
    set((state) => {
      const keyFor = keyOf(kind);
      const current = layersOf(state.layers, kind);
      const kept = current.filter(
        (entry) => !(entry.op === "put" && keyFor(entry.item) === key),
      );
      const stillVisible = applyLayers(
        baseOf(state.base, kind),
        kept,
        keyFor,
      ).some((entry) => keyFor(entry) === key);
      const next: LayerOp<ListItems[K]>[] = stillVisible
        ? [...kept, { op: "remove", key }]
        : kept;
      if (next.length === current.length && !stillVisible) return {};
      return commit(state, kind, next);
    });

  return {
    ...stateForProfile(seed, emptyLayers()),
    slots: {},

    // setEdition 现在只能切到 personal——公共骨架不内置 enterprise profile。
    // 如果将来引入 enterprise overlay，调用方应改为直接 hydrate(overlayProfile)。
    // TODO Slice 4: Edition 改 opaque string 后，这个函数签名也要收窄或彻底重做。
    setEdition: (_edition) => {
      void _edition;
      set((state) => stateForProfile(personalProfile, state.layers));
    },

    hydrate: (profile) => set((state) => stateForProfile(profile, state.layers)),

    clearLayers: () =>
      set((state) => {
        const layers = emptyLayers();
        return {
          layers,
          ...deriveVisible(state.base, layers),
        };
      }),

    setCapabilities: (patch) =>
      set((state) => {
        const base = { ...state.base.capabilities, ...patch };
        return {
          base: { ...state.base, capabilities: base },
          capabilities: deriveCapabilities(base, state.layers.capabilities),
        };
      }),

    registerCapabilities: (patch) => {
      const entry = { ...patch };
      set((state) => {
        const patches = [...state.layers.capabilities, entry];
        return {
          layers: { ...state.layers, capabilities: patches },
          capabilities: deriveCapabilities(state.base.capabilities, patches),
        };
      });
      return () => {
        set((state) => {
          if (!state.layers.capabilities.includes(entry)) return {};
          const patches = state.layers.capabilities.filter((p) => p !== entry);
          return {
            layers: { ...state.layers, capabilities: patches },
            capabilities: deriveCapabilities(state.base.capabilities, patches),
          };
        });
      };
    },

    registerRoute: (route, placement) => put("desktopRoutes", route, placement),
    registerRouteRemoval: (id) => removal("desktopRoutes", id),
    unregisterRoute: (id) => unregister("desktopRoutes", id),

    registerSettingsSection: (section, placement) =>
      put("settingsSections", section, placement),
    registerSettingsSectionRemoval: (id) => removal("settingsSections", id),
    unregisterSettingsSection: (id) => unregister("settingsSections", id),

    registerProjectPanel: (panel) => put("projectPanels", panel),
    unregisterProjectPanel: (id) => unregister("projectPanels", id),

    registerService: (descriptor) => put("services", descriptor),
    unregisterService: (name) => unregister("services", name),

    registerNavItem: (item, placement) => put("navItems", item, placement),
    registerNavItemRemoval: (id) => removal("navItems", id),
    unregisterNavItem: (id) => unregister("navItems", id),

    registerNavGroup: (group, placement) => put("navGroups", group, placement),

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
  };
});

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
