import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { initI18n } from "@valuz/shared/i18n";
import { pluginsApi } from "@valuz/core";
import type { AgentPluginView } from "@valuz/core";
import { PluginsPage } from "./PluginsPage";

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

const plugin = (overrides: Partial<AgentPluginView>): AgentPluginView => ({
  id: "p1",
  name: "equity-research",
  version: "1.2.0",
  description: "Equity research toolkit",
  author: { name: "Valuz" },
  homepage: null,
  repository: null,
  license: "MIT",
  keywords: ["finance"],
  source: "market",
  source_ref: "market:plugin:equity-research",
  composition: "with_connectors",
  enabled: true,
  members: [
    {
      kind: "skill",
      slug: "comps",
      name: "Comps",
      description: "Comparable company analysis",
      meta_version: "1.0",
      content_hash: "a",
      installed: true,
      content_differs: false,
    },
    {
      kind: "connector",
      slug: "valuz-stock",
      name: "Valuz Stock",
      description: null,
      meta_version: null,
      content_hash: "b",
      installed: true,
      content_differs: false,
    },
  ],
  skill_count: 1,
  connector_count: 1,
  root_path: "/tmp/plugins/equity-research",
  installed_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:00Z",
  update_available: false,
  ...overrides,
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/plugins"]}>
      <PluginsPage />
    </MemoryRouter>,
  );
}

describe("PluginsPage", () => {
  beforeEach(() => {
    initI18n({ locale: "zh-CN", fallbackLocale: "en-US" });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the empty state with market + install actions when nothing is installed", async () => {
    vi.spyOn(pluginsApi, "list").mockResolvedValue({ items: [] });
    renderPage();
    expect(await screen.findByText("还没有安装插件")).toBeTruthy();
    expect(screen.getByRole("button", { name: "浏览插件市场" })).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: "安装插件" }).length,
    ).toBeGreaterThan(0);
  });

  it("lists installed plugins with version + composition and renders the selected detail inline", async () => {
    vi.spyOn(pluginsApi, "list").mockResolvedValue({
      items: [
        plugin({}),
        plugin({
          id: "p2",
          name: "writing-kit",
          version: "0.3.0",
          composition: "skills_only",
          skill_count: 4,
          connector_count: 0,
          members: [],
          enabled: false,
          source: "zip",
          source_ref: null,
        }),
      ],
    });
    renderPage();
    const cards = await screen.findAllByTestId("plugin-list-card");
    expect(cards).toHaveLength(2);
    expect(cards[0].textContent).toContain("equity-research");
    // The compact row mirrors ConnectorListItem density: name + composition
    // chip + switch. The version lives in the detail panel, not the row.
    expect(cards[0].textContent).toContain("含连接器");
    expect(cards[1].textContent).toContain("writing-kit");
    expect(cards[1].textContent).toContain("技能套件");
    // First plugin is selected by default → its detail renders inline in the
    // page (no longer pushed into the shared right panel).
    await waitFor(() => {
      expect(document.body.textContent).toContain("Comps");
    });
    expect(document.body.textContent).toContain("Valuz Stock");
    expect(document.body.textContent).toContain("MIT");
  });

  it("toggles a plugin off through the list switch", async () => {
    vi.spyOn(pluginsApi, "list").mockResolvedValue({ items: [plugin({})] });
    const disableSpy = vi
      .spyOn(pluginsApi, "disable")
      .mockResolvedValue(plugin({ enabled: false }));
    renderPage();
    // Two switches now carry the enable label (compact list row + inline
    // detail panel); the list row is the first.
    const toggles = await screen.findAllByRole("switch", { name: "停用" });
    fireEvent.click(toggles[0]);
    await waitFor(() => {
      expect(disableSpy).toHaveBeenCalledWith("p1");
    });
    expect(
      (await screen.findAllByRole("switch", { name: "启用" }))[0],
    ).toBeTruthy();
  });

  it("filters the list by the search box", async () => {
    vi.spyOn(pluginsApi, "list").mockResolvedValue({
      items: [
        plugin({}),
        plugin({ id: "p2", name: "writing-kit", members: [] }),
      ],
    });
    renderPage();
    await screen.findAllByTestId("plugin-list-card");
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    fireEvent.change(screen.getByPlaceholderText("搜索插件…"), {
      target: { value: "writing" },
    });
    await waitFor(() => {
      expect(screen.getAllByTestId("plugin-list-card")).toHaveLength(1);
    });
    // The name now shows in both the list row and the inline detail, so assert
    // it via the single remaining list card rather than a document-wide query.
    expect(screen.getByTestId("plugin-list-card").textContent).toContain(
      "writing-kit",
    );
  });
});
