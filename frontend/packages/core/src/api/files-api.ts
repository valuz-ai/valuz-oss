import { resolveApiBase, type ApiBaseRef } from "./base-resolver";
import { createFetchJson, type RequestOptions } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setFilesApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

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
