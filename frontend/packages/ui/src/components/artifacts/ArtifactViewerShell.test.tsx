import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { setLocale } from "@valuz/shared/i18n";

import {
  ArtifactRenderer,
  ArtifactViewerShell,
  type ArtifactDescriptor,
} from "./ArtifactViewerShell";

vi.mock("../reader/PdfDocumentRenderer", () => ({
  PdfDocumentRenderer: ({
    url,
    title,
    location,
  }: {
    url: string;
    title: string;
    location?: { page?: number };
  }) => (
    <div
      data-testid="pdfjs-document"
      data-url={url}
      data-page={location?.page}
      aria-label={title}
    />
  ),
}));

beforeAll(() => {
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => new DOMRect(),
  });
});

function artifact(
  capabilities: Partial<ArtifactDescriptor["capabilities"]> = {},
): ArtifactDescriptor {
  return {
    id: "artifact:test",
    kind: "project_file",
    path: "notes.txt",
    name: "notes.txt",
    previewKind: "unsupported",
    capabilities: {
      canPreview: false,
      canEdit: false,
      canOpenExternal: false,
      canCopyContent: false,
      canDownload: false,
      ...capabilities,
    },
  };
}

describe("ArtifactViewerShell", () => {
  it("renders Markdown source in the wrapped code editor", async () => {
    render(
      <ArtifactViewerShell
        artifact={{
          ...artifact({ canPreview: true }),
          previewKind: "markdown",
          mimeType: "text/markdown",
          name: "notes.md",
        }}
        content={{
          kind: "text",
          encoding: "utf-8",
          content: "# Heading",
          truncated: false,
        }}
      />,
    );

    const shell = screen.getByRole("article");
    const titleActions = within(shell.querySelector("header")!);
    const preview = titleActions.getByRole("button", { name: "预览" });
    const source = titleActions.getByRole("button", { name: "源代码" });

    expect(preview.getAttribute("aria-pressed")).toBe("true");
    expect(source.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getAllByText("Markdown")).toHaveLength(1);

    fireEvent.click(source);

    expect(preview.getAttribute("aria-pressed")).toBe("false");
    expect(source.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getAllByText("Markdown")).toHaveLength(1);
    await waitFor(() =>
      expect(shell.querySelector(".cm-editor")).not.toBeNull(),
    );
    expect(shell.querySelector(".cm-lineNumbers")).not.toBeNull();
    expect(shell.querySelector(".cm-activeLine")).not.toBeNull();
    expect(shell.querySelector(".cm-lineWrapping")).not.toBeNull();
  });

  it("renders HTML source with line numbers and active-line highlighting", async () => {
    render(
      <ArtifactViewerShell
        artifact={{
          ...artifact({ canPreview: true }),
          previewKind: "html",
          mimeType: "text/html",
          name: "report.html",
        }}
        content={{
          kind: "text",
          encoding: "utf-8",
          content: "<main>\n  <h1>Report</h1>\n</main>",
          truncated: false,
        }}
      />,
    );

    const shell = screen.getByRole("article");
    fireEvent.click(
      within(shell.querySelector("header")!).getByRole("button", {
        name: "源代码",
      }),
    );

    await waitFor(() =>
      expect(shell.querySelector(".cm-editor")).not.toBeNull(),
    );
    expect(shell.querySelector(".cm-lineNumbers")).not.toBeNull();
    expect(shell.querySelector(".cm-activeLine")).not.toBeNull();
    expect(shell.querySelector(".cm-lineWrapping")).not.toBeNull();
  });

  it("does not draw a second frame when embedded in a panel", () => {
    render(
      <ArtifactViewerShell
        artifact={artifact()}
        content={{ kind: "external", reason: "unsupported" }}
        framed={false}
      />,
    );

    const shell = screen.getByRole("article");
    expect(shell.classList.contains("border")).toBe(false);
    expect(shell.classList.contains("rounded-[14px]")).toBe(false);
    expect(shell.classList.contains("shadow-sm")).toBe(false);
  });

  it("announces preview errors and exposes a retry action", () => {
    const onReload = vi.fn();
    render(
      <ArtifactViewerShell
        artifact={null}
        content={null}
        error="读取失败"
        onReload={onReload}
      />,
    );

    expect(screen.getByRole("alert").textContent).toContain("读取失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onReload).toHaveBeenCalledOnce();
  });

  it("blocks external-open controls and shortcuts without capability", () => {
    const onOpenExternal = vi.fn();
    render(
      <ArtifactViewerShell
        artifact={artifact()}
        content={{ kind: "external", reason: "unsupported" }}
        onOpenExternal={onOpenExternal}
      />,
    );

    expect(
      (screen.getByRole("button", {
        name: "外部打开",
      }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.keyDown(screen.getByRole("article"), {
      key: "o",
      metaKey: true,
      shiftKey: true,
    });
    expect(onOpenExternal).not.toHaveBeenCalled();
  });

  it("focuses a newly opened artifact and supports its external shortcut", async () => {
    const onOpenExternal = vi.fn();
    render(
      <ArtifactViewerShell
        artifact={artifact({ canOpenExternal: true })}
        content={{ kind: "external", reason: "unsupported" }}
        onOpenExternal={onOpenExternal}
      />,
    );

    const shell = screen.getByRole("article");
    await waitFor(() => expect(document.activeElement).toBe(shell));
    fireEvent.keyDown(shell, {
      key: "o",
      ctrlKey: true,
      shiftKey: true,
    });
    expect(onOpenExternal).toHaveBeenCalledOnce();
  });

  it("surfaces image loading failures", () => {
    render(
      <ArtifactViewerShell
        artifact={{
          ...artifact(),
          previewKind: "image",
          mimeType: "image/png",
          name: "preview.png",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.png",
          mimeType: "image/png",
        }}
      />,
    );

    expect(screen.getByRole("status").textContent).toContain("正在加载图片");
    fireEvent.error(screen.getByRole("img", { name: "preview.png" }));
    expect(screen.getByRole("alert").textContent).toContain("无法加载图片");
  });

  it("moves image zoom controls into the title actions", () => {
    render(
      <ArtifactViewerShell
        artifact={{
          ...artifact(),
          previewKind: "image",
          mimeType: "image/png",
          name: "preview.png",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.png",
          mimeType: "image/png",
        }}
      />,
    );

    const shell = screen.getByRole("article");
    const titleActions = within(shell.querySelector("header")!);
    expect(
      titleActions.getByRole("button", { name: "缩小图片" }),
    ).not.toBeNull();
    expect(
      titleActions.getByRole("button", { name: "放大图片" }),
    ).not.toBeNull();
    expect(screen.getAllByText("Image")).toHaveLength(1);
    expect(
      titleActions.getByRole("button", { name: "图片适合窗口" }).textContent,
    ).toBe("适合窗口");

    fireEvent.click(titleActions.getByRole("button", { name: "放大图片" }));
    expect(
      titleActions.getByRole("button", { name: "图片适合窗口" }).textContent,
    ).toBe("125%");
  });

  it("localizes the standalone image fit control", () => {
    render(
      <ArtifactRenderer
        artifact={{
          ...artifact(),
          previewKind: "image",
          mimeType: "image/png",
          name: "preview.png",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.png",
          mimeType: "image/png",
        }}
      />,
    );

    expect(
      screen.getByRole("button", { name: "图片适合窗口" }).textContent,
    ).toBe("适合窗口");
  });

  it("renders the image fit control in English", () => {
    setLocale("en-US");
    const view = render(
      <ArtifactRenderer
        artifact={{
          ...artifact(),
          previewKind: "image",
          mimeType: "image/png",
          name: "preview.png",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.png",
          mimeType: "image/png",
        }}
      />,
    );

    try {
      expect(
        screen.getByRole("button", { name: "Fit image to window" })
          .textContent,
      ).toBe("Fit to window");
    } finally {
      view.unmount();
      setLocale("zh-CN");
    }
  });

  it("surfaces media loading failures", () => {
    const { container } = render(
      <ArtifactViewerShell
        artifact={{
          ...artifact(),
          previewKind: "media",
          mimeType: "video/mp4",
          name: "preview.mp4",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.mp4",
          mimeType: "video/mp4",
        }}
      />,
    );

    const video = container.querySelector("video");
    expect(video).not.toBeNull();
    fireEvent.error(video!);
    expect(screen.getByRole("alert").textContent).toContain("无法加载媒体文件");
  });

  it("renders PDFs with PDF.js at the requested one-based page", () => {
    render(
      <ArtifactViewerShell
        artifact={{
          ...artifact(),
          previewKind: "pdf",
          mimeType: "application/pdf",
          name: "preview.pdf",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.pdf#zoom=page-width",
          mimeType: "application/pdf",
        }}
        target={{ page: 12 }}
      />,
    );

    const pdf = screen.getByTestId("pdfjs-document");
    expect(pdf.getAttribute("data-url")).toBe(
      "https://example.invalid/preview.pdf#zoom=page-width",
    );
    expect(pdf.getAttribute("data-page")).toBe("12");
    expect(screen.queryByTitle("preview.pdf")).toBeNull();
  });

  it("offers a re-resolving retry when an image fails to load", () => {
    const onReload = vi.fn();
    render(
      <ArtifactViewerShell
        artifact={{
          ...artifact(),
          previewKind: "image",
          mimeType: "image/png",
          name: "preview.png",
        }}
        content={{
          kind: "binary",
          openUrl: "https://example.invalid/preview.png",
          mimeType: "image/png",
        }}
        onReload={onReload}
      />,
    );

    fireEvent.error(screen.getByRole("img", { name: "preview.png" }));
    fireEvent.click(
      within(screen.getByRole("alert")).getByRole("button", { name: "重试" }),
    );

    expect(onReload).toHaveBeenCalledOnce();
  });

  it("exposes fullscreen controls and a keyboard shortcut for PDFs", () => {
    const originalRequestFullscreen = Object.getOwnPropertyDescriptor(
      Element.prototype,
      "requestFullscreen",
    );
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(Element.prototype, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });

    try {
      render(
        <ArtifactViewerShell
          artifact={{
            ...artifact(),
            previewKind: "pdf",
            mimeType: "application/pdf",
            name: "preview.pdf",
          }}
          content={{
            kind: "binary",
            openUrl: "https://example.invalid/preview.pdf",
            mimeType: "application/pdf",
          }}
        />,
      );

      fireEvent.click(screen.getByRole("button", { name: "进入全屏" }));
      expect(requestFullscreen).toHaveBeenCalledOnce();
      fireEvent.keyDown(screen.getByRole("article"), {
        key: "f",
        ctrlKey: true,
        shiftKey: true,
      });
      expect(requestFullscreen).toHaveBeenCalledTimes(2);
    } finally {
      if (originalRequestFullscreen) {
        Object.defineProperty(
          Element.prototype,
          "requestFullscreen",
          originalRequestFullscreen,
        );
      } else {
        Reflect.deleteProperty(Element.prototype, "requestFullscreen");
      }
    }
  });
});
