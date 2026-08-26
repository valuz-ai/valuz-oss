/** @vitest-environment jsdom */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { resolveOne, resolvedToArtifactFile } = vi.hoisted(() => ({
  resolveOne: vi.fn(),
  resolvedToArtifactFile: vi.fn(),
}));

vi.mock("@valuz/core", async (loadOriginal) => {
  const actual = await loadOriginal<typeof import("@valuz/core")>();
  return {
    ...actual,
    filesApi: { ...actual.filesApi, resolveOne },
  };
});

vi.mock("../lib/resolve-artifact", () => ({ resolvedToArtifactFile }));

import type {
  ApiBaseRef,
  ArtifactFileResponse,
  PlatformCapabilities,
  ResolvedFileDescriptor,
} from "@valuz/core";
import {
  MAX_OPEN_ARTIFACT_TABS,
  useArtifactFile,
} from "./use-artifact-file";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const platform = {
  isElectron: false,
  isMac: false,
} as PlatformCapabilities;

function descriptor(name: string): ResolvedFileDescriptor {
  return {
    ref: `valuz-file:///root/${name}`,
    kind: "remote",
    absPath: null,
    url: `https://files.example/${name}`,
    expiresAt: null,
    name,
    mimeType: "text/plain",
    size: 1,
    exists: true,
    previewKind: "plain",
    capabilities: {
      canPreview: true,
      canDownload: true,
      canOpenExternal: false,
      canCopyContent: true,
    },
    error: null,
  };
}

function response(name: string): ArtifactFileResponse {
  return {
    artifact: {
      id: name,
      kind: "project_file",
      projectId: "p1",
      path: name,
      name,
      previewKind: "plain",
      capabilities: {
        canPreview: true,
        canEdit: false,
        canOpenExternal: false,
        canCopyContent: true,
        canDownload: true,
      },
    },
    content: {
      kind: "text",
      encoding: "utf-8",
      content: name,
      truncated: false,
    },
  };
}

const renderArtifactHook = (baseRef?: ApiBaseRef, multiTab = false) =>
  renderHook(() =>
    useArtifactFile({
      projectId: "p1",
      platform,
      locate: (path) => ({
        absolutePath: `/root/${path}`,
        relativePath: path,
      }),
      missingErrorMessage: "missing",
      baseRef,
      multiTab,
    }),
  );

const renderTabbedHook = () => renderArtifactHook(undefined, true);

const tabNames = (tabs: { path: string }[]) => tabs.map((tab) => tab.path);

beforeEach(() => {
  resolveOne.mockReset();
  resolvedToArtifactFile.mockReset();
  resolvedToArtifactFile.mockImplementation(
    async (item: ResolvedFileDescriptor) => response(item.name),
  );
});

describe("useArtifactFile", () => {
  it("keeps the latest selection when an older request finishes last", async () => {
    const first = deferred<ResolvedFileDescriptor | null>();
    const second = deferred<ResolvedFileDescriptor | null>();
    resolveOne
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { result } = renderArtifactHook();

    act(() => {
      void result.current.open("a.txt");
      void result.current.open("b.txt");
    });
    expect(result.current.selectedPath).toBe("b.txt");

    await act(async () => second.resolve(descriptor("b.txt")));
    await waitFor(() => expect(result.current.artifact?.name).toBe("b.txt"));
    await act(async () => first.resolve(descriptor("a.txt")));

    expect(result.current.artifact?.name).toBe("b.txt");
    expect(resolvedToArtifactFile).toHaveBeenCalledTimes(1);
  });

  it("aborts the active transport and clears state on close", () => {
    resolveOne.mockReturnValue(new Promise(() => {}));
    const { result } = renderArtifactHook();

    act(() => {
      void result.current.open("a.txt");
    });
    const signal = resolveOne.mock.calls[0]?.[1]?.signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    act(() => result.current.close());
    expect(signal.aborted).toBe(true);
    expect(result.current.selectedPath).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("shows the page-provided message for a missing descriptor", async () => {
    resolveOne.mockResolvedValue(null);
    const { result } = renderArtifactHook();

    await act(async () => result.current.open("missing.txt"));

    expect(result.current.error).toBe("missing");
    expect(result.current.loading).toBe(false);
  });

  it("routes the resolve with the caller's entity ref", async () => {
    resolveOne.mockResolvedValue(descriptor("a.txt"));
    const { result } = renderArtifactHook({ sessionId: "s1", projectId: "p1" });

    await act(async () => result.current.open("a.txt"));

    expect(resolveOne.mock.calls[0]?.[1]?.baseRef).toEqual({
      sessionId: "s1",
      projectId: "p1",
      taskId: undefined,
      automationId: undefined,
      kbId: undefined,
    });
  });

  it("defaults the entity ref to the project", async () => {
    resolveOne.mockResolvedValue(descriptor("a.txt"));
    const { result } = renderArtifactHook();

    await act(async () => result.current.open("a.txt"));

    expect(resolveOne.mock.calls[0]?.[1]?.baseRef).toEqual({
      projectId: "p1",
    });
  });

  it("preserves an artifact target across reload and clears it on close", async () => {
    resolveOne.mockResolvedValue(descriptor("report.pdf"));
    const { result } = renderArtifactHook();

    await act(async () => result.current.open("report.pdf", { page: 12 }));
    expect(result.current.target).toEqual({ page: 12 });

    await act(async () => result.current.reload());
    expect(resolveOne).toHaveBeenCalledTimes(2);
    expect(result.current.target).toEqual({ page: 12 });

    act(() => result.current.close());
    expect(result.current.target).toBeNull();
  });

  it("replaces the single tab when multiTab is off", async () => {
    resolveOne.mockImplementation(async (ref: string) =>
      descriptor(ref.split("/").pop() ?? ""),
    );
    const { result } = renderArtifactHook();

    await act(async () => result.current.open("a.txt"));
    await act(async () => result.current.open("b.txt"));

    expect(tabNames(result.current.tabs)).toEqual(["b.txt"]);
    expect(result.current.activePath).toBe("b.txt");
  });

  it("keeps each opened document as its own tab and focuses the newest", async () => {
    resolveOne.mockImplementation(async (ref: string) =>
      descriptor(ref.split("/").pop() ?? ""),
    );
    const { result } = renderTabbedHook();

    await act(async () => result.current.open("a.txt"));
    await act(async () => result.current.open("b.txt"));

    expect(tabNames(result.current.tabs)).toEqual(["a.txt", "b.txt"]);
    expect(result.current.activePath).toBe("b.txt");
    expect(result.current.artifact?.name).toBe("b.txt");
  });

  it("focuses an already-open document instead of refetching it", async () => {
    resolveOne.mockImplementation(async (ref: string) =>
      descriptor(ref.split("/").pop() ?? ""),
    );
    const { result } = renderTabbedHook();

    await act(async () => result.current.open("a.txt"));
    await act(async () => result.current.open("b.txt"));
    expect(resolveOne).toHaveBeenCalledTimes(2);

    await act(async () => result.current.open("a.txt"));

    expect(tabNames(result.current.tabs)).toEqual(["a.txt", "b.txt"]);
    expect(result.current.activePath).toBe("a.txt");
    expect(resolveOne).toHaveBeenCalledTimes(2);
  });

  it("moves focus to the right neighbour when the active tab closes", async () => {
    resolveOne.mockImplementation(async (ref: string) =>
      descriptor(ref.split("/").pop() ?? ""),
    );
    const { result } = renderTabbedHook();

    for (const name of ["a.txt", "b.txt", "c.txt"]) {
      await act(async () => result.current.open(name));
    }
    act(() => result.current.activate("b.txt"));

    act(() => result.current.closeTab("b.txt"));
    expect(tabNames(result.current.tabs)).toEqual(["a.txt", "c.txt"]);
    expect(result.current.activePath).toBe("c.txt");

    // Nothing to the right of the last tab — fall back to the left one.
    act(() => result.current.closeTab("c.txt"));
    expect(result.current.activePath).toBe("a.txt");

    act(() => result.current.closeTab("a.txt"));
    expect(result.current.tabs).toEqual([]);
    expect(result.current.activePath).toBeNull();
  });

  it("evicts the least-recently-viewed tab once the ceiling is reached", async () => {
    resolveOne.mockImplementation(async (ref: string) =>
      descriptor(ref.split("/").pop() ?? ""),
    );
    const { result } = renderTabbedHook();

    for (let i = 0; i < MAX_OPEN_ARTIFACT_TABS; i += 1) {
      await act(async () => result.current.open(`f${i}.txt`));
    }
    expect(result.current.tabs).toHaveLength(MAX_OPEN_ARTIFACT_TABS);

    // Re-view the oldest tab so it is no longer the eviction candidate; the
    // next-oldest (f1) should go instead.
    act(() => result.current.activate("f0.txt"));
    await act(async () => result.current.open("overflow.txt"));

    expect(result.current.tabs).toHaveLength(MAX_OPEN_ARTIFACT_TABS);
    expect(tabNames(result.current.tabs)).toContain("f0.txt");
    expect(tabNames(result.current.tabs)).not.toContain("f1.txt");
    expect(result.current.activePath).toBe("overflow.txt");
  });
});
