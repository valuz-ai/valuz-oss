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

/** Deck dimensions the fake reports; overridden per test. */
let deckSize = { width: 960, height: 540 };
let host: HTMLElement | null = null;

/** How many slides the fake deck has. */
let slides = 1;

const preview = vi.fn(async () => {
  // What the real library builds: one box per slide, sized from the options
  // it was handed — which is exactly what the renderer has to correct.
  for (let i = 0; i < slides; i++) {
    const box = document.createElement("div");
    box.className = "pptx-preview-slide-wrapper";
    host?.append(box);
  }
  return undefined;
});
const destroy = vi.fn();
const init = vi.fn((dom: HTMLElement, _options?: unknown) => {
  host = dom;
  return { slideCount: 1, preview, destroy, pptx: deckSize };
});

vi.mock("pptx-preview", () => ({
  init: (dom: HTMLElement, options: unknown) => init(dom, options),
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
  deckSize = { width: 960, height: 540 };
  slides = 1;
  host = null;
  Element.prototype.scrollIntoView = vi.fn();
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

  it("resizes the slide boxes to the deck's real aspect", async () => {
    // A 4:3 deck laid out in the provisional 16:9 box would letterbox, and the
    // box clips (``overflow: hidden``), so this is not cosmetic. The real
    // dimensions are only known after parsing, hence the correction.
    deckSize = { width: 1024, height: 768 };
    stubHostWidth(900);
    const view = render(
      <PresentationRenderer artifact={artifact} content={content} />,
    );

    await waitFor(() => expect(preview).toHaveBeenCalled());
    const box = view.container.querySelector<HTMLElement>(
      ".pptx-preview-slide-wrapper",
    );
    // 880 * 768 / 1024
    await waitFor(() => expect(box!.style.height).toBe("660px"));
  });

  it("offers a pager only when there is more than one slide", async () => {
    stubHostWidth(900);
    const view = render(
      <PresentationRenderer artifact={artifact} content={content} />,
    );
    await waitFor(() => expect(preview).toHaveBeenCalled());
    // A one-slide deck has nothing to page through; the bar would be noise.
    expect(view.queryByRole("status")).toBeNull();
  });

  it("pages to the next slide and says where it is", async () => {
    slides = 3;
    stubHostWidth(900);
    const view = render(
      <PresentationRenderer artifact={artifact} content={content} />,
    );
    await waitFor(() => expect(view.getByRole("status").textContent).toBe("1 / 3"));

    // Previous is unreachable from the first slide, next is not.
    expect(
      (view.getByLabelText("上一页") as HTMLButtonElement).disabled,
    ).toBe(true);
    view.getByLabelText("下一页").click();

    await waitFor(() => expect(view.getByRole("status").textContent).toBe("2 / 3"));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
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
