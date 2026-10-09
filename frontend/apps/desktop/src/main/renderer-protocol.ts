import path from "node:path";
import { readFile } from "node:fs/promises";
import { app, protocol } from "electron";

/**
 * The scheme the packaged renderer is served from.
 *
 * The packaged window used to load ``file://…/dist/index.html``. A ``file://``
 * page has no origin — Chromium serializes it as ``file://``/``null``, which
 * no allowlist can name and no CSP ``frame-ancestors`` can pin. That left the
 * desktop unable to embed any Workbench site (the backend refuses a parent
 * that is not an origin). Serving the same files from a privileged custom
 * scheme gives the renderer a real, stable origin (``valuz-app://app``) that
 * backends can list in ``VALUZ_SITES_EMBED_PARENT_ORIGINS`` and CSP can pin.
 *
 * The scheme name is a cross-repo contract: the commercial overlay's
 * ``managed-runtime.ts`` and the backend allowlists reference it.
 */
export const RENDERER_SCHEME = "valuz-app";
export const RENDERER_HOST = "app";
export const RENDERER_ORIGIN = `${RENDERER_SCHEME}://${RENDERER_HOST}`;

const MIME: Record<string, string> = {
  html: "text/html",
  js: "text/javascript",
  mjs: "text/javascript",
  css: "text/css",
  json: "application/json",
  svg: "image/svg+xml",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  ico: "image/x-icon",
  woff: "font/woff",
  woff2: "font/woff2",
  ttf: "font/ttf",
  otf: "font/otf",
  wasm: "application/wasm",
  txt: "text/plain",
  map: "application/json",
  pdf: "application/pdf",
  mp4: "video/mp4",
  webm: "video/webm",
};

function mimeFor(p: string): string {
  const ext = p.includes(".") ? p.split(".").pop()!.toLowerCase() : "";
  return MIME[ext] ?? "application/octet-stream";
}

/**
 * Resolve a ``valuz-app://app/<path>`` URL to a file under ``root``.
 *
 * Pure and exported for tests: this is the security boundary of the handler —
 * only exact files inside the bundle root are ever returned; ``..`` (raw or
 * percent-encoded), credentials, a foreign host, or a NUL byte all resolve to
 * ``null``. Extensionless paths fall back to ``index.html`` (the renderer is
 * hash-routed; this is belt and braces, never a path users navigate to).
 */
export function resolveRendererTarget(
  rawUrl: string,
  root: string,
): { abs: string; mime: string } | null {
  let pathname: string;
  try {
    const url = new URL(rawUrl);
    if (url.hostname !== RENDERER_HOST || url.username || url.password) {
      return null;
    }
    pathname = decodeURIComponent(url.pathname);
    if (pathname.includes("\0")) {
      return null;
    }
  } catch {
    return null;
  }
  if (pathname === "/" || pathname === "") {
    pathname = "/index.html";
  }
  let resolved = path.normalize(path.join(root, pathname));
  const rel = path.relative(root, resolved);
  // A relative result that escapes upward (``../``) or is absolute means the
  // URL pointed outside the bundle root.
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    return null;
  }
  if (!path.extname(resolved)) {
    resolved = path.join(root, "index.html");
  }
  return { abs: resolved, mime: mimeFor(resolved) };
}

/**
 * Must be called AFTER ``app.whenReady()`` (declare the scheme as privileged
 * separately, before ready — see ``index.ts``). Registers the
 * ``valuz-app://`` request handler that serves the built renderer from
 * ``<appPath>/dist``. The security boundary is ``resolveRendererTarget`` —
 * only files inside ``dist`` are ever served.
 */
export function registerRendererProtocolHandler(): void {
  const root = path.join(app.getAppPath(), "dist");
  protocol.handle(RENDERER_SCHEME, async (request) => {
    const target = resolveRendererTarget(request.url, root);
    if (!target) {
      console.error(`${RENDERER_SCHEME}:// refused`, request.url);
      return new Response("bad request", { status: 400 });
    }
    try {
      const data = await readFile(target.abs);
      return new Response(new Uint8Array(data), {
        headers: { "content-type": target.mime },
      });
    } catch (err) {
      console.error(
        `${RENDERER_SCHEME}:// failed to serve`,
        request.url,
        "->",
        err instanceof Error ? err.message : String(err),
      );
      return new Response("not found", { status: 404 });
    }
  });
}
