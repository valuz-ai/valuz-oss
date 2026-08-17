import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";
import { MemoryRouter } from "react-router-dom";
import { initI18n } from "@valuz/shared/i18n";
import { marketplaceApi } from "@valuz/core";
import type {
  MarketplaceItem,
  MarketplaceItemList,
  MarketplaceListParams,
} from "@valuz/core";
import { MarketplacePage } from "./MarketplacePage";

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useOutletContext: () => ({
      setRightPanel: vi.fn(),
      setHeader: vi.fn(),
      setHeaderClassName: vi.fn(),
      setHideHeader: vi.fn(),
      setAsideClassName: vi.fn(),
      setMainClassName: vi.fn(),
      setContentInnerClassName: vi.fn(),
    }),
  };
});

const item = (
  overrides: Partial<MarketplaceItem> & Pick<MarketplaceItem, "id" | "type">,
): MarketplaceItem => ({
  source: "valuz_official",
  source_ref: overrides.id,
  title: overrides.id,
  description: "",
  subcategories: [],
  badges: [],
  stats: {},
  install_target: "skill_library",
  installed: false,
  ...overrides,
});

const emptyList = (): MarketplaceItemList => ({
  items: [],
  total: 0,
  page: 1,
  page_size: 30,
  degraded: false,
});

function renderPage(initialPath = "/marketplace") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <MarketplacePage />
    </MemoryRouter>,
  );
}

describe("MarketplacePage", () => {
  let listSpy: MockInstance<typeof marketplaceApi.list>;

  beforeEach(() => {
    initI18n({ locale: "zh-CN", fallbackLocale: "en-US" });
    vi.spyOn(marketplaceApi, "categories").mockResolvedValue({
      categories: [],
      degraded: false,
    });
    listSpy = vi
      .spyOn(marketplaceApi, "list")
      .mockImplementation(async (params: MarketplaceListParams) => {
        if (params.type === "plugin") {
          return {
            ...emptyList(),
            total: 2,
            items: [
              item({
                id: "market:plugin:equity-research",
                type: "plugin",
                title: "Equity Research",
                install_target: "plugin_library",
                version: "1.2.0",
                skill_count: 3,
                connector_count: 1,
                composition: "with_connectors",
              }),
              item({
                id: "market:plugin:writing-kit",
                type: "plugin",
                title: "Writing Kit",
                install_target: "plugin_library",
                skill_count: 4,
                connector_count: 0,
                composition: "skills_only",
              }),
            ].filter(
              (p) =>
                !params.composition || p.composition === params.composition,
            ),
          };
        }
        return emptyList();
      });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the four top-level tabs in order: agents, plugins, skills, connectors", async () => {
    renderPage();
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "智能体",
      "插件",
      "技能",
      "连接器",
    ]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
  });

  it("agents tab shows the 单智能体 | 团队 sub-tabs with 单智能体 first and default", async () => {
    renderPage();
    const single = await screen.findByRole("button", { name: "单智能体" });
    const teams = screen.getByRole("button", { name: "团队" });
    expect(
      single.compareDocumentPosition(teams) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // Default = single agents shelf (官方 Agent 模板 heading), not the teams shelf.
    expect(screen.getByText("官方 Agent 模板")).toBeTruthy();
    expect(screen.queryByText("精选 Agent 团队")).toBeNull();
    fireEvent.click(teams);
    expect(screen.getByText("精选 Agent 团队")).toBeTruthy();
    expect(screen.queryByText("官方 Agent 模板")).toBeNull();
  });

  it("skills tab 套件 sub-tab lists skills-only plugins", async () => {
    renderPage("/marketplace?tab=skills");
    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith(
        expect.objectContaining({ type: "skill" }),
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "套件" }));
    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith(
        expect.objectContaining({ type: "plugin", composition: "skills_only" }),
      );
    });
    await screen.findByText("Writing Kit");
    expect(screen.queryByText("Equity Research")).toBeNull();
    // 套件 cards show only the skill count.
    expect(screen.getByText("4 个技能")).toBeTruthy();
  });

  it("?tab=plugins selects the plugins tab, defaults to 全部 and filters by composition", async () => {
    renderPage("/marketplace?tab=plugins");
    const tabs = await screen.findAllByRole("tab");
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");

    // Default 全部 → no composition filter; both cards visible with the
    // "N 技能 · M 连接器" members line.
    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith(
        expect.objectContaining({ type: "plugin", composition: undefined }),
      );
    });
    expect(await screen.findByText("Equity Research")).toBeTruthy();
    expect(screen.getByText("Writing Kit")).toBeTruthy();
    expect(screen.getByText("3 技能 · 1 连接器")).toBeTruthy();
    expect(screen.getByText("v1.2.0")).toBeTruthy();

    // 含连接器 → only the plugin with connectors.
    fireEvent.click(screen.getByRole("button", { name: "含连接器" }));
    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "plugin",
          composition: "with_connectors",
        }),
      );
    });
    await waitFor(() => {
      expect(screen.queryByText("Writing Kit")).toBeNull();
    });
    expect(screen.getByText("Equity Research")).toBeTruthy();

    // 技能套件 → only the skills-only plugin.
    fireEvent.click(screen.getByRole("button", { name: "技能套件" }));
    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith(
        expect.objectContaining({ type: "plugin", composition: "skills_only" }),
      );
    });
    await waitFor(() => {
      expect(screen.queryByText("Equity Research")).toBeNull();
    });
    expect(screen.getByText("Writing Kit")).toBeTruthy();
  });

  it("switching to the plugins tab via the header updates the URL tab param", async () => {
    renderPage();
    const tabs = await screen.findAllByRole("tab");
    fireEvent.click(tabs[1]);
    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith(
        expect.objectContaining({ type: "plugin" }),
      );
    });
    expect(screen.getAllByRole("tab")[1].getAttribute("aria-selected")).toBe(
      "true",
    );
  });
});
