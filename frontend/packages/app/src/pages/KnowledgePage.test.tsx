import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactElement, ReactNode } from "react";
import { initI18n } from "@valuz/shared/i18n";
import { toast } from "sonner";
import { docsApi, filesApi, kbApi } from "@valuz/core";
import { PlatformProvider } from "@valuz/app/platform";
import { KnowledgePage } from "./KnowledgePage";
import type { PlatformCapabilities } from "@valuz/core";

let latestHeader: ReactNode | null = null;
// The detail panel is handed to the LAYOUT, not rendered inline, so there is
// no button in this tree to press. Captured instead, and its callbacks are
// invoked directly — the wiring is what these tests are about.
let latestRightPanel: ReactNode | null = null;
let latestAsideClassName: string | undefined;

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), loading: vi.fn(), dismiss: vi.fn() },
}));

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useOutletContext: () => ({
      setRightPanel: (node: ReactNode | null) => {
        latestRightPanel = node;
      },
      setHeader: (node: ReactNode | null) => {
        latestHeader = node;
      },
      setHeaderClassName: vi.fn(),
      setHideHeader: vi.fn(),
      setAsideClassName: (cls: string | undefined) => {
        latestAsideClassName = cls;
      },
      setMainClassName: vi.fn(),
      setContentInnerClassName: vi.fn(),
    }),
  };
});

const platform: PlatformCapabilities = {
  selectDirectory: vi.fn(),
  copyFiles: vi.fn(),
  deleteFile: vi.fn(),
  revealInFinder: vi.fn(),
  quitApp: vi.fn(),
  openNewWindow: vi.fn(),
  isElectron: false,
  isMac: false,
};

function renderKnowledgePage(props: Parameters<typeof KnowledgePage>[0] = {}) {
  latestHeader = null;
  latestRightPanel = null;
  latestAsideClassName = undefined;
  return render(
    <PlatformProvider value={platform}>
      <KnowledgePage {...props} />
      <div data-testid="page-header">{latestHeader}</div>
    </PlatformProvider>,
  );
}

const KB = {
  id: "kb1",
  name: "测试",
  root_path: "/data/kb1",
  parser_routing: "auto",
  document_count: 1,
  status: "has_processing" as const,
  created_at: 0,
  auto_discover: true,
  last_full_scan_at: null,
};

const DOC = {
  id: "d1",
  filename: "a.pdf",
  title: null,
  status: "processing" as const,
  chunk_count: 0,
  file_size_bytes: 1,
  mime_type: "application/pdf",
  kb_id: KB.id,
  kb_folder_id: null,
  relative_path: "a.pdf",
  created_at: 0,
  source_path: "/tmp/kb-root/a.pdf",
  parser_mode: null,
  docs_runtime_id: null,
  last_error_code: null,
  last_error_message: null,
  parser_attempts: [],
};

/** Open the library and select its one document. */
async function openDoc(opts: { platformOverrides?: Partial<PlatformCapabilities> } = {}) {
  Object.assign(platform, opts.platformOverrides ?? {});
  vi.spyOn(kbApi, "list").mockResolvedValue({ knowledge_bases: [KB] });
  vi.spyOn(kbApi, "get").mockResolvedValue(KB);
  vi.spyOn(kbApi, "tree").mockResolvedValue({
    nodes: [
      {
        id: DOC.id,
        name: DOC.filename,
        relative_path: DOC.relative_path,
        kind: "document" as const,
        status: DOC.status,
        document_count: 0,
      },
    ],
  });
  vi.spyOn(docsApi, "health").mockResolvedValue({
    status: "healthy",
    total_documents: 1,
    ready_count: 0,
    processing_count: 1,
    failed_count: 0,
    missing_count: 0,
  });
  const get = vi.spyOn(docsApi, "get").mockResolvedValue(DOC);
  vi.spyOn(docsApi, "preview").mockResolvedValue({
    document_id: DOC.id,
    markdown: "",
  });

  const rendered = renderKnowledgePage();
  await waitFor(() => expect(screen.getByText(KB.name)).toBeTruthy());
  fireEvent.click(screen.getByText(KB.name));
  await waitFor(() => expect(screen.getByText(DOC.filename)).toBeTruthy());
  fireEvent.click(screen.getByText(DOC.filename));
  await waitFor(() => expect(get).toHaveBeenCalled());
  return { get, unmount: rendered.unmount };
}

describe("KnowledgePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    // ``openDoc`` mutates the shared platform stub in place; put the
    // browser-shaped defaults back so one test cannot configure another.
    Object.assign(platform, { isElectron: false, revealInFinder: vi.fn() });
  });

  // ── Which backend a per-document call goes to ────────────────────────
  //
  // A library can live on a different backend than the module default, and
  // every per-document call carries the library id so it can be routed there.
  // "Carries" is per call site, so it is forgettable — and it was forgotten in
  // exactly the places that run without anybody watching: the detail poll and
  // the retry. On a cloud library both asked the LOCAL backend about a
  // document it has never heard of. The poll's 404 fell into its catch and the
  // panel sat frozen while the list beside it updated every three seconds; the
  // retry's 404 surfaced as a failure toast on every press, which reads as a
  // document that cannot be retried.

  it("polls a document's detail on its own library's backend", async () => {
    initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
    const { get } = await openDoc();
    vi.useFakeTimers();

    await vi.advanceTimersByTimeAsync(3500);

    expect(get).toHaveBeenLastCalledWith(DOC.id, KB.id);
  });

  it("gives the open document's detail the wider side", async () => {
    // The detail is what is being read — parse history, error text, source
    // path — while the list is just where the click came from. The default
    // 345px aside made the panel the cramped side.
    initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
    await openDoc();

    await waitFor(() =>
      expect(latestAsideClassName ?? "").toContain("w-[70%]"),
    );
  });

  it("hands the layout back when the document is closed and on unmount", async () => {
    // The panel and the widened aside live in the LAYOUT. Settings is not an
    // overlay route, so navigating there unmounts this page — and without the
    // cleanup the document detail stayed on screen beside the settings
    // content.
    initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
    const { unmount } = await openDoc();
    await waitFor(() => expect(latestAsideClassName ?? "").toContain("w-[70%]"));

    unmount();

    expect(latestRightPanel).toBeNull();
    expect(latestAsideClassName).toBeUndefined();
  });

  it("retries a document on its own library's backend", async () => {
    initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
    const reindex = vi.spyOn(docsApi, "reindex").mockResolvedValue({
      task_id: "t1",
      task_type: "reindex",
      status: "queued",
      total_items: 1,
    } as never);
    await openDoc();

    await waitFor(() => expect(latestRightPanel).toBeTruthy());
    const panel = latestRightPanel as ReactElement<{
      onRegenerate?: () => void;
    }>;
    panel.props.onRegenerate?.();

    await waitFor(() => expect(reindex).toHaveBeenCalledWith([DOC.id], KB.id));
  });

  it("keeps the header add button visible when the knowledge base list is empty", async () => {
    initI18n({ locale: "en-US", fallbackLocale: "en-US" });
    vi.spyOn(kbApi, "list").mockResolvedValue({ knowledge_bases: [] });
    vi.spyOn(docsApi, "health").mockResolvedValue({
      status: "healthy",
      total_documents: 0,
      ready_count: 0,
      processing_count: 0,
      failed_count: 0,
      missing_count: 0,
    });

    const { rerender } = renderKnowledgePage();

    await waitFor(() => {
      expect(screen.getByText("Create new knowledge base")).toBeTruthy();
    });
    rerender(
      <PlatformProvider value={platform}>
        <KnowledgePage />
        <div data-testid="page-header">{latestHeader}</div>
      </PlatformProvider>,
    );

    const header = screen.getByTestId("page-header");
    expect(header.textContent).toContain("Add");
    expect(
      screen.getByRole("button", { name: "Add knowledge base" }),
    ).toBeTruthy();
  });

  it("shows document health in the header when the knowledge base list is empty", async () => {
    initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
    vi.spyOn(kbApi, "list").mockResolvedValue({ knowledge_bases: [] });
    vi.spyOn(docsApi, "health").mockResolvedValue({
      status: "healthy",
      total_documents: 14,
      ready_count: 0,
      processing_count: 14,
      failed_count: 0,
      missing_count: 0,
    });

    const { rerender } = renderKnowledgePage();

    await waitFor(() => {
      expect(screen.getByText("创建一个新的知识库")).toBeTruthy();
    });
    rerender(
      <PlatformProvider value={platform}>
        <KnowledgePage />
        <div data-testid="page-header">{latestHeader}</div>
      </PlatformProvider>,
    );

    const header = screen.getByTestId("page-header");
    expect(header.textContent).toContain("14 文档");
    expect(header.textContent).toContain("0 已就绪");
    expect(header.textContent).toContain("14 索引中");
  });

  it("hides the local directory picker when creating a managed knowledge base", async () => {
    initI18n({ locale: "en-US", fallbackLocale: "en-US" });
    vi.spyOn(kbApi, "list").mockResolvedValue({ knowledge_bases: [] });
    vi.spyOn(docsApi, "health").mockResolvedValue({
      status: "healthy",
      total_documents: 0,
      ready_count: 0,
      processing_count: 0,
      failed_count: 0,
      missing_count: 0,
    });

    const { rerender } = renderKnowledgePage({
      directoryFieldMode: "managed",
    });

    await waitFor(() => {
      expect(screen.getByText("Create new knowledge base")).toBeTruthy();
    });
    rerender(
      <PlatformProvider value={platform}>
        <KnowledgePage directoryFieldMode="managed" />
        <div data-testid="page-header">{latestHeader}</div>
      </PlatformProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Add knowledge base" }));

    expect(screen.getAllByText(/managed directory/).length).toBeGreaterThan(0);
    expect(screen.queryByText("Select directory")).toBeNull();
  });

  // ── Opening the ORIGINAL file ────────────────────────────────────────
  //
  // ``/v1/files/resolve`` answers for a file that is not there the same way it
  // answers for one that is: ``kind`` and ``absPath`` are both filled in, with
  // the bad news in ``error`` / ``exists``. Acting on the first two alone hands
  // a dead path to the OS, which opens nothing and says nothing — the click
  // looks broken. A knowledge base whose folder was cleaned out from under it
  // (a library under ``/tmp``, a moved directory) is exactly that shape.

  it("says the source file is gone instead of opening nothing", async () => {
    initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
    const reveal = vi.fn(async () => "");
    vi.spyOn(filesApi, "resolveOne").mockResolvedValue({
      ref: "valuz-file:///tmp/kb-root/a.pdf",
      kind: "local",
      absPath: "/tmp/kb-root/a.pdf",
      exists: false,
      error: "not_found",
    } as never);
    await openDoc({ platformOverrides: { isElectron: true, revealInFinder: reveal } });

    await waitFor(() => expect(latestRightPanel).toBeTruthy());
    const panel = latestRightPanel as ReactElement<{ onViewSource?: () => void }>;
    panel.props.onViewSource?.();

    // Naming the failure is the whole point: falling through to the generic
    // "failed" toast is the same dead end the user already had, one word
    // louder. The message has to say the SOURCE FILE is gone.
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("源文件缺失"),
    );
    // And the OS is never handed the dead path.
    expect(reveal).not.toHaveBeenCalled();
  });

  it("surfaces what the OS complained about when opening fails", async () => {
    // ``shell.openPath`` reports failure by RESOLVING with a message. A caller
    // that ignores the return value turns "no application can open this" into
    // a click that does nothing.
    initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
    const reveal = vi.fn(async () => "no application knows how to open this");
    vi.spyOn(filesApi, "resolveOne").mockResolvedValue({
      ref: "valuz-file:///tmp/kb-root/a.pdf",
      kind: "local",
      absPath: "/tmp/kb-root/a.pdf",
      exists: true,
      error: null,
    } as never);
    await openDoc({ platformOverrides: { isElectron: true, revealInFinder: reveal } });

    await waitFor(() => expect(latestRightPanel).toBeTruthy());
    const panel = latestRightPanel as ReactElement<{ onViewSource?: () => void }>;
    panel.props.onViewSource?.();

    await waitFor(() => expect(reveal).toHaveBeenCalledWith("/tmp/kb-root/a.pdf"));
  });

});
