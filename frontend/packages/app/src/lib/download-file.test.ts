/**
 * Which of the three save paths a descriptor takes, and why.
 *
 * The interesting case is a cloud file: it must go to ``downloadUrl`` when the
 * deployment offers one, because navigating to the *render* URL cross-origin
 * ignores the ``download`` attribute and opens the file in a tab under the
 * storage key's name. The blob fallback only exists for deployments that have
 * no attachment address, and it must refuse files too big to hold in memory.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import type { PlatformCapabilities, ResolvedFileDescriptor } from "@valuz/core";

import { downloadResolvedFile, MAX_BLOB_DOWNLOAD_BYTES } from "./download-file";

function descriptor(
  overrides: Partial<ResolvedFileDescriptor> = {},
): ResolvedFileDescriptor {
  return {
    ref: "valuz-file:///w/u1/p/report.pptx",
    kind: "remote",
    absPath: null,
    url: "https://store.example/w/u1/p/obj?sig=render",
    downloadUrl: null,
    expiresAt: null,
    name: "report.pptx",
    mimeType: null,
    size: 1024,
    revision: "1-1024",
    exists: true,
    previewKind: "unsupported",
    capabilities: {
      canPreview: false,
      canDownload: true,
      canOpenExternal: false,
      canCopyContent: false,
    },
    error: null,
    ...overrides,
  };
}

function platform(
  overrides: Partial<PlatformCapabilities> = {},
): PlatformCapabilities {
  return {
    isElectron: false,
    isMac: false,
    ...overrides,
  } as PlatformCapabilities;
}

/** The href the code handed to the browser, captured off the anchor click. */
function captureAnchorClicks(): {
  hrefs: string[];
  names: string[];
  targets: string[];
} {
  const hrefs: string[] = [];
  const names: string[] = [];
  const targets: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    hrefs.push(this.href);
    names.push(this.download);
    targets.push(this.target);
  });
  return { hrefs, names, targets };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("remote files", () => {
  it("uses the attachment address when the deployment offers one", async () => {
    const clicks = captureAnchorClicks();
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const outcome = await downloadResolvedFile(
      descriptor({ downloadUrl: "https://store.example/obj?disp=attachment" }),
      platform(),
    );

    expect(outcome).toEqual({ ok: true, via: "address" });
    expect(clicks.hrefs).toEqual(["https://store.example/obj?disp=attachment"]);
    // Streamed by the browser — nothing is pulled into the page.
    expect(fetchSpy).not.toHaveBeenCalled();
    // A store that ever fails to send the attachment header would NAVIGATE a
    // same-tab click, taking the SPA and its unsaved state with it.
    expect(clicks.targets).toEqual(["_blank"]);
  });

  it("falls back to fetching the bytes when there is no attachment address", async () => {
    const clicks = captureAnchorClicks();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(new Blob(["x"]), { status: 200 }),
    );
    // jsdom implements neither half of the object-URL API.
    URL.createObjectURL = vi.fn(() => "blob:local");
    URL.revokeObjectURL = vi.fn();

    const outcome = await downloadResolvedFile(descriptor(), platform());

    expect(outcome).toEqual({ ok: true, via: "blob" });
    expect(clicks.hrefs).toEqual(["blob:local"]);
    // Same-origin blob, so the download attribute is what names the file —
    // and no new tab, which a popup blocker would eat.
    expect(clicks.names).toEqual(["report.pptx"]);
    expect(clicks.targets).toEqual([""]);
  });

  it("refuses the blob fallback for a file too large to hold in memory", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const outcome = await downloadResolvedFile(
      descriptor({ size: MAX_BLOB_DOWNLOAD_BYTES + 1 }),
      platform(),
    );

    // Distinct from ``unavailable``: the file is fine, the deployment is just
    // missing an attachment address, and the message has to say so.
    expect(outcome).toEqual({ ok: false, reason: "too_large" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("reports a failed fetch rather than silently doing nothing", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 403 }),
    );

    const outcome = await downloadResolvedFile(descriptor(), platform());

    expect(outcome).toEqual({
      ok: false,
      reason: "failed",
      message: "HTTP 403",
    });
  });
});

describe("local files", () => {
  const local = descriptor({
    kind: "local",
    absPath: "/Users/u/p/report.pptx",
    url: null,
  });

  it("asks the host to save a copy", async () => {
    const saveFileAs = vi.fn(async () => ({
      saved: true,
      path: "/out/r.pptx",
    }));

    const outcome = await downloadResolvedFile(local, platform({ saveFileAs }));

    expect(saveFileAs).toHaveBeenCalledWith(
      "/Users/u/p/report.pptx",
      "report.pptx",
    );
    expect(outcome).toEqual({
      ok: true,
      via: "saveAs",
      savedPath: "/out/r.pptx",
    });
  });

  it("distinguishes a dismissed dialog from a failure", async () => {
    const outcome = await downloadResolvedFile(
      local,
      platform({ saveFileAs: async () => ({ saved: false }) }),
    );
    expect(outcome).toEqual({ ok: false, reason: "cancelled" });
  });

  it("is unavailable in a host that cannot read local paths", async () => {
    // The browser-plus-local-project cell: no save capability, no address.
    const outcome = await downloadResolvedFile(local, platform());
    expect(outcome).toEqual({ ok: false, reason: "unavailable" });
  });
});

it("does not offer to download a file that is gone", async () => {
  const outcome = await downloadResolvedFile(
    descriptor({ exists: false, error: "not_found" }),
    platform(),
  );
  expect(outcome).toEqual({ ok: false, reason: "unavailable" });
});
