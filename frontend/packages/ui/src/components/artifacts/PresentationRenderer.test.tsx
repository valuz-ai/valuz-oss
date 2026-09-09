/** @vitest-environment jsdom */
/**
 * The renderer's contract with the library, which the library's own output
 * cannot tell us: slides are laid out once at a concrete pixel width, so
 * measuring has to happen BEFORE the first render and a zero-width host must
 * not consume the file — otherwise the deck renders as a stack of empty
 * slides and never recovers.
 */

import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ArtifactDescriptor } from "./artifact-viewer.types";
import { PresentationRenderer } from "./PresentationRenderer";

const preview = vi.fn(async () => undefined);
const destroy = vi.fn();
const init = vi.fn(() => ({ slideCount: 1, preview, destroy }));

vi.mock("pptx-preview", () => ({
  init: (...a: unknown[]) => init(...(a as [])),
}));

const artifact: ArtifactDescriptor = {
  id: "deck",
  kind: "project_file",
  name: "A股早盘行情.pptx",
  previewKind: "presentation",
  capabilities: {
    canPreview: true,
    canEdit: false,
    canOpenExternal: false,
    canCopyContent: false,
    canDownload: true,
  },
};

const content = {
  kind: "binary" as const,
  openUrl: "https://store.example/deck.pptx",
  mimeType:
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
};

/** jsdom has no layout, so the measured width is whatever we say it is. */
function stubHostWidth(px: number) {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => px,
  });
}

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(new ArrayBuffer(8), { status: 200 })),
  );
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("PresentationRenderer", () => {
  it("renders at the measured width, in list mode", async () => {
    stubHostWidth(900);
    render(<PresentationRenderer artifact={artifact} content={content} />);

    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1));
    const [, options] = init.mock.calls[0] as unknown as [
      HTMLElement,
      { width: number; height: number; mode: string },
    ];
    // Rounded to a step so dragging the split pane does not re-parse the file
    // on every frame.
    expect(options.width).toBe(880);
    expect(options.height).toBe(495); // 16:9
    // "slide" mode would add the library's own pager next to the viewer's.
    expect(options.mode).toBe("list");
  });

  it("does not consume the file before the host has a width", async () => {
    stubHostWidth(0);
    render(<PresentationRenderer artifact={artifact} content={content} />);

    await new Promise((r) => setTimeout(r, 0));
    expect(fetch).not.toHaveBeenCalled();
    expect(init).not.toHaveBeenCalled();
  });

  it("tears the previewer down on unmount", async () => {
    stubHostWidth(900);
    const view = render(
      <PresentationRenderer artifact={artifact} content={content} />,
    );
    await waitFor(() => expect(preview).toHaveBeenCalled());

    view.unmount();
    // Clearing the node alone would leak the library's own listeners.
    expect(destroy).toHaveBeenCalledTimes(1);
  });

  it("surfaces a failed read instead of an empty pane", async () => {
    stubHostWidth(900);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 403 })),
    );
    const view = render(
      <PresentationRenderer artifact={artifact} content={content} />,
    );

    await waitFor(() =>
      expect(view.container.querySelector('[role="alert"]')).toBeTruthy(),
    );
    expect(init).not.toHaveBeenCalled();
  });
});
