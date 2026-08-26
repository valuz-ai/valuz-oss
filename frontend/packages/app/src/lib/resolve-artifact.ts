/**
 * Turn a ``/v1/files/resolve`` descriptor into the ``{ artifact, content }`` shape
 * the ArtifactViewerShell already consumes — so the viewer, copy, and state code
 * stay unchanged while the *content source* moves from the backend content
 * endpoint to an access address (remote presigned URL / local ``valuz-local://``).
 * The backend never proxies bytes: the client fetches from the address here.
 * See docs/design/file-address-resolution.md.
 */

import {
  buildLocalFileUrl,
  type ArtifactContent,
  type ArtifactDescriptor,
  type ArtifactFileResponse,
  type ArtifactPreviewKind,
  type PlatformCapabilities,
  type ResolvedFileDescriptor,
} from "@valuz/core";

const TEXT_KINDS = new Set(["markdown", "code", "html", "plain"]);
const BINARY_KINDS = new Set(["image", "pdf", "media", "docx", "spreadsheet"]);
export const MAX_TEXT_PREVIEW_BYTES = 5 * 1024 * 1024;

async function readResponseTextPreview(
  response: Response,
  signal?: AbortSignal,
): Promise<{ content: string; truncated: boolean }> {
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  const declaredSize = Number(response.headers.get("content-length"));
  const reader = response.body?.getReader();
  if (!reader) {
    const bytes = new Uint8Array(await response.arrayBuffer());
    const truncated = bytes.byteLength > MAX_TEXT_PREVIEW_BYTES;
    return {
      content: new TextDecoder().decode(
        truncated ? bytes.subarray(0, MAX_TEXT_PREVIEW_BYTES) : bytes,
      ),
      truncated,
    };
  }

  const chunks: Uint8Array[] = [];
  let total = 0;
  let truncated = Number.isFinite(declaredSize) && declaredSize > MAX_TEXT_PREVIEW_BYTES;
  while (total < MAX_TEXT_PREVIEW_BYTES) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const { done, value } = await reader.read();
    if (done) break;
    const remaining = MAX_TEXT_PREVIEW_BYTES - total;
    if (value.byteLength > remaining) {
      chunks.push(value.subarray(0, remaining));
      total += remaining;
      truncated = true;
      break;
    }
    chunks.push(value);
    total += value.byteLength;
  }
  if (total >= MAX_TEXT_PREVIEW_BYTES && !truncated) {
    const next = await reader.read();
    truncated ||= !next.done;
  }
  if (truncated) await reader.cancel();

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { content: new TextDecoder().decode(bytes), truncated };
}

function extensionOf(name: string): string | null {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : null;
}

/** The URL the client can fetch/render this file from, or null if unreachable. */
function addressUrl(
  d: ResolvedFileDescriptor,
  platform: PlatformCapabilities,
): string | null {
  if (d.kind === "remote") return d.url;
  if (d.kind === "local" && d.absPath && platform.isElectron) {
    return buildLocalFileUrl(d.absPath);
  }
  return null;
}

async function buildContent(
  d: ResolvedFileDescriptor,
  previewKind: ArtifactPreviewKind,
  platform: PlatformCapabilities,
  signal?: AbortSignal,
): Promise<ArtifactContent> {
  if (!d.exists || d.error) {
    return { kind: "external", reason: d.error ?? "not_found" };
  }

  const url = addressUrl(d, platform);

  if (TEXT_KINDS.has(previewKind)) {
    // Remote: fetch text directly from the presigned URL (client → object store).
    if (d.kind === "remote" && d.url) {
      try {
        const res = await fetch(d.url, { signal });
        const text = await readResponseTextPreview(res, signal);
        return {
          kind: "text",
          encoding: "utf-8",
          content: text.content,
          truncated: text.truncated,
        };
      } catch (cause) {
        if (signal?.aborted) throw cause;
        return { kind: "external", reason: "fetch_failed", openUrl: d.url };
      }
    }
    // Local desktop: read via IPC (no backend proxy).
    if (d.kind === "local" && d.absPath && platform.readFileContent) {
      const { content, truncated } = await platform.readFileContent(d.absPath);
      if (content != null) {
        return {
          kind: "text",
          encoding: "utf-8",
          content,
          truncated,
        };
      }
    }
    // Local file in a plain browser can't be read: degrade to metadata.
    return {
      kind: "external",
      reason: "open_in_desktop",
      openUrl: url ?? undefined,
    };
  }

  if (BINARY_KINDS.has(previewKind)) {
    if (url) {
      return {
        kind: "binary",
        openUrl: url,
        mimeType: d.mimeType ?? "application/octet-stream",
        size: d.size,
      };
    }
    return { kind: "external", reason: "open_in_desktop" };
  }

  return { kind: "external", reason: "unsupported", openUrl: url ?? undefined };
}

/** Build the shell's ``{ artifact, content }`` from a resolve descriptor. */
export async function resolvedToArtifactFile(
  d: ResolvedFileDescriptor,
  opts: {
    projectId: string;
    relPath: string;
    platform: PlatformCapabilities;
    signal?: AbortSignal;
  },
): Promise<ArtifactFileResponse> {
  const previewKind = (d.previewKind || "unsupported") as ArtifactPreviewKind;
  const artifact: ArtifactDescriptor = {
    id: `project_file:${opts.projectId}:${opts.relPath}`,
    kind: "project_file",
    projectId: opts.projectId,
    path: opts.relPath,
    name: d.name,
    mimeType: d.mimeType,
    extension: extensionOf(d.name),
    size: d.size,
    previewKind,
    capabilities: {
      canPreview: d.capabilities.canPreview,
      canEdit: false,
      // Only offer "open externally" when a local file is on a desktop client.
      canOpenExternal:
        d.capabilities.canOpenExternal && !!opts.platform.isElectron,
      canCopyContent: d.capabilities.canCopyContent,
      canDownload: d.capabilities.canDownload,
    },
  };
  const content = await buildContent(d, previewKind, opts.platform, opts.signal);
  return { artifact, content };
}
