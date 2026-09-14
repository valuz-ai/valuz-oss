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
      // Build the options FIRST and hand back the ones actually wired into the
      // hook — returning locally-made mocks that ``overrides`` may have
      // replaced would make every assertion on them vacuous.
      const options = {
        projectRootPath: root,
        previewFile: vi.fn(),
        openFile: vi.fn(),
        blockFile: vi.fn(),
        ...overrides,
      };
      const { result } = renderHook(() =>
        useConversationLocalFileLinks(options),
      );
      return { result, ...options };
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

    it("previews when the model lowercases the drive or a segment", () => {
      // Windows is case-insensitive, so this is the same file as the project
      // root's. A case-sensitive root compare demoted it to a shell open.
      const { result } = renderWindows();

      expect(
        result.current.resolveLocalFileHref(
          "valuz-file:///c:/users/ada/valuz/chats/2026/09/13/nznnggqz/看板.html",
        ),
      ).toEqual({ kind: "preview", path: "看板.html" });
    });

    it("previews a two-slash ref, the form a model drops a slash into", () => {
      // `C:` lands in the URL authority as host `C` + empty port, so the
      // tolerant repair has to happen before parsing or the drive colon is lost
      // and the path resolves to `/C/Users/…`.
      const { result } = renderWindows();

      expect(
        result.current.resolveLocalFileHref(
          "valuz-file://C:/Users/ada/Valuz/chats/2026/09/13/NZNNGGQZ/看板.html",
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
      "javascript:alert(1)",
      "evidence://ev_mcp_abc123",
      "valuz-local://f/Users/ada/project/report.md",
      // Single-character schemes: these are drive-shaped, so teaching the
      // guard about drive letters is exactly what could have let them through.
      // They must stay rejected on a POSIX host — this is the regression guard
      // for the whole Windows fix.
      "s://evil.example/report.pdf",
      "a://host/report.pdf",
      "x:\\\\server\\share",
    ]) {
      expect(result.current.resolveLocalFileHref(href)).toBeNull();
    }
  });

  it("treats a bare drive path as a path even on a POSIX host", () => {
    // The accepted residual of the Windows fix, asserted so it cannot drift
    // silently. `C:/x` and `a:/x` are indistinguishable from a filesystem path
    // without knowing the platform, and this resolver is not told the platform
    // — it only ever sees a project root, which is empty for a quick chat.
    // Resolving them as paths is harmless: `openFile` hands them to
    // `shell.openPath`, which no-ops on a path that does not exist.
    const { result } = renderHook(() =>
      useConversationLocalFileLinks({
        projectRootPath: "/Users/ada/project",
        previewFile: vi.fn(),
        openFile: vi.fn(),
      }),
    );

    expect(
      result.current.resolveLocalFileHref("C:/Users/ada/report.pdf"),
    ).toEqual({ kind: "open", path: "C:/Users/ada/report.pdf" });
    expect(result.current.resolveLocalFileHref("a:/host/report.pdf")).toEqual({
      kind: "open",
      path: "a:/host/report.pdf",
    });
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

  /**
   * The invariant, asserted as an invariant rather than as a list of cases.
   *
   * Every test above pins one instance, which is exactly why the original bug
   * shipped: the suite proved particular links worked and proved nothing about
   * the ones nobody had thought of. What actually has to hold is a rule — a
   * link never silently loses its href, on any surface, for any shape a model
   * can write — and the second dimension is the one that was missing: whether
   * the host wired up ``isLocalFileHref`` at all. The share viewer does not,
   * and that is where every file link used to die.
   */
  describe("invariant: a link is never silently destroyed", () => {
    const SHAPES = [
      "valuz-file:///Users/ada/proj/a.md",
      "valuz-file:///C:/Users/ada/proj/a.md",
      "valuz-file:///C%3A/Users/ada/proj/a.md",
      "valuz-file://C:/Users/ada/proj/a.md",
      "file:///Users/ada/proj/a.md",
      "file:///C:/Users/ada/proj/a.md",
      "/Users/ada/proj/a.md",
      "/Users/ada/proj/a.md:12",
      "C:\\Users\\ada\\proj\\a.md",
      "reports/q3.md",
      "./reports/q3.md",
      "https://example.com/a.md",
      "ftp://example.com/a.md",
      "javascript:alert(1)",
    ];

    const HOSTS: Array<[string, Record<string, unknown>]> = [
      // A host that can open files (desktop / webui conversation).
      [
        "with a local-file host",
        {
          isLocalFileHref: (href: string) =>
            isDefaultLocalFileHref(href, "/Users/ada/proj"),
          onLocalFileLinkClick: () => {},
        },
      ],
      // A host that cannot (the share viewer passes neither prop).
      ["without a local-file host", {}],
    ];

    for (const [hostLabel, hostProps] of HOSTS) {
      for (const href of SHAPES) {
        it(`keeps "${href}" readable ${hostLabel}`, () => {
          const { container } = render(
            <MarkdownContent
              content={`看这个 [文件名.md](${href}) 谢谢`}
              mode="static"
              {...hostProps}
            />,
          );
          const body = container.querySelector("p");
          const text = body?.textContent ?? "";

          // 1. The label survives — the user can always read what was linked.
          expect(text).toContain("文件名.md");
          // 2. The sentence around it survives.
          expect(text).toContain("看这个");
          expect(text).toContain("谢谢");
          // 3. No accusatory marker, ever.
          expect(text).not.toContain("[blocked]");
          // 4. Nothing is left pointing at the internal carrier domain, which
          //    would 404 the user out of the app if they clicked it.
          const anchor = body?.querySelector("a");
          expect(anchor?.getAttribute("href") ?? "").not.toContain(
            "valuz.local-file.invalid",
          );
        });
      }
    }
  });

  it("still neutralizes a scheme the sanitizer does not allow", () => {
    // The link must not be followable — that part is unchanged. What changed is
    // how the refusal reads: this used to assert the "[blocked]" marker, and
    // that marker is now removed everywhere (see the ``span`` override in
    // MarkdownContent). The anchor is gone either way, so the marker added
    // nothing but a security accusation against, most often, one of our own
    // file paths.
    renderLink("[not a file](ftp://example.com/report.pdf)", WINDOWS_ROOT);

    expect(screen.queryByRole("link", { name: "not a file" })).toBeNull();
    expect(screen.getByText("not a file")).toBeTruthy();
    expect(document.body.textContent).not.toContain("[blocked]");
  });
});
