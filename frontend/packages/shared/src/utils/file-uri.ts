/**
 * Single source of truth for the file-identity URI codec, shared by every layer
 * (renderer, desktop main process, and — mirrored 1:1 — the Python backend).
 *
 * A file's identity is its absolute path carried behind a scheme:
 *   - ``valuz-file://<abs>`` — the location-agnostic identity a client exchanges
 *     at ``POST /v1/files/resolve``. Also emitted by the MODEL in markdown prose.
 *   - ``valuz-local://<abs>`` — the desktop transport the main process serves a
 *     local file on (renders by URL, uniform with a remote presigned URL).
 *
 * Canonical form is **three slashes** (``scheme:///abs``): the ``//`` is the URI
 * authority marker and an absolute path has no host, so the path sits after an
 * EMPTY authority. Historically several hand-rolled builders/parsers disagreed
 * on this and produced/accepted a two-slash form (``scheme://Users/…``) where the
 * first path segment is mis-parsed as the host — that ambiguity is why file
 * previews intermittently 404'd. Keeping the build/parse in ONE place kills the
 * drift; see ``docs/design/file-address-resolution.md``.
 *
 * Zero internal deps on purpose so the Electron main process can import it (it
 * cannot safely import ``@valuz/core``, which pulls the browser transport).
 */

export const FILE_URI_SCHEME = "valuz-file:";
export const LOCAL_FILE_URL_SCHEME = "valuz-local:";

const FILE_URI_PREFIX = `${FILE_URI_SCHEME}//`;
const LOCAL_FILE_URL_PREFIX = `${LOCAL_FILE_URL_SCHEME}//`;

// valuz-local:// is loaded by Chromium as a ``standard`` scheme (registered
// privileged in the desktop main process). Chromium's standard-URL parser
// ALWAYS promotes the first path segment after ``//`` to the host — and
// lowercases it — so ``valuz-local:///Users/x`` and ``valuz-local://Users/x``
// BOTH canonicalize to ``valuz-local://users/x`` (host=users, path=/x),
// silently dropping ``/Users``. Three slashes do NOT help here (unlike
// valuz-file://, which we parse ourselves and never hand to Chromium). Pin a
// fixed dummy authority so the real absolute path stays entirely in the path
// component — case- and unicode-preserved, cross-platform. The handler ignores
// this host and reads the pathname. (Verified with an Electron repro.)
const LOCAL_FILE_HOST = "f";

const WIN_DRIVE = /^\/[A-Za-z]:\//;

/**
 * Encode an absolute path into the payload of a ``scheme://<abs>`` URI:
 * normalize ``\`` → ``/``, ensure a leading ``/`` (so a Windows ``C:/x`` becomes
 * ``/C:/x``), percent-encode each segment but keep the slashes. Because the
 * result starts with ``/``, appending it after the scheme's own ``//`` yields
 * three slashes with an empty authority. The one encoder both builders use.
 */
function encodeAbsPath(absPath: string): string {
  let p = absPath.replace(/\\/g, "/");
  if (!p.startsWith("/")) p = `/${p}`;
  return p
    .split("/")
    .map((seg) => (seg ? encodeURIComponent(seg) : seg))
    .join("/");
}

/**
 * Decode the absolute path back out of a ``scheme://<abs>`` URI.
 *
 * ``tolerant`` governs the two-slash case (``scheme://Users/…``): fold the
 * mis-parsed host back onto the front of the path so ``//abs`` and ``///abs``
 * resolve identically. Use it only where the producer is untrusted (a MODEL
 * emitting ``valuz-file://`` in prose may drop a slash). For a URL we built
 * ourselves (``valuz-local://``) parse STRICTLY so a malformed URL fails loudly
 * — surfacing a builder/build bug instead of silently "repairing" it.
 */
function decodeUriToAbsPath(uri: string, tolerant: boolean): string | null {
  let url: URL;
  try {
    url = new URL(uri);
  } catch {
    return null;
  }
  const host = tolerant && url.host ? `/${url.host}` : "";
  let path: string;
  try {
    path = decodeURIComponent(host + url.pathname);
  } catch {
    return null;
  }
  if (WIN_DRIVE.test(path)) path = path.slice(1); // /C:/x -> C:/x
  return path || null;
}

/** True when ``ref`` is a ``valuz-file://`` URI. */
export function isFileRef(ref: string): boolean {
  return ref.startsWith(FILE_URI_PREFIX);
}

/** Build a ``valuz-file://<abs>`` ref from an absolute path (POSIX or Windows). */
export function buildFileRef(absPath: string): string {
  return `${FILE_URI_SCHEME}//${encodeAbsPath(absPath)}`;
}

/**
 * Extract the absolute path from a ``valuz-file://<abs>`` ref, or null.
 * TOLERANT — models emit this scheme and may drop a slash.
 */
export function parseFileRef(ref: string): string | null {
  if (!isFileRef(ref)) return null;
  return decodeUriToAbsPath(ref, true);
}

/**
 * Build a ``valuz-local://f/<abs>`` URL from an absolute path (Electron only).
 * The ``f`` authority is a fixed placeholder — see {@link LOCAL_FILE_HOST}; the
 * real path lives in the path component so Chromium's standard-scheme parser
 * can't eat its first segment.
 */
export function buildLocalFileUrl(absPath: string): string {
  return `${LOCAL_FILE_URL_SCHEME}//${LOCAL_FILE_HOST}${encodeAbsPath(absPath)}`;
}

/**
 * Extract the absolute path from a ``valuz-local://f/<abs>`` URL, or null.
 * Reads the path component only — the ``f`` host is a fixed placeholder and is
 * ignored. Used by the desktop ``valuz-local://`` protocol handler.
 */
export function parseLocalFileUrl(url: string): string | null {
  if (!url.startsWith(LOCAL_FILE_URL_PREFIX)) return null;
  return decodeUriToAbsPath(url, false);
}
