import { resolveApiBase, type ApiBaseRef } from "./base-resolver";
import { createFetchJson, type RequestOptions } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setFilesApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

<<<<<<< Updated upstream
// The file-identity URI codec — build/parse of ``valuz-file://`` (identity, also
// emitted by the model) and ``valuz-local://`` (desktop transport) — lives in
// ``@valuz/shared`` as the single source of truth so every layer (renderer,
// desktop MAIN process, and the mirrored Python backend) agrees on the canonical
// three-slash form. Re-exported here so existing ``@valuz/core`` importers are
// unaffected. See docs/design/file-address-resolution.md.
export {
  FILE_URI_SCHEME,
  LOCAL_FILE_URL_SCHEME,
  isFileRef,
  buildFileRef,
  parseFileRef,
  buildLocalFileUrl,
  parseLocalFileUrl,
} from "@valuz/shared";
=======
/** The ``valuz-file://`` URI scheme — a file's identity is its absolute path. */
export const FILE_URI_SCHEME = "valuz-file:";
const FILE_URI_PREFIX = `${FILE_URI_SCHEME}//`;

/** Build a ``valuz-file://<abs>`` ref from an absolute path (POSIX or Windows). */
export function buildFileRef(absPath: string): string {
  let p = absPath.replace(/\\/g, "/");
  if (!p.startsWith("/")) p = `/${p}`; // C:/x -> /C:/x so the result is three-slash
  // encode each segment but keep the slashes
  const encoded = p
    .split("/")
    .map((seg) => (seg ? encodeURIComponent(seg) : seg))
    .join("/");
  // `encoded` already starts with "/" (leading empty segment of an absolute
  // path), so the scheme adds its own "//" authority separator → three slashes
  // (valuz-file:///abs). A single "/" here would make the first path segment the
  // URL host and the backend would reject the ref as invalid.
  return `${FILE_URI_SCHEME}//${encoded}`;
}

/** True when ``ref`` is a ``valuz-file://`` URI. */
export function isFileRef(ref: string): boolean {
  return ref.startsWith(FILE_URI_PREFIX);
}

/** Extract the absolute path from a ``valuz-file://<abs>`` ref, or null. */
export function parseFileRef(ref: string): string | null {
  if (!isFileRef(ref)) return null;
  try {
    const url = new URL(ref);
    // Tolerate a two-slash ref (valuz-file://Users/…): the first path segment was
    // mis-parsed as the host — fold it back so //abs and ///abs give the same path.
    let path = decodeURIComponent(
      (url.host ? `/${url.host}` : "") + url.pathname,
    );
    if (/^\/[A-Za-z]:\//.test(path)) path = path.slice(1); // /C:/x -> C:/x
    return path || null;
  } catch {
    return null;
  }
}

/**
 * The client-side scheme that the desktop main process serves local files on
 * (see apps/desktop/.../local-file-protocol.ts). Lets a ``kind==="local"`` file
 * render by URL (<img>/<iframe>/fetch), uniform with a remote presigned URL.
 * Only resolvable inside Electron.
 */
export const LOCAL_FILE_URL_SCHEME = "valuz-local:";

/** Build a ``valuz-local://<abs>`` URL from an absolute path (Electron only). */
export function buildLocalFileUrl(absPath: string): string {
  let p = absPath.replace(/\\/g, "/");
  if (!p.startsWith("/")) p = `/${p}`;
  const encoded = p
    .split("/")
    .map((seg) => (seg ? encodeURIComponent(seg) : seg))
    .join("/");
  // Pin a fixed ``f`` authority: valuz-local:// is a Chromium `standard` scheme,
  // whose parser promotes the first path segment to the host (and lowercases
  // it) — so an empty-authority form drops the leading path segment. Keep the
  // real path in the path component. See valuz-oss PR #469.
  return `${LOCAL_FILE_URL_SCHEME}//f${encoded}`;
}
>>>>>>> Stashed changes

/**
 * How the client should reach a file. ``kind==="local"`` carries ``absPath``
 * (read it via the desktop ``valuz-local://`` protocol / IPC); ``kind==="remote"``
 * carries a presigned ``url`` the client fetches directly. See
 * docs/design/file-address-resolution.md.
 */
export interface FileCapabilities {
  canPreview: boolean;
  canDownload: boolean;
  /** Only ``true`` for local files; the client further gates by whether it is Electron. */
  canOpenExternal: boolean;
  canCopyContent: boolean;
}

export interface ResolvedFileDescriptor {
  ref: string;
  kind: "local" | "remote" | "";
  absPath: string | null;
  url: string | null;
  expiresAt: number | null;
  name: string;
  mimeType: string | null;
  size: number | null;
  exists: boolean;
  previewKind: string;
  capabilities: FileCapabilities;
  /** ``"invalid_ref" | "forbidden" | "not_found"`` when the ref could not be resolved. */
  error: string | null;
}

const MAX_REFS = 256;

export interface FileResolveOptions extends Pick<RequestOptions, "signal"> {
  /**
   * The entity the file belongs to (session / project / task). A file lives on
   * the backend that owns its entity, so multi-target editions must route the
   * resolve call there — the module default would send a cloud-owned path to
   * the local backend, which rejects it as ``forbidden`` (its owner-root
   * allowlist has no such prefix). Pass the same ref the surface uses for its
   * own entity-scoped calls. Omitted → module default (OSS single-backend).
   */
  baseRef?: ApiBaseRef;
}

export const filesApi = {
  /**
   * Resolve a batch of ``valuz-file://`` refs into access-address descriptors.
   * The backend never returns file bytes — the client fetches from the returned
   * address (desktop ``valuz-local://`` for local, presigned URL for remote).
   */
  resolve(
    refs: string[],
    options: FileResolveOptions = {},
  ): Promise<{ results: ResolvedFileDescriptor[] }> {
    const { baseRef, ...init } = options;
    return fetchJson("/v1/files/resolve", {
      ...init,
      baseUrl: resolveApiBase(baseRef ?? {}, _apiBase),
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refs: refs.slice(0, MAX_REFS) }),
    });
  },

  /** Convenience: resolve a single ref (returns null on error/empty). */
  async resolveOne(
    ref: string,
    options: FileResolveOptions = {},
  ): Promise<ResolvedFileDescriptor | null> {
    const res = await filesApi.resolve([ref], options);
    return res.results[0] ?? null;
  },
};
