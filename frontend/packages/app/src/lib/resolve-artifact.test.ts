import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  PlatformCapabilities,
  ResolvedFileDescriptor,
} from "@valuz/core";
import {
  MAX_TEXT_PREVIEW_BYTES,
  resolvedToArtifactFile,
} from "./resolve-artifact";

function descriptor(overrides: Partial<ResolvedFileDescriptor> = {}): ResolvedFileDescriptor {
  return {
    ref: "valuz-file:///root/report.md",
    kind: "remote",
    absPath: null,
    url: "https://files.example/report.md",
    expiresAt: null,
    name: "report.md",
    mimeType: "text/markdown",
    size: 12,
    exists: true,
    previewKind: "markdown",
    capabilities: {
      canPreview: true,
      canDownload: true,
      canOpenExternal: false,
      canCopyContent: true,
    },
    error: null,
    ...overrides,
  };
}

const platform = { isElectron: false, isMac: false } as PlatformCapabilities;

afterEach(() => vi.restoreAllMocks());

describe("resolvedToArtifactFile", () => {
  it("does not render a remote HTTP error body as artifact text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("Access denied", { status: 403 }),
    );

    const result = await resolvedToArtifactFile(descriptor(), {
      projectId: "p1",
      relPath: "report.md",
      platform,
    });

    expect(result.content).toEqual({
      kind: "external",
      reason: "fetch_failed",
      openUrl: "https://files.example/report.md",
    });
  });

  it("marks remote text as truncated from its declared size", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("preview", {
        headers: {
          "content-length": String(MAX_TEXT_PREVIEW_BYTES + 1),
        },
      }),
    );

    const result = await resolvedToArtifactFile(descriptor(), {
      projectId: "p1",
      relPath: "report.md",
      platform,
    });

    expect(result.content).toMatchObject({
      kind: "text",
      content: "preview",
      truncated: true,
    });
  });

  it("uses the structured truncation flag returned by desktop IPC", async () => {
    const localPlatform = {
      isElectron: true,
      isMac: false,
      readFileContent: vi.fn().mockResolvedValue({
        content: "preview",
        truncated: true,
      }),
    } as unknown as PlatformCapabilities;

    const result = await resolvedToArtifactFile(
      descriptor({ kind: "local", absPath: "/root/report.md", url: null }),
      { projectId: "p1", relPath: "report.md", platform: localPlatform },
    );

    expect(result.content).toMatchObject({ kind: "text", truncated: true });
  });
});
