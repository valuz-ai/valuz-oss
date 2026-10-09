import { ApiError, createFetchJson } from "./fetch-json";

/**
 * Client for ``/v1/dsh/plugins`` — the Valuz-managed DeepSeek Harness (dsh)
 * profile, managed the dsh way.
 *
 * The backend proxies every ``remote/{method}`` call to dsh's own
 * ``pluginManager`` Remote service, so install / enable / remove behave exactly
 * as they do in dsh (guided installs, build approval, rollback, peer gating).
 * The wire argument names are dsh's parameter names
 * (``packages/boot/plugin-manager/src/index.ts``), and a ``remote`` call answers
 * ``{ value }``.
 *
 * These routes are not in ``api/openapi.yaml`` (the ``remote`` body is a dsh
 * passthrough), so the types below mirror dsh's ``plugin-manager/src/types.ts``
 * by hand, in the same way ``plugins-api.ts`` mirrors its schemas. Names carry a
 * ``Dsh`` prefix because ``@valuz/core`` already exports unrelated ``Plugin*``
 * names.
 *
 * Failure model (mirrors dsh): a refused or failed *change* is HTTP 200 with
 * ``application: "failed"`` and an ``error`` — check the result. HTTP errors are
 * transport-level: ``409`` = the manager cannot run here (reason in ``detail``),
 * ``400`` = a dsh Remote error ``{ detail: { code, error } }``.
 */

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setDshPluginsApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

// ── manager host ──────────────────────────────────────────────────────────

export interface DshManagerStatus {
  /** Whether this deployment may run a manager host at all (cloud: false). */
  enabled: boolean;
  /** ``enabled`` and the runtime closure + Node are present. */
  available: boolean;
  running: boolean;
  unavailable_reason: string | null;
  /** The managed dsh home (isolated from the user's own ``~/.dsh``). */
  home: string;
  profile: string;
  /** Token URL of the native dsh web UI, only while the host is running. */
  ui_url: string | null;
}

export interface DshManagerStart extends DshManagerStatus {
  /** Token URL of the native dsh web UI of the resident host. */
  ui_url: string;
}

// ── pluginManager records ─────────────────────────────────────────────────

/** Why dsh refuses to change a profile control (switch off, remove). */
export type DshReadOnlyReason = "management-required" | "unaddressable";

export type DshManagementErrorCode =
  | DshReadOnlyReason
  | "unknown-plugin"
  | "invalid-spec"
  | "ambiguous-install"
  | "not-bundle"
  | "not-removable"
  | "stop-profile"
  | "bundle-in-use"
  | "stale-approval"
  | "incompatible-version"
  | "operation-error";

/** A package whose declared dsh peers reject the running dsh version. */
export interface DshIncompatiblePlugin {
  name: string;
  version: string;
  runtimeVersion: string;
  peers: Record<string, string>;
}

export interface DshManagementError {
  code: DshManagementErrorCode;
  diagnostic?: string;
  incompatible?: DshIncompatiblePlugin[];
}

/** Literal text, or translations keyed by lowercase language id (``en`` required). */
export type DshLocalizedText =
  string | { en: string; [locale: string]: string };

export interface DshLocalizedMeta {
  title?: DshLocalizedText;
  description?: DshLocalizedText;
  /** Base64 image data URL. */
  icon?: string;
  /** Unmodified local metadata diagnostic; the plugin stays manageable. */
  error?: string;
}

export interface DshBundleRow {
  rowId: string;
  moduleName: string;
  meta?: DshLocalizedMeta;
  entryId?: string;
}

/** One installed or installation-provided bundle (``listBundles``). */
export interface DshBundleInfo {
  name: string;
  version?: string;
  meta?: DshLocalizedMeta;
  /** Untranslated ``description`` of the bundle's package manifest. */
  description?: string;
  /** Selected in the profile manifest. */
  enabled: boolean;
  /** The profile's own dependencies hold the package. */
  installed: boolean;
  /** Spec ``pnpm add`` accepts, for a profile-owned dependency. */
  source?: string;
  /** The installation ships it for the person to switch on. */
  optional: boolean;
  removable: boolean;
  /** Why dsh refuses to switch the bundle off or remove it. */
  readOnlyReason?: DshReadOnlyReason;
  error?: DshManagementError;
  rows: DshBundleRow[];
  overrides: string[];
}

export type DshFiberPhase =
  "pending" | "loading" | "active" | "failed" | "unloading" | null;

/** One running-profile entry (``listPlugins``). */
export interface DshPluginInfo {
  entryId: string;
  moduleName: string;
  meta?: DshLocalizedMeta;
  enabled: boolean;
  fiberPhase: DshFiberPhase;
  /** Present when the entry can be switched through the profile patch. */
  patchId?: string;
  readOnlyReason?: DshReadOnlyReason;
}

/** A registry URL, or ``null`` for the one pnpm's own configuration names. */
export type DshRegistry = string | null;

export type DshInstallSpecKind = "registry" | "path" | "git" | "tarball";

export type DshInspectProblem =
  | "invalid-spec"
  | "already-installed"
  | "not-found"
  | "not-a-package"
  | "not-a-bundle"
  | "network"
  | "unknown";

/** What a spec names, read before installing it (``inspect``). */
export type DshSpecInspection =
  | {
      status: "accepted";
      kind: DshInstallSpecKind;
      name?: string;
      version?: string;
      description?: string;
      /** ``null`` for git / tarball: only known once pnpm has fetched it. */
      bundle: boolean | null;
      registry: DshRegistry;
      /** The host a git spec or tarball URL is fetched from. */
      host?: string;
    }
  | {
      status: "refused";
      problem: DshInspectProblem;
      reason: string;
      registries?: DshRegistry[];
    };

export type DshPackageResultKind =
  | "pnpm-missing"
  | "timeout"
  | "not-found"
  | "no-matching-version"
  | "network"
  | "disk-full"
  | "permission"
  | "build-blocked"
  | "integrity"
  | "unknown";

export interface DshPackageResult {
  exitCode: number;
  output: string;
  truncated: boolean;
  logPath: string;
  kind?: DshPackageResultKind;
  timedOut?: boolean;
  incompatible?: DshIncompatiblePlugin[];
}

export type DshApplication =
  "applied" | "restart-required" | "overridden" | "failed" | "cancelled";

/** Persisted change plus the independently observed application outcome. */
export interface DshChangeResult {
  changed: boolean;
  application: DshApplication;
  stage: "install" | "enable" | "remove";
  target: string;
  enabled?: boolean;
  error?: DshManagementError;
  warnings?: string[];
  packageResult?: DshPackageResult;
  /** The bundle an installation added. */
  bundle?: string;
  version?: string;
  /** Packages awaiting explicit build-script approval (dsh's own UI approves). */
  pendingBuilds?: string[];
  approvedBuilds?: string[];
  registries?: DshRegistry[];
  failedAt?: "registry" | "spec-host";
}

export interface DshInspectOptions {
  registry?: DshRegistry;
}

export interface DshInstallOptions {
  /** Activate the bundle after installing (dsh defaults to true). */
  enabled?: boolean;
  /** Names the install for ``cancelInstall``. */
  requestId?: string;
  approvedBuilds?: string[];
  registry?: DshRegistry;
}

export interface DshInstallCancellation {
  status: "cancelled" | "too-late" | "not-running";
}

// ── transport ─────────────────────────────────────────────────────────────

const BASE_PATH = "/v1/dsh/plugins";

type DshRemoteMethod =
  | "listBundles"
  | "listPlugins"
  | "inspect"
  | "installBundle"
  | "cancelInstall"
  | "removeBundle"
  | "setBundleEnabled"
  | "setPluginEnabled";

/** ``args`` keys are dsh's parameter names; ``undefined`` values are dropped. */
async function remote<T>(
  method: DshRemoteMethod,
  args: Record<string, unknown> = {},
): Promise<T> {
  const { value } = await fetchJson<{ value: T }>(
    `${BASE_PATH}/remote/${method}`,
    { method: "POST", json: { args } },
  );
  return value;
}

export const dshPluginsApi = {
  /** Never starts the manager — safe to call on page open. */
  status(): Promise<DshManagerStatus> {
    return fetchJson(`${BASE_PATH}/status`);
  },

  /** Starts the resident manager host if needed (can take ~30s). */
  startManager(): Promise<DshManagerStart> {
    return fetchJson(`${BASE_PATH}/manager/start`, { method: "POST" });
  },

  stopManager(): Promise<DshManagerStatus> {
    return fetchJson(`${BASE_PATH}/manager/stop`, { method: "POST" });
  },

  /** Starts the manager on first use, like every ``remote`` call. */
  listBundles(): Promise<DshBundleInfo[]> {
    return remote("listBundles");
  },

  listPlugins(): Promise<DshPluginInfo[]> {
    return remote("listPlugins");
  },

  /** Read what a spec names before installing it. */
  inspect(
    spec: string,
    options?: DshInspectOptions,
  ): Promise<DshSpecInspection> {
    return remote("inspect", { spec, options });
  },

  /** A failed install resolves with ``application: "failed"`` — it does not throw. */
  installBundle(
    spec: string,
    options?: DshInstallOptions,
  ): Promise<DshChangeResult> {
    return remote("installBundle", { spec, options });
  },

  cancelInstall(requestId: string): Promise<DshInstallCancellation> {
    return remote("cancelInstall", { requestId });
  },

  setBundleEnabled(name: string, enabled: boolean): Promise<DshChangeResult> {
    return remote("setBundleEnabled", { name, enabled });
  },

  /** ``id`` is a ``DshPluginInfo.entryId``. */
  setPluginEnabled(id: string, enabled: boolean): Promise<DshChangeResult> {
    return remote("setPluginEnabled", { id, enabled });
  },

  removeBundle(name: string): Promise<DshChangeResult> {
    return remote("removeBundle", { name });
  },
};

// ── errors ────────────────────────────────────────────────────────────────

export interface DshApiFailure {
  /** HTTP status, or ``null`` for a non-HTTP failure (offline, aborted). */
  status: number | null;
  /** dsh's Remote error code, on a ``400``. */
  code: string | null;
  message: string;
}

/** ``409`` — the manager cannot run in this deployment (reason in the message). */
export const isDshUnavailableError = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 409;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * Flatten any failure of this API into ``{ status, code, message }``.
 * A ``400`` carries dsh's own ``{ detail: { code, error } }`` (the transport's
 * error extractor finds no ``message`` there), so it is read from the raw body.
 */
export function describeDshApiError(error: unknown): DshApiFailure {
  if (!(error instanceof ApiError)) {
    return {
      status: null,
      code: null,
      message: error instanceof Error ? error.message : String(error),
    };
  }
  if (error.status === 400 && error.body) {
    try {
      const detail = asRecord(asRecord(JSON.parse(error.body))?.detail);
      const code = typeof detail?.code === "string" ? detail.code : null;
      const inner = asRecord(detail?.error);
      const message =
        (typeof inner?.message === "string" && inner.message) ||
        (typeof inner?.diagnostic === "string" && inner.diagnostic) ||
        code;
      if (message) return { status: 400, code, message };
    } catch {
      // Not JSON — fall through to the transport's message.
    }
  }
  return { status: error.status, code: null, message: error.message };
}
