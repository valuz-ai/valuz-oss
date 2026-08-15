import { fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { UseArtifactFileResult } from "../hooks/use-artifact-file";
import { ArtifactSplitPane } from "./ArtifactSplitPane";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ArtifactSplitPane preview shortcut", () => {
  it("closes the active tab instead of dismissing every open document", () => {
    vi.stubGlobal(
      "ResizeObserver",
      class ResizeObserver {
        private readonly callback: globalThis.ResizeObserverCallback;

        constructor(callback: globalThis.ResizeObserverCallback) {
          this.callback = callback;
        }

        observe(target: Element) {
          const rect = {
            width: 1_000,
            height: 600,
            top: 0,
            left: 0,
            x: 0,
            y: 0,
          };
          this.callback(
            [
              {
                target,
                contentRect: rect,
                borderBoxSize: [
                  { inlineSize: rect.width, blockSize: rect.height },
                ],
                contentBoxSize: [
                  { inlineSize: rect.width, blockSize: rect.height },
                ],
              },
            ] as unknown as globalThis.ResizeObserverEntry[],
            this as unknown as globalThis.ResizeObserver,
          );
        }

        unobserve() {}
        disconnect() {}
      },
    );

    const closeTab = vi.fn();
    const closeAll = vi.fn();
    const file: UseArtifactFileResult = {
      tabs: [
        {
          path: "first.md",
          name: "first.md",
          artifact: null,
          content: null,
          target: null,
          loading: false,
          error: null,
        },
        {
          path: "second.pdf",
          name: "second.pdf",
          artifact: null,
          content: null,
          target: null,
          loading: false,
          error: null,
        },
      ],
      activePath: "second.pdf",
      activate: vi.fn(),
      closeTab,
      selectedPath: "second.pdf",
      artifact: null,
      content: null,
      target: null,
      loading: false,
      error: null,
      open: vi.fn(async () => undefined),
      reload: vi.fn(async () => undefined),
      close: closeAll,
    };

    render(
      <ArtifactSplitPane
        file={file}
        onReload={vi.fn()}
        onClose={closeAll}
        onCopyContent={vi.fn()}
        onOpenExternal={vi.fn()}
      >
        <div>conversation</div>
      </ArtifactSplitPane>,
    );

    const apple = /^(darwin|mac|iphone|ipad|ipod)/i.test(
      navigator.platform || navigator.userAgent,
    );
    fireEvent.keyDown(window, {
      key: "w",
      metaKey: apple,
      ctrlKey: !apple,
    });

    expect(closeTab).toHaveBeenCalledWith("second.pdf");
    expect(closeAll).not.toHaveBeenCalled();
  });
});
