import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { Readable } from "node:stream";
import { protocol } from "electron";
import { parseLocalFileUrl } from "@valuz/shared";
import { parseByteRange } from "./byte-range";

/**
 * The ``valuz-local://`` scheme serves a local file to the app's OWN renderer so
 * a ``kind==="local"`` file renders by URL (<img>/<iframe>/fetch) — uniform with
 * a remote presigned URL. Client-side only: no network, the backend never
 * proxies bytes. See docs/design/file-address-resolution.md.
 *
 * Trust model matches the existing ``read_file_content`` IPC — the renderer is
 * trusted, and the backend already validated file ownership before handing the
 * absolute path back in a resolve descriptor.
 */
export const LOCAL_FILE_SCHEME = "valuz-local";

const MIME: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  svg: "image/svg+xml",
  bmp: "image/bmp",
  ico: "image/x-icon",
  pdf: "application/pdf",
  mp4: "video/mp4",
  webm: "video/webm",
  mov: "video/quicktime",
  mp3: "audio/mpeg",
  wav: "audio/wav",
  ogg: "audio/ogg",
  m4a: "audio/mp4",
  txt: "text/plain; charset=utf-8",
  md: "text/markdown; charset=utf-8",
  json: "application/json; charset=utf-8",
  csv: "text/csv; charset=utf-8",
  html: "text/html; charset=utf-8",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

function mimeFor(p: string): string {
  const ext = p.includes(".") ? p.split(".").pop()!.toLowerCase() : "";
  return MIME[ext] ?? "application/octet-stream";
}

/**
 * Must be called AFTER ``app.whenReady()`` (register the scheme as privileged
 * separately, before ready). Registers the ``valuz-local://`` request handler.
 */
export function registerLocalFileProtocolHandler(): void {
  protocol.handle(LOCAL_FILE_SCHEME, async (request) => {
    // parseLocalFileUrl is the single, STRICT codec (shared with the renderer
    // and backend). It is strict on purpose: this URL is always built by
    // buildLocalFileUrl, so a non-canonical form is a builder bug we want to
    // see as a loud 404 + log, not silently "repair".
    const abs = parseLocalFileUrl(request.url);
    if (!abs) {
      console.error("valuz-local:// unparseable url", request.url);
      return new Response("bad request", { status: 400 });
    }
    try {
      const fileStat = await stat(abs);
      const range = parseByteRange(request.headers.get("range"), fileStat.size);
      if (range === "invalid") {
        return new Response("range not satisfiable", {
          status: 416,
          headers: { "content-range": `bytes */${fileStat.size}` },
        });
      }

      const start = range?.start ?? 0;
      const end = range?.end ?? Math.max(fileStat.size - 1, 0);
      const headers = new Headers({
        "accept-ranges": "bytes",
        "cache-control": "no-store",
        "content-length": String(range ? end - start + 1 : fileStat.size),
        "content-type": mimeFor(abs),
      });
      if (range) headers.set("content-range", `bytes ${start}-${end}/${fileStat.size}`);
      if (request.method === "HEAD") {
        return new Response(null, { status: range ? 206 : 200, headers });
      }

      const nodeStream = createReadStream(abs, range ? { start, end } : undefined);
      const body = Readable.toWeb(nodeStream) as ReadableStream<Uint8Array>;
      return new Response(body, { status: range ? 206 : 200, headers });
    } catch (err) {
      // Surface why — a 404 here means the resolved path was wrong or the file
      // moved/was deleted after resolve.
      console.error(
        "valuz-local:// failed to serve",
        request.url,
        "->",
        err instanceof Error ? err.message : String(err),
      );
      return new Response("not found", { status: 404 });
    }
  });
}
