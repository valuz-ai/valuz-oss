import { fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  isPreviewCloseShortcut,
  usePreviewCloseShortcut,
} from "./use-preview-close-shortcut";

const keyEvent = (
  overrides: Partial<Parameters<typeof isPreviewCloseShortcut>[0]> = {},
) => ({
  altKey: false,
  ctrlKey: false,
  isComposing: false,
  key: "w",
  metaKey: false,
  shiftKey: false,
  ...overrides,
});

function Preview({ active, onClose }: { active: boolean; onClose: () => void }) {
  usePreviewCloseShortcut({ active, onClose });
  return null;
}

afterEach(() => {
  delete (window as Window & { valuzDesktop?: unknown }).valuzDesktop;
  vi.restoreAllMocks();
});

describe("preview close shortcut", () => {
  it("uses Command+W on Apple platforms and Control+W elsewhere", () => {
    expect(
      isPreviewCloseShortcut(keyEvent({ metaKey: true }), "darwin"),
    ).toBe(true);
    expect(
      isPreviewCloseShortcut(keyEvent({ ctrlKey: true }), "darwin"),
    ).toBe(false);
    expect(
      isPreviewCloseShortcut(keyEvent({ ctrlKey: true }), "win32"),
    ).toBe(true);
    expect(
      isPreviewCloseShortcut(keyEvent({ metaKey: true }), "linux"),
    ).toBe(false);
  });

  it("does not treat Escape, modified W, or composition as close", () => {
    expect(isPreviewCloseShortcut(keyEvent({ key: "Escape" }), "darwin")).toBe(
      false,
    );
    expect(
      isPreviewCloseShortcut(
        keyEvent({ metaKey: true, shiftKey: true }),
        "darwin",
      ),
    ).toBe(false);
    expect(
      isPreviewCloseShortcut(
        keyEvent({ ctrlKey: true, isComposing: true }),
        "win32",
      ),
    ).toBe(false);
  });

  it("closes only the most recently activated preview", () => {
    const closeBackground = vi.fn();
    const closeForeground = vi.fn();
    const { rerender } = render(
      <>
        <Preview active onClose={closeBackground} />
        <Preview active onClose={closeForeground} />
      </>,
    );

    const apple = /^(darwin|mac|iphone|ipad|ipod)/i.test(
      navigator.platform || navigator.userAgent,
    );
    fireEvent.keyDown(window, {
      key: "w",
      metaKey: apple,
      ctrlKey: !apple,
    });
    expect(closeForeground).toHaveBeenCalledOnce();
    expect(closeBackground).not.toHaveBeenCalled();

    rerender(
      <>
        <Preview active onClose={closeBackground} />
        <Preview active={false} onClose={closeForeground} />
      </>,
    );
    fireEvent.keyDown(window, {
      key: "w",
      metaKey: apple,
      ctrlKey: !apple,
    });
    expect(closeBackground).toHaveBeenCalledOnce();
  });

  it("handles Electron's native close request before closing the window", () => {
    let nativeClose: ((payload: unknown) => void) | undefined;
    const invoke = vi.fn(async () => undefined);
    const on = vi.fn((event: string, handler: (payload: unknown) => void) => {
      expect(event).toBe("desktop:preview-close-requested");
      nativeClose = handler;
    });
    const off = vi.fn();
    Object.defineProperty(window, "valuzDesktop", {
      configurable: true,
      value: {
        runtime: { platform: "darwin" },
        invoke,
        on,
        off,
      },
    });
    const closePreview = vi.fn();
    const { rerender } = render(
      <Preview active onClose={closePreview} />,
    );

    nativeClose?.(undefined);
    expect(closePreview).toHaveBeenCalledOnce();
    expect(invoke).not.toHaveBeenCalled();

    rerender(<Preview active={false} onClose={closePreview} />);
    nativeClose?.(undefined);
    expect(invoke).toHaveBeenCalledWith("window_close");
  });
});
