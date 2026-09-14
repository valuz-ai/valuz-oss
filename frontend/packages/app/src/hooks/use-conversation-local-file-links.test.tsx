/** @vitest-environment jsdom */
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { MarkdownContent } from "@valuz/ui";
import {
  isDefaultLocalFileHref,
  useConversationLocalFileLinks,
} from "./use-conversation-local-file-links";
import { ConversationLocalFileLinkProvider } from "./conversation-local-file-link-provider";

describe("useConversationLocalFileLinks", () => {
  it("previews project-local file links and strips markdown line suffixes", () => {
    const previewFile = vi.fn();
    const openFile = vi.fn();

    const { result } = renderHook(() =>
      useConversationLocalFileLinks({
        projectRootPath: "/Users/ada/project",
        previewFile,
        openFile,
      }),
    );

    expect(
      result.current.isLocalFileHref("/Users/ada/project/src/App.tsx:12"),
    ).toBe(true);

    act(() => {
      result.current.openLocalFileHref("/Users/ada/project/src/App.tsx:12");
    });

    expect(previewFile).toHaveBeenCalledWith("src/App.tsx");
    expect(openFile).not.toHaveBeenCalled();
  });

  it("opens absolute file links outside the active project in the system", () => {
    const previewFile = vi.fn();
    const openFile = vi.fn();

    const { result } = renderHook(() =>
      useConversationLocalFileLinks({
        projectRootPath: "/Users/ada/project",
        previewFile,
        openFile,
      }),
    );

    expect(
      result.current.isLocalFileHref("/Users/ada/Downloads/report.pdf"),
    ).toBe(true);

    act(() => {
      result.current.openLocalFileHref("/Users/ada/Downloads/report.pdf");
    });

    expect(openFile).toHaveBeenCalledWith("/Users/ada/Downloads/report.pdf");
    expect(previewFile).not.toHaveBeenCalled();
  });

  it("preserves a PDF page target when previewing a project file", () => {
    const previewFile = vi.fn();
    const openFile = vi.fn();

    const { result } = renderHook(() =>
      useConversationLocalFileLinks({
        projectRootPath: "/Users/ada/project",
        previewFile,
        openFile,
      }),
    );

    expect(
      result.current.resolveLocalFileHref(
        "/Users/ada/project/reports/plan.pdf#page=12&zoom=page-width",
      ),
    ).toEqual({
      kind: "preview",
      path: "reports/plan.pdf",
      target: { page: 12 },
    });

    act(() => {
      result.current.openLocalFileHref(
        "/Users/ada/project/reports/plan.pdf#page=12&zoom=page-width",
      );
    });

    expect(previewFile).toHaveBeenCalledWith("reports/plan.pdf", { page: 12 });
    expect(openFile).not.toHaveBeenCalled();
  });

  it("accepts query page targets and ignores invalid pages", () => {
    const previewFile = vi.fn();
    const openFile = vi.fn();

    const { result } = renderHook(() =>
      useConversationLocalFileLinks({
        projectRootPath: "/Users/ada/project",
        previewFile,
        openFile,
      }),
    );

    expect(
      result.current.resolveLocalFileHref(
        "/Users/ada/project/reports/plan.pdf?page=7",
      ),
    ).toEqual({
      kind: "preview",
      path: "reports/plan.pdf",
      target: { page: 7 },
    });
    expect(
      result.current.resolveLocalFileHref(
        "/Users/ada/project/reports/plan.pdf#page=0",
      ),
    ).toEqual({ kind: "preview", path: "reports/plan.pdf" });
  });

  it("renders outside absolute paths as blocked local links in managed cloud mode", () => {
    const previewFile = vi.fn();
    const openFile = vi.fn();
    const blockFile = vi.fn();

    const { result } = renderHook(() =>
      useConversationLocalFileLinks({
        projectRootPath: "/srv/valuz/projects/cloud-managed",
        runtimeMode: "managed",
        previewFile,
        openFile,
        blockFile,
      }),
    );

    expect(
      result.current.isLocalFileHref("/Users/ada/Downloads/report.pdf"),
    ).toBe(true);
    expect(
      result.current.resolveLocalFileHref("/Users/ada/Downloads/report.pdf"),
    ).toEqual({
      kind: "blocked",
      path: "/Users/ada/Downloads/report.pdf",
      reason: "managed_outside_project",
    });
    expect(
      result.current.isLocalFileHref(
        "/srv/valuz/projects/cloud-managed/report.md",
      ),
    ).toBe(true);

    act(() => {
      result.current.openLocalFileHref(
        "/srv/valuz/projects/cloud-managed/report.md:8",
      );
    });

    expect(previewFile).toHaveBeenCalledWith("report.md");
    expect(openFile).not.toHaveBeenCalled();

    act(() => {
      result.current.openLocalFileHref("/Users/ada/Downloads/report.pdf");
    });

    expect(blockFile).toHaveBeenCalledWith(
      "/Users/ada/Downloads/report.pdf",
      "managed_outside_project",
    );
    expect(openFile).not.toHaveBeenCalled();
  });

  it("resolves valuz-file:// refs on a POSIX host", () => {
    const previewFile = vi.fn();
    const openFile = vi.fn();

    const { result } = renderHook(() =>
      useConversationLocalFileLinks({
        projectRootPath: "/Users/ada/project",
        previewFile,
        openFile,
      }),
    );

    expect(
      result.current.resolveLocalFileHref(
        "valuz-file:///Users/ada/project/reports/q3.md",
      ),
    ).toEqual({ kind: "preview", path: "reports/q3.md" });
    expect(
      result.current.resolveLocalFileHref(
        "valuz-file:///Users/ada/Downloads/report.pdf",
      ),
    ).toEqual({ kind: "open", path: "/Users/ada/Downloads/report.pdf" });
  });

  // A Windows drive specifier is syntactically a one-character URI scheme, so
  // the "did normalization leave a scheme behind?" guard used to match `C:` and
  // drop every `valuz-file://` link a Windows client rendered. The link then
  // kept its raw scheme through the markdown pipeline, where the sanitizer's
  // protocol allowlist stripped the href and it rendered as "[blocked]".
  // `file:///C:/…` was exempt from that guard, which is why this was invisible
  // until a model emitted the scheme the system prompt actually teaches.
  describe("Windows drive-letter paths", () => {
    const root = "C:\\Users\\ada\\Valuz\\chats\\2026\\09\\13\\NZNNGGQZ";
    const renderWindows = (
      overrides: Partial<
        Parameters<typeof useConversationLocalFileLinks>[0]
      > = {},
    ) => {
      const previewFile = vi.fn();
      const openFile = vi.fn();
      const blockFile = vi.fn();
      const { result } = renderHook(() =>
        useConversationLocalFileLinks({
          projectRootPath: root,
          previewFile,
          openFile,
          blockFile,
          ...overrides,
        }),
      );
      return { result, previewFile, openFile, blockFile };
    };

    it("previews a valuz-file:// ref inside the project root", () => {
      const { result, previewFile } = renderWindows();

      expect(
        result.current.isLocalFileHref(
          "valuz-file:///C:/Users/ada/Valuz/chats/2026/09/13/NZNNGGQZ/看板.html",
        ),
      ).toBe(true);

      act(() => {
        result.current.openLocalFileHref(
          "valuz-file:///C:/Users/ada/Valuz/chats/2026/09/13/NZNNGGQZ/看板.html",
        );
      });

      expect(previewFile).toHaveBeenCalledWith("看板.html");
    });

    it("previews the .artifact copy a delivered artifact links to", () => {
      const { result } = renderWindows();

      expect(
        result.current.resolveLocalFileHref(
          "valuz-file:///C:/Users/ada/Valuz/chats/2026/09/13/NZNNGGQZ/.artifact/9AGPGVG5/v1/看板.html",
        ),
      ).toEqual({
        kind: "preview",
        path: ".artifact/9AGPGVG5/v1/看板.html",
      });
    });

    it("accepts the percent-encoded drive spelling buildFileRef emits", () => {
      const { result } = renderWindows();

      expect(
        result.current.resolveLocalFileHref(
          "valuz-file:///C%3A/Users/ada/Valuz/chats/2026/09/13/NZNNGGQZ/看板.html",
        ),
      ).toEqual({ kind: "preview", path: "看板.html" });
    });

    it("opens a drive path outside the project in the system", () => {
      const { result, openFile } = renderWindows();

      act(() => {
        result.current.openLocalFileHref(
          "valuz-file:///C:/Users/ada/Desktop/report.pdf",
        );
      });

      expect(openFile).toHaveBeenCalledWith("C:/Users/ada/Desktop/report.pdf");
    });

    it("still opens bare and file:// drive paths", () => {
      const { result } = renderWindows();

      expect(
        result.current.resolveLocalFileHref("C:\\Users\\ada\\Desktop\\x.html"),
      ).toEqual({ kind: "open", path: "C:\\Users\\ada\\Desktop\\x.html" });
      expect(
        result.current.resolveLocalFileHref(
          "file:///C:/Users/ada/Desktop/x.html",
        ),
      ).toEqual({ kind: "open", path: "C:/Users/ada/Desktop/x.html" });
    });

    it("blocks a drive path outside the project in managed cloud mode", () => {
      const { result } = renderWindows({ runtimeMode: "managed" });

      expect(
        result.current.resolveLocalFileHref(
          "valuz-file:///C:/Users/ada/Desktop/report.pdf",
        ),
      ).toEqual({
        kind: "blocked",
        path: "C:/Users/ada/Desktop/report.pdf",
        reason: "managed_outside_project",
      });
    });
  });

  it("still rejects hrefs that keep a real URI scheme after normalization", () => {
    const { result } = renderHook(() =>
      useConversationLocalFileLinks({
        projectRootPath: "/Users/ada/project",
        previewFile: vi.fn(),
        openFile: vi.fn(),
      }),
    );

    for (const href of [
      "https://example.com/report.pdf",
      "http://example.com/report.pdf",
      "mailto:ada@example.com",
      "data:text/plain,hello",
      "evidence://ev_mcp_abc123",
      "valuz-local://f/Users/ada/project/report.md",
    ]) {
      expect(result.current.resolveLocalFileHref(href)).toBeNull();
    }
  });

  it("allows an overlay provider to replace local file link handling", () => {
    const previewFile = vi.fn();
    const openFile = vi.fn();
    const overlayOpen = vi.fn();

    const wrapper = ({ children }: { children: ReactNode }) => (
      <ConversationLocalFileLinkProvider
        value={{
          isLocalFileHref: (href) => href.startsWith("valuz-local://"),
          openLocalFileHref: (href) => overlayOpen(href),
        }}
      >
        {children}
      </ConversationLocalFileLinkProvider>
    );

    const { result } = renderHook(
      () =>
        useConversationLocalFileLinks({
          projectRootPath: "/Users/ada/project",
          previewFile,
          openFile,
        }),
      { wrapper },
    );

    expect(result.current.isLocalFileHref("valuz-local://artifact/123")).toBe(
      true,
    );

    act(() => {
      result.current.openLocalFileHref("valuz-local://artifact/123");
    });

    expect(overlayOpen).toHaveBeenCalledWith("valuz-local://artifact/123");
    expect(previewFile).not.toHaveBeenCalled();
    expect(openFile).not.toHaveBeenCalled();
  });
});

/**
 * The symptom the resolver bug actually produced, end to end.
 *
 * ``MarkdownContent`` only rewrites a local-file link into a href the markdown
 * sanitizer will keep (``https://valuz.local-file.invalid/…``) when
 * ``isLocalFileHref`` says it is one. A ``false`` there is not a no-op: the raw
 * ``valuz-file:`` href reaches rehype-sanitize, whose protocol allowlist is
 * http/https/irc/ircs/mailto/xmpp/tel, the attribute is dropped, and
 * rehype-harden replaces the whole anchor with "<text> [blocked]".
 *
 * Wired here with the REAL resolver (the ui package's own tests stub the
 * predicate, so they cannot see this) — that seam is the entire bug.
 */
describe("MarkdownContent with the default local-file resolver", () => {
  const WINDOWS_ROOT = "C:\\Users\\ada\\Valuz\\chats\\2026\\09\\13\\NZNNGGQZ";
  const POSIX_ROOT = "/Users/ada/Valuz/chats/2026/09/13/NZNNGGQZ";

  const renderLink = (content: string, projectRootPath: string) => {
    const onLocalFileLinkClick = vi.fn();
    render(
      <MarkdownContent
        content={content}
        mode="static"
        onLocalFileLinkClick={onLocalFileLinkClick}
        isLocalFileHref={(href) =>
          isDefaultLocalFileHref(href, projectRootPath)
        }
      />,
    );
    return { onLocalFileLinkClick };
  };

  it("renders a Windows valuz-file:// link as a clickable link", () => {
    const href =
      "valuz-file:///C:/Users/ada/Valuz/chats/2026/09/13/NZNNGGQZ/.artifact/9AGPGVG5/v1/看板.html";
    const { onLocalFileLinkClick } = renderLink(
      `做好了：[看板.html](${href})`,
      WINDOWS_ROOT,
    );

    expect(screen.queryByText(/\[blocked\]/)).toBeNull();
    const link = screen.getByRole("link", { name: "看板.html" });
    expect(link.getAttribute("href")).toBeTruthy();

    fireEvent.click(link);
    expect(onLocalFileLinkClick).toHaveBeenCalledWith(href);
  });

  it("renders a POSIX valuz-file:// link as a clickable link", () => {
    const href =
      "valuz-file:///Users/ada/Valuz/chats/2026/09/13/NZNNGGQZ/看板.html";
    const { onLocalFileLinkClick } = renderLink(
      `做好了：[看板.html](${href})`,
      POSIX_ROOT,
    );

    expect(screen.queryByText(/\[blocked\]/)).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "看板.html" }));
    expect(onLocalFileLinkClick).toHaveBeenCalledWith(href);
  });

  it("leaves a web link to the normal external-link path", () => {
    const { onLocalFileLinkClick } = renderLink(
      "[the docs](https://example.com/report.pdf)",
      WINDOWS_ROOT,
    );

    expect(
      screen.getByRole("link", { name: "the docs" }).getAttribute("href"),
    ).toBe("https://example.com/report.pdf");
    expect(onLocalFileLinkClick).not.toHaveBeenCalled();
  });

  it("still blocks a scheme the sanitizer does not allow", () => {
    renderLink("[not a file](ftp://example.com/report.pdf)", WINDOWS_ROOT);

    expect(screen.queryByRole("link", { name: "not a file" })).toBeNull();
    expect(document.body.textContent).toContain("[blocked]");
  });
});
