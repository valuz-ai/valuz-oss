/**
 * Project-root path arithmetic — the single implementation.
 *
 * Agents hand back either a project-relative path (``"reports/q3.md"``, the
 * common case for ``finish_task``/artifacts) or an absolute one. Every surface
 * that opens a file needs the same two conversions: relative → absolute (the
 * identity handed to the file-address resolver) and absolute → project-relative
 * (what the shell and the page URL show).
 *
 * This used to be copy-pasted into three pages plus the conversation link
 * hook, and the copies had already drifted: one of them tested Windows
 * absolute paths with ``/^[a-zA-Z]:\//`` (forward slash only), so ``C:\x``
 * was classified as relative there and as absolute everywhere else.
 */

/** ``C:\x`` or ``C:/x`` — a Windows drive specifier, either spelling. */
export function isWindowsDrivePath(path: string): boolean {
  return /^[a-zA-Z]:[\\/]/.test(path);
}

/**
 * True when ``value`` opens with a URI scheme rather than a filesystem path.
 *
 * The naive test — ``/^[a-z][a-z0-9+.-]*:/i`` — cannot be used on its own,
 * because a Windows drive specifier IS a syntactically valid one-character
 * scheme: it matches ``C:/Users/…`` and declares the whole of Windows to be
 * URLs. Subtracting every drive-shaped prefix is the opposite error, and just
 * as wrong: it hands back ``s://evil.example/x`` as a "path".
 *
 * The tie-breaker is the authority marker. ``scheme://host`` opens an
 * authority with two slashes; a drive specifier is a root, and a root is one
 * separator. So a drive-shaped prefix followed by a SECOND separator is a
 * scheme, not a drive.
 *
 * One case stays genuinely undecidable and is resolved in favour of the path:
 * a one-letter scheme with no authority (``a:/x``) is character-for-character
 * a drive specifier. No amount of parsing separates them — ``a:`` is a real
 * Windows drive — so this returns false there, same as Node's
 * ``path.win32.isAbsolute``. Nothing in the product emits a one-letter scheme,
 * and every dangerous one (``javascript:``, ``data:``, ``vbscript:``,
 * ``file:``) is multi-character and unaffected.
 */
export function hasUriScheme(value: string): boolean {
  if (!/^[a-z][a-z0-9+.-]*:/i.test(value)) return false;
  if (!isWindowsDrivePath(value)) return true;
  return /^[a-zA-Z]:[\\/][\\/]/.test(value);
}

/** ``/x``, ``C:\x`` or ``C:/x`` — POSIX and both Windows spellings. */
export function isAbsolutePath(path: string): boolean {
  return path.startsWith("/") || isWindowsDrivePath(path);
}

/**
 * Join a project-relative path onto ``rootPath``. Absolute paths, empty
 * paths, and an unknown root are returned unchanged — this never invents a
 * location it cannot justify.
 */
export function toAbsoluteProjectPath(path: string, rootPath: string): string {
  if (!path || isAbsolutePath(path)) return path;
  if (!rootPath) return path;
  const sep = rootPath.includes("\\") ? "\\" : "/";
  const trimmed = rootPath.endsWith(sep) ? rootPath.slice(0, -1) : rootPath;
  return `${trimmed}${sep}${path}`;
}

/**
 * Strip ``rootPath`` off an absolute path. Returns ``null`` when the path
 * lies outside the project (or IS the root), so callers can tell "not ours"
 * apart from "at the root". An already-relative path is normalized and
 * returned as-is.
 */
export function toProjectRelativePath(
  path: string,
  rootPath: string,
): string | null {
  if (!path) return null;
  const normalizedPath = path.replace(/\\/g, "/");
  if (!isAbsolutePath(normalizedPath)) {
    return normalizedPath.replace(/^\/+/, "");
  }
  if (!rootPath) return null;
  const normalizedRoot = rootPath.replace(/\\/g, "/").replace(/\/+$/, "");
  if (!normalizedRoot) return null;
  if (normalizedPath === normalizedRoot) return null;
  if (!normalizedPath.startsWith(`${normalizedRoot}/`)) return null;
  return normalizedPath.slice(normalizedRoot.length + 1);
}
