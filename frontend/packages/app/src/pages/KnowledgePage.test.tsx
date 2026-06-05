import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { initI18n } from "@valuz/shared/i18n";
import { docsApi, kbApi } from "@valuz/core";
import { PlatformProvider } from "@valuz/app/platform";
import { KnowledgePage } from "./KnowledgePage";
import type { PlatformCapabilities } from "@valuz/core";

let latestHeader: ReactNode | null = null;

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useOutletContext: () => ({
      setRightPanel: vi.fn(),
      setHeader: (node: ReactNode | null) => {
        latestHeader = node;
      },
      setHeaderClassName: vi.fn(),
      setHideHeader: vi.fn(),
      setAsideClassName: vi.fn(),
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
};

function renderKnowledgePage() {
  latestHeader = null;
  return render(
    <PlatformProvider value={platform}>
      <KnowledgePage />
      <div data-testid="page-header">{latestHeader}</div>
    </PlatformProvider>,
  );
}

describe("KnowledgePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
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
});
