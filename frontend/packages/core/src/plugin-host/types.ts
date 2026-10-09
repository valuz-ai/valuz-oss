import type { ResourceCategory } from "@valuz/shared";
import type { Capabilities } from "../edition/capabilities";
import type {
  DesktopRouteModule,
  NavGroupModule,
  NavItemModule,
  ProjectPanelModule,
  ServiceDescriptor,
  SettingsSectionModule,
} from "../edition/profile";
import type { Placement } from "../edition/registries/layers";
import type { SlotRegistration } from "../edition/registries/slots";

/**
 * A unit of UI capability. Every contribution a plugin makes through its
 * {@link ValuzPluginContext} is an effect of the plugin: it is withdrawn when the
 * plugin unloads, and rolled back if ``apply`` throws.
 */
export interface ValuzPlugin {
  /** Stable id, unique within a host. */
  readonly id: string;
  /** Contribute UI and behaviour. May be async; the host waits for it. */
  apply(ctx: ValuzPluginContext): void | Promise<void>;
}

/**
 * Registration helpers scoped to one plugin. Each call writes into the shared
 * registries exactly as the bare store API would, and ties the undo to the
 * plugin's lifetime.
 */
export interface PluginRegistry {
  /** Contribute to a UI slot (list / single / keyed — see SlotRegistration). */
  slot(name: string, registration: SlotRegistration): void;
  /**
   * Add a route, or replace one by id (in place; unloading restores the
   * replaced route). ``placement`` positions a route whose id is new.
   */
  route(route: DesktopRouteModule, placement?: Placement): void;
  /** Hide the route with ``id`` while the plugin is loaded; unloading restores it. */
  removeRoute(id: string): void;
  /** Add a settings section, or replace one by id (restored on unload). */
  settingsSection(
    section: SettingsSectionModule,
    placement?: Placement,
  ): void;
  /** Hide the settings section with ``id`` while loaded; unloading restores it. */
  removeSettingsSection(id: string): void;
  /** Add a project panel, or replace one by id (restored on unload). */
  projectPanel(panel: ProjectPanelModule): void;
  /** Add a sidebar nav item, or replace one by id (restored on unload). */
  navItem(item: NavItemModule, placement?: Placement): void;
  /** Hide the nav item with ``id`` while loaded; unloading restores it. */
  removeNavItem(id: string): void;
  /** Declare a custom labelled sidebar group for nav items to join. */
  navGroup(group: NavGroupModule, placement?: Placement): void;
  /** Add a service descriptor. */
  service(descriptor: ServiceDescriptor): void;
  /** Contribute resource-library categories for ``type`` alongside others. */
  categories(type: string, categories: ResourceCategory<unknown>[]): void;
  /** Suppress a host surface while the plugin is loaded. */
  suppress(surface: string): void;
  /**
   * Override capabilities while loaded. The override is a layer over the
   * edition's capabilities: it survives the host hydrating the registry, wins
   * over ``setCapabilities`` while loaded, and unloading withdraws it.
   */
  capabilities(patch: Partial<Capabilities>): void;
}

export interface ValuzPluginContext {
  readonly pluginId: string;
  readonly registry: PluginRegistry;
  /**
   * Hold a resource for the plugin's lifetime: ``setup`` runs now, the
   * disposer it returns runs when the plugin unloads.
   */
  effect(setup: () => (() => void) | void): void;
  /**
   * Run an install function that predates the plugin model and cannot be
   * undone (it returns no disposer). Recorded on the plugin so an unload can
   * report what stays behind. Prefer ``effect`` for anything new.
   */
  legacy(name: string, install: () => void): void;
  /** Mount a child plugin whose lifetime is bound to this one. */
  plugin(child: ValuzPlugin): Promise<PluginRecord>;
}

/**
 * - ``loading`` / ``active`` / ``failed`` / ``disposed`` — the lifecycle of a
 *   plugin the host mounted.
 * - ``backend-disabled`` — never mounted: the backend counterpart of the
 *   plugin (same id) is switched off in this process, so its UI would only
 *   call routes that are not there. See {@link PluginHost.skip}.
 */
export type PluginStatus =
  | "loading"
  | "active"
  | "failed"
  | "disposed"
  | "backend-disabled";

/** Why a plugin was recorded without being mounted. */
export type PluginSkipReason = "backend-disabled";

export interface PluginRecord {
  readonly id: string;
  readonly status: PluginStatus;
  /** Why ``apply`` failed, when ``status === "failed"``. */
  readonly error?: unknown;
  /** Names of legacy installs this plugin ran (see ValuzPluginContext.legacy). */
  readonly legacy: readonly string[];
}

export interface PluginHost {
  /**
   * Load one plugin. Never rejects: a plugin whose ``apply`` throws is marked
   * ``failed`` (its partial contributions rolled back) and the returned record
   * says so — the caller decides whether that plugin was optional.
   */
  load(plugin: ValuzPlugin): Promise<PluginRecord>;
  /** Load plugins one after another, in order. */
  loadAll(plugins: readonly ValuzPlugin[]): Promise<PluginRecord[]>;
  /**
   * Record ``id`` as not loaded for ``reason`` without mounting anything, so
   * the plugin list still shows it (and why it is off). Does nothing when the
   * plugin is already loaded or loading. A later ``load`` of the same id
   * replaces the record.
   */
  skip(id: string, reason: PluginSkipReason): PluginRecord;
  /** Unload a plugin and everything it (and its children) contributed. */
  unload(id: string): Promise<void>;
  get(id: string): PluginRecord | undefined;
  /** Records in load order. The same array is returned until something changes. */
  list(): readonly PluginRecord[];
  subscribe(listener: () => void): () => void;
}
