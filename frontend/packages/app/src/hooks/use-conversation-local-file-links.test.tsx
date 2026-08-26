/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { useConversationLocalFileLinks } from "./use-conversation-local-file-links";
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

    expect(result.current.isLocalFileHref("/Users/ada/Downloads/report.pdf")).toBe(
      true,
    );

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

    expect(result.current.isLocalFileHref("/Users/ada/Downloads/report.pdf")).toBe(
      true,
    );
    expect(
      result.current.resolveLocalFileHref("/Users/ada/Downloads/report.pdf"),
    ).toEqual({
      kind: "blocked",
      path: "/Users/ada/Downloads/report.pdf",
      reason: "managed_outside_project",
    });
    expect(
      result.current.isLocalFileHref("/srv/valuz/projects/cloud-managed/report.md"),
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
