/**
 * Save a resolved file to the user's machine.
 *
 * The counterpart to ``resolve-artifact.ts``: that module turns a resolve
 * descriptor into something renderable, this one turns the same descriptor into
 * a saved file. Both take the descriptor and branch on ``kind`` — neither
 * branches on edition or deployment.
 *
 * Three paths, in order of preference:
 *
 * 1. ``downloadUrl`` — an address whose response carries
 *    ``Content-Disposition: attachment``. This is the only path that works for
 *    a large cloud file: the browser streams it straight to disk. It exists as
 *    a *separate* address because a cross-origin ``<a download>`` has its
 *    ``download`` attribute ignored, so navigating to the render URL would open
 *    the file in a tab, named after the storage key.
 * 2. Blob fallback — fetch the render URL and save the bytes. Used when the
 *    deployment's resolver offers no attachment address. Buffers the whole file
 *    in memory and needs the object store to allow cross-origin GET, so it is a
 *    fallback and not the default.
 * 3. ``saveFileAs`` — the file is already on this machine (bundled desktop), so
 *    the honest action is a save-a-copy dialog rather than a fake download.
 *
 * See docs/design/file-address-resolution.md.
 */

import type { PlatformCapabilities, ResolvedFileDescriptor } from "@valuz/core";

export type DownloadOutcome =
  | { ok: true; via: "address" | "blob" | "saveAs"; savedPath?: string }
  /** The user dismissed the save dialog. Not an error — say nothing. */
  | { ok: false; reason: "cancelled" }
  /** No path to the bytes from this client (e.g. a local file in a browser). */
  | { ok: false; reason: "unavailable" }
  /**
   * Only reachable through the blob fallback, which has to hold the whole file
   * in memory. Distinct from ``unavailable`` because the file is perfectly
   * fine — the deployment is missing an attachment address.
   */
  | { ok: false; reason: "too_large" }
  | { ok: false; reason: "failed"; message: string };

/** Bytes above this are not pulled through the blob fallback. */
export const MAX_BLOB_DOWNLOAD_BYTES = 100 * 1024 * 1024;

function clickDownloadAnchor(
  url: string,
  fileName: string,
  { sameTab }: { sameTab: boolean },
): void {
  const anchor = document.createElement("a");
  anchor.href = url;
  // Honoured for the blob path (same-origin). Ignored cross-origin, where the
  // response's Content-Disposition names the file instead.
  anchor.download = fileName;
  anchor.rel = "noopener";
  // A remote address is only a download as long as the store actually sends
  // ``Content-Disposition: attachment``. If it ever does not, a same-tab click
  // NAVIGATES — the user loses the conversation, the composer draft, and every
  // other piece of client state. A new tab degrades to a stray tab instead.
  // The blob path needs no such hedge: the download attribute is honoured
  // there, and a new tab would be blocked as a popup.
  if (!sameTab) anchor.target = "_blank";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

async function downloadViaBlob(
  url: string,
  fileName: string,
  size: number | null,
): Promise<DownloadOutcome> {
  // This path holds the entire file in memory, so refuse the ones that would
  // take the tab down with them. The attachment-address path has no such limit
  // — a deployment that hits this is missing a resolver, not a big file.
  if (size != null && size > MAX_BLOB_DOWNLOAD_BYTES) {
    return { ok: false, reason: "too_large" };
  }
  let objectUrl: string | null = null;
  try {
    const response = await fetch(url);
    if (!response.ok) {
      return {
        ok: false,
        reason: "failed",
        message: `HTTP ${response.status}`,
      };
    }
    objectUrl = URL.createObjectURL(await response.blob());
    clickDownloadAnchor(objectUrl, fileName, { sameTab: true });
    return { ok: true, via: "blob" };
  } catch (cause) {
    return {
      ok: false,
      reason: "failed",
      message: cause instanceof Error ? cause.message : String(cause),
    };
  } finally {
    // The click has already handed the URL to the browser's download machinery
    // by the time this runs; revoking on the next tick keeps the object out of
    // memory without racing that handoff.
    const created = objectUrl;
    if (created) setTimeout(() => URL.revokeObjectURL(created), 60_000);
  }
}

/**
 * Save ``descriptor`` to the user's machine.
 *
 * Pass a **freshly resolved** descriptor: a remote address is short-lived, so
 * one captured when a preview opened may already have expired. Re-resolve on
 * the click — the same rule the viewer's retry buttons follow.
 */
export async function downloadResolvedFile(
  descriptor: ResolvedFileDescriptor,
  platform: PlatformCapabilities,
): Promise<DownloadOutcome> {
  if (!descriptor.exists || descriptor.error) {
    return { ok: false, reason: "unavailable" };
  }
  const fileName = descriptor.name || "download";

  if (descriptor.kind === "remote") {
    if (descriptor.downloadUrl) {
      clickDownloadAnchor(descriptor.downloadUrl, fileName, { sameTab: false });
      return { ok: true, via: "address" };
    }
    if (descriptor.url) {
      return downloadViaBlob(descriptor.url, fileName, descriptor.size);
    }
    return { ok: false, reason: "unavailable" };
  }

  if (
    descriptor.kind === "local" &&
    descriptor.absPath &&
    platform.saveFileAs
  ) {
    const result = await platform.saveFileAs(descriptor.absPath, fileName);
    if (result.saved)
      return { ok: true, via: "saveAs", savedPath: result.path };
    if (result.error)
      return { ok: false, reason: "failed", message: result.error };
    return { ok: false, reason: "cancelled" };
  }

  // A local path with no host able to read it — the browser-plus-local-project
  // cell the address design already records as degraded.
  return { ok: false, reason: "unavailable" };
}
