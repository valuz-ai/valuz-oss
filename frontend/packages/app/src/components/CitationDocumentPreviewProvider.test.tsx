import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useLayoutEffect, useState } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  citationsApi,
  type PlatformCapabilities,
} from "@valuz/core";

import {
  CitationDocumentPreviewProvider,
  citationResolutionI18nKey,
  decodeCitationOpenRef,
  encodeCitationOpenRef,
  locatorToDocumentLocation,
  materializeCitationDocument,
  useCitationDocumentPreview,
} from "./CitationDocumentPreviewProvider";
import { WebPlatformProvider } from "../platform";

const WEB_PLATFORM: PlatformCapabilities = {
  selectDirectory: async () => null,
  copyFiles: async () => ({ copied: 0, errors: [] }),
  deleteFile: async () => ({ success: false }),
  revealInFinder: async () => "",
  quitApp: async () => undefined,
  openNewWindow: async () => undefined,
  isElectron: false,
  isMac: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function OpenCloseProbe() {
  const { openCitation, openDocument, dismissDocument, closeCitation } =
    useCitationDocumentPreview();
  return (
    <>
      <button
        type="button"
        onClick={() =>
          openCitation({
            sessionId: "session-1",
            messageId: "message-1",
            citationId: "citation-1",
          })
        }
      >
        open preview
      </button>
      <button type="button" onClick={closeCitation}>
        close preview
      </button>
      <button
        type="button"
        onClick={() =>
          openDocument({
            document: {
              id: "finance-doc-1",
              title: "Finance report",
              render: {
                kind: "chunks",
                chunks: [
                  {
                    id: "chunk-1",
                    type: "paragraph",
                    text: "Revenue grew.",
                  },
                ],
              },
            },
          })
        }
      >
        open document
      </button>
      <button type="button" onClick={dismissDocument}>
        dismiss document
      </button>
    </>
  );
}

function OpenBeforeHostProbe() {
  const { openDocument } = useCitationDocumentPreview();
  const [hostMounted, setHostMounted] = useState(false);

  useLayoutEffect(() => {
    openDocument({
      document: {
        id: "delayed-doc",
        title: "Delayed report",
        render: {
          kind: "chunks",
          chunks: [{ id: "chunk-1", type: "paragraph", text: "Report body" }],
        },
      },
    });
    const timer = window.setTimeout(() => setHostMounted(true), 0);
    return () => window.clearTimeout(timer);
  }, [openDocument]);

  return hostMounted ? (
    <div data-preview-test-host>
      <main>conversation</main>
    </div>
  ) : null;
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname}{location.search}</div>;
}

describe("citation document preview helpers", () => {
  it("maps backend resolution reasons to user-facing locale keys", () => {
    expect(
      citationResolutionI18nKey("citation_has_no_readable_document"),
    ).toBe("ui.citation.noReadableDocument");
    expect(citationResolutionI18nKey("document_version_changed")).toBe(
      "ui.reader.locationDegraded",
    );
    expect(citationResolutionI18nKey("unexpected_backend_reason")).toBe(
      "ui.citation.unavailable",
    );
  });

  it("round-trips an opaque identity-only open ref", () => {
    const target = {
      sessionId: "session-1",
      messageId: "message-1",
      citationId: "cit-1",
    };
    const encoded = encodeCitationOpenRef(target);

    expect(encoded).not.toContain("session-1");
    expect(decodeCitationOpenRef(encoded)).toEqual(target);
    expect(decodeCitationOpenRef("not+base64")).toBeNull();
  });

  it("maps every PDF locator field needed by the highlighter", () => {
    expect(
      locatorToDocumentLocation({
        kind: "pdf",
        page: 42,
        rects: [{ x: 0.1, y: 0.2, width: 0.3, height: 0.04 }],
        quote: { exact: "Revenue grew." },
        pageRotation: 90,
      }),
    ).toEqual({
      kind: "pdf",
      page: 42,
      rects: [{ x: 0.1, y: 0.2, width: 0.3, height: 0.04 }],
      quote: { exact: "Revenue grew." },
      pageRotation: 90,
    });
  });

  it("reads remote HTML client-side before handing it to the sandboxed reader", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<h1>Report</h1>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
      ),
    );

    const document = await materializeCitationDocument(
      {
        id: "doc-1",
        title: "Report",
        render: {
          kind: "file",
          mimeType: "text/html",
          address: {
            kind: "remote",
            absPath: null,
            url: "https://signed.invalid/file",
            expiresAt: 123,
          },
        },
      },
      WEB_PLATFORM,
    );

    expect(document.render).toEqual({
      kind: "html",
      html: "<h1>Report</h1>",
    });
  });

  it("sanitizes backend-fetched inline HTML without a network request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const document = await materializeCitationDocument(
      {
        id: "doc-1",
        title: "Report",
        render: {
          kind: "html",
          html: '<h1 data-chunk-id="c1">Report</h1><script>steal()</script>',
        },
      },
      WEB_PLATFORM,
    );

    expect(fetchMock).not.toHaveBeenCalled();
    expect(document.render).toEqual({
      kind: "html",
      html: '<h1 data-chunk-id="c1">Report</h1>',
    });
  });

  it("resolves a same-origin PDF proxy against the citation API base", async () => {
    const document = await materializeCitationDocument(
      {
        id: "doc-1",
        title: "Report",
        render: {
          kind: "file",
          mimeType: "application/pdf",
          address: {
            kind: "remote",
            absPath: null,
            url: "/v1/finance/documents/doc-1/pdf",
            expiresAt: null,
          },
        },
      },
      WEB_PLATFORM,
      undefined,
      "http://localhost:8000",
    );

    expect(document.render).toEqual({
      kind: "file",
      url: "http://localhost:8000/v1/finance/documents/doc-1/pdf",
      mimeType: "application/pdf",
    });
  });

  it("sanitizes table markup in both the renderer and fallback chunk index", async () => {
    const malicious = '<table><tr><td onclick="steal()">42</td></tr></table>';
    const document = await materializeCitationDocument(
      {
        id: "doc-1",
        title: "Report",
        chunks: [{ id: "c1", type: "table", html: malicious }],
        render: {
          kind: "chunks",
          chunks: [{ id: "c1", type: "table", html: malicious }],
        },
      },
      WEB_PLATFORM,
    );

    expect(document.chunks?.[0]?.html).not.toContain("onclick");
    expect(document.render.kind).toBe("chunks");
    if (document.render.kind === "chunks") {
      expect(document.render.chunks[0]?.html).not.toContain("onclick");
    }
  });

  it("does not reopen from the stale citation query while closing", async () => {
    vi.spyOn(citationsApi, "resolve").mockImplementation(
      () => new Promise(() => undefined),
    );

    render(
      <WebPlatformProvider>
        <MemoryRouter initialEntries={["/conversation/session-1"]}>
          <CitationDocumentPreviewProvider>
            <div>
              <main>
                <OpenCloseProbe />
              </main>
            </div>
          </CitationDocumentPreviewProvider>
        </MemoryRouter>
      </WebPlatformProvider>,
    );

    fireEvent.click(screen.getByText("open preview"));
    expect(await screen.findByRole("dialog")).toBeTruthy();

    fireEvent.click(screen.getByText("close preview"));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("navigates a canonical structured-data route inside the app", async () => {
    vi.spyOn(citationsApi, "resolve").mockResolvedValue({
      document: {
        id: "structured:income:600519:2024",
        title: "Company income statement · 600519",
        render: {
          kind: "external",
          url: "/finance/stock/600519?tab=financials&statement=income&period=annual&field=total_revenue",
        },
      },
      effective_locator: { kind: "external" },
      status: "ready",
      fallback_reason: null,
      canonical_url: null,
    });

    render(
      <WebPlatformProvider>
        <MemoryRouter initialEntries={["/conversation/session-1"]}>
          <div>
            <main>
              <CitationDocumentPreviewProvider>
                <OpenCloseProbe />
                <LocationProbe />
              </CitationDocumentPreviewProvider>
            </main>
          </div>
        </MemoryRouter>
      </WebPlatformProvider>,
    );

    fireEvent.click(screen.getByText("open preview"));

    await waitFor(() => {
      expect(screen.getByTestId("location-probe").textContent).toBe(
        "/finance/stock/600519?tab=financials&statement=income&period=annual&field=total_revenue",
      );
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens a resolved document in the same reader with research tabs", async () => {
    render(
      <WebPlatformProvider>
        <MemoryRouter initialEntries={["/finance/follow"]}>
          <main>
            <CitationDocumentPreviewProvider>
              <OpenCloseProbe />
            </CitationDocumentPreviewProvider>
          </main>
        </MemoryRouter>
      </WebPlatformProvider>,
    );

    fireEvent.click(screen.getByText("open document"));

    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "Finance report" }),
    ).toBeTruthy();
    expect(screen.getByRole("tab", { name: "摘要" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "问答" })).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeTruthy();

    const apple = /^(darwin|mac|iphone|ipad|ipod)/i.test(
      navigator.platform || navigator.userAgent,
    );
    fireEvent.keyDown(window, {
      key: "w",
      metaKey: apple,
      ctrlKey: !apple,
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("waits for the project content host instead of falling back to fullscreen", async () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function (this: HTMLElement) {
        const isPreviewHost = this.hasAttribute("data-preview-test-host");
        const left = isPreviewHost ? 220 : 0;
        const top = isPreviewHost ? 36 : 0;
        const width = isPreviewHost ? 1204 : 1440;
        const height = isPreviewHost ? 848 : 900;
        return {
          x: left,
          y: top,
          left,
          top,
          right: left + width,
          bottom: top + height,
          width,
          height,
          toJSON: () => ({}),
        };
      },
    );

    render(
      <WebPlatformProvider>
        <MemoryRouter initialEntries={["/conversation/session-1"]}>
          <CitationDocumentPreviewProvider>
            <OpenBeforeHostProbe />
          </CitationDocumentPreviewProvider>
        </MemoryRouter>
      </WebPlatformProvider>,
    );

    const dialog = await screen.findByRole("dialog");
    expect(dialog.classList.contains("z-40")).toBe(true);
    expect(dialog.classList.contains("z-[80]")).toBe(false);
    expect(dialog.style.left).toBe("220px");
    expect(dialog.style.top).toBe("36px");
    expect(dialog.style.width).toBe("1204px");
    expect(dialog.style.height).toBe("848px");
  });
});
