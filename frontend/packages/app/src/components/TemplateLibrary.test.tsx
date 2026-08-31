import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  marketplaceApi,
  type MarketplaceItem,
  type MarketplaceItemDetail,
} from "@valuz/core";
import { initI18n } from "@valuz/shared/i18n";

import { TemplateLibrary } from "./TemplateLibrary";

beforeAll(() => initI18n({ locale: "zh-CN", fallbackLocale: "en-US" }));
afterEach(() => vi.restoreAllMocks());

const item: MarketplaceItem = {
  id: "valuz_official:playbook_template:brokerage-review",
  type: "playbook_template",
  source: "valuz_official",
  source_ref: "brokerage-review",
  title: "券商账户与订单巡检执行手册",
  description: "核对账户资产、持仓和订单。",
  category: "finance",
  category_label: "金融投资",
  subcategories: ["brokerage"],
  scenario_tags: ["monitoring-alerting"],
  badges: [],
  stats: {},
  install_target: "playbook_builder",
  installed: false,
};

describe("TemplateLibrary", () => {
  it("shows eight cards in the empty-state recommendation variant", async () => {
    vi.spyOn(marketplaceApi, "categories").mockResolvedValue({
      categories: [],
      scenario_tags: [],
      degraded: false,
    });
    const recommendedItems = Array.from({ length: 10 }, (_, index) => ({
      ...item,
      id: `${item.id}-${index}`,
      source_ref: `${item.source_ref}-${index}`,
      title: `推荐模板 ${index + 1}`,
      subcategories: [
        [
          "accounting-reporting",
          "valuation-modeling",
          "quant-trading",
          "equity-research",
          "wealth-management",
          "portfolio-risk",
          "market-data",
          "brokerage",
          "macro-strategy",
          "general-finance",
        ][index],
      ],
    }));
    const list = vi.spyOn(marketplaceApi, "list").mockResolvedValue({
      items: recommendedItems,
      total: recommendedItems.length,
      page: 1,
      page_size: 60,
      degraded: false,
    });

    render(
      <TemplateLibrary kind="playbook" variant="recommended" onUse={vi.fn()} />,
    );

    await screen.findByText("推荐模板 10");
    expect(screen.getAllByRole("button")).toHaveLength(8);
    expect(screen.queryByText("推荐模板 1")).toBeNull();
    expect(screen.queryByText("推荐模板 5")).toBeNull();
    expect(list).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, page_size: 60 }),
    );
  });

  it("keeps search and scenario filters without category navigation", async () => {
    vi.spyOn(marketplaceApi, "categories").mockResolvedValue({
      categories: [
        {
          key: "finance",
          label: "金融投资",
          count: 2,
          subcategories: [{ key: "brokerage", label: "券商接入", count: 2 }],
        },
      ],
      scenario_tags: [{ key: "monitoring-alerting", label: "监控预警", count: 1 }],
      degraded: false,
    });
    const list = vi.spyOn(marketplaceApi, "list").mockResolvedValue({
      items: [item],
      total: 1,
      page: 1,
      page_size: 60,
      degraded: false,
    });

    render(<TemplateLibrary kind="playbook" onUse={vi.fn()} />);
    expect(await screen.findByText("券商账户与订单巡检执行手册")).toBeTruthy();
    expect(screen.queryByText("分类浏览")).toBeNull();
    expect(screen.queryByRole("button", { name: /券商接入 2/ })).toBeNull();
    expect(screen.getByPlaceholderText("搜索模板")).toBeTruthy();

    const allScenarios = screen.getByRole("button", { name: "全部场景" });
    const monitoring = screen.getByRole("button", { name: /监控预警 1/ });
    expect(allScenarios.getAttribute("aria-pressed")).toBe("true");
    expect(allScenarios.className).toContain("rounded-full");

    await userEvent.click(monitoring);
    expect(allScenarios.getAttribute("aria-pressed")).toBe("false");
    expect(monitoring.getAttribute("aria-pressed")).toBe("true");
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        expect.objectContaining({ scenario: "monitoring-alerting" }),
      ),
    );

    await userEvent.type(screen.getByPlaceholderText("搜索模板"), "订单");
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ q: "订单" })),
    );
    expect(list).not.toHaveBeenCalledWith(
      expect.objectContaining({ category: expect.anything() }),
    );
    expect(list).not.toHaveBeenCalledWith(
      expect.objectContaining({ subcategory: expect.anything() }),
    );
  });

  it("opens template details and hands the raw manifest to the prefill flow", async () => {
    vi.spyOn(marketplaceApi, "categories").mockResolvedValue({
      categories: [],
      scenario_tags: [],
      degraded: false,
    });
    vi.spyOn(marketplaceApi, "list").mockResolvedValue({
      items: [item],
      total: 1,
      page: 1,
      page_size: 60,
      degraded: false,
    });
    const detail: MarketplaceItemDetail = {
      ...item,
      workflow: ["核对输入", "检查异常"],
      deliverables: ["巡检报告"],
      usage_notes: ["创建前确认智能体"],
      install_manifest: {
        content: { "zh-CN": "核对账户资产、持仓和订单。" },
        status: "draft",
      },
    };
    vi.spyOn(marketplaceApi, "get").mockResolvedValue(detail);
    const onUse = vi.fn();

    render(<TemplateLibrary kind="playbook" onUse={onUse} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /券商账户与订单巡检执行手册/ }),
    );
    expect(await screen.findByText("巡检报告")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "使用执行手册模板" }));
    expect(onUse).toHaveBeenCalledWith(detail);
  });
});
