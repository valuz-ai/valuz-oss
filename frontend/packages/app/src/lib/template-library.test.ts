import { describe, expect, it } from "vitest";

import type { MarketplaceItemDetail } from "@valuz/core";

import {
  automationTemplatePrefill,
  playbookTemplatePrefill,
  recommendedTemplates,
  resolveTemplateText,
} from "./template-library";

const detail = (overrides: Partial<MarketplaceItemDetail>): MarketplaceItemDetail => ({
  id: "valuz_official:playbook_template:morning-briefing",
  type: "playbook_template",
  source: "valuz_official",
  source_ref: "morning-briefing",
  title: "每日市场晨报",
  description: "生成每日市场简报",
  subcategories: ["market-overview"],
  scenario_tags: ["briefing-summary"],
  badges: [],
  stats: {},
  install_target: "playbook_builder",
  installed: false,
  ...overrides,
});

describe("template library manifest adapters", () => {
  it("resolves localized fields with locale and English fallbacks", () => {
    expect(
      resolveTemplateText(
        { "zh-CN": "中文正文", "en-US": "English body" },
        "zh-CN",
      ),
    ).toBe("中文正文");
    expect(resolveTemplateText({ "en-US": "English body" }, "fr-FR")).toBe(
      "English body",
    );
  });

  it("maps a Playbook template into a create-only prefill", () => {
    expect(
      playbookTemplatePrefill(
        detail({
          install_manifest: {
            content: { "zh-CN": "先检索，再分析。", "en-US": "Research first." },
            status: "active",
            default_agent_slug: "valurion",
          },
        }),
        "zh-CN",
      ),
    ).toEqual({
      name: "每日市场晨报",
      content: "先检索，再分析。",
      status: "active",
      default_agent_slug: "valurion",
    });
  });

  it("maps an Automation template trigger and execution defaults", () => {
    expect(
      automationTemplatePrefill(
        detail({
          id: "valuz_official:automation_template:weekly-review",
          type: "automation_template",
          title: "每周研发复盘",
          install_target: "automation_builder",
          install_manifest: {
            prompt_template: { "zh-CN": "汇总本周变更" },
            default_agent_slug: "engineering-lead",
            trigger: { kind: "interval", seconds: 7200 },
            action_kind: "task",
            worktree: true,
          },
        }),
        "zh-CN",
      ),
    ).toEqual({
      name: "每周研发复盘",
      prompt_template: "汇总本周变更",
      agent_slug: "engineering-lead",
      trigger: { kind: "interval", seconds: 7200 },
      action_kind: "task",
      worktree: true,
    });
  });
});

describe("empty-state template recommendations", () => {
  const financeItem = (subcategory: string): MarketplaceItemDetail =>
    detail({
      id: `valuz_official:playbook_template:${subcategory}`,
      source_ref: subcategory,
      title: subcategory,
      category: "finance",
      subcategories: [subcategory],
    });

  const shuffledFinanceCatalog = [
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
  ].map(financeItem);

  it("keeps eight Playbooks and prioritizes briefings, monitoring, then investment research", () => {
    expect(
      recommendedTemplates(shuffledFinanceCatalog, "playbook").map(
        (item) => item.source_ref,
      ),
    ).toEqual([
      "general-finance",
      "macro-strategy",
      "brokerage",
      "market-data",
      "portfolio-risk",
      "equity-research",
      "quant-trading",
      "valuation-modeling",
    ]);
  });

  it("keeps eight Automations and prioritizes monitoring, briefings, then investment research", () => {
    expect(
      recommendedTemplates(shuffledFinanceCatalog, "automation").map(
        (item) => item.source_ref,
      ),
    ).toEqual([
      "brokerage",
      "market-data",
      "portfolio-risk",
      "general-finance",
      "macro-strategy",
      "equity-research",
      "quant-trading",
      "valuation-modeling",
    ]);
  });

  it("preserves API order for a mixed catalog while still limiting it to eight", () => {
    const mixed = shuffledFinanceCatalog.map((item, index) => ({
      ...item,
      category: index === 0 ? "office" : item.category,
    }));
    expect(recommendedTemplates(mixed, "playbook")).toEqual(mixed.slice(0, 8));
  });
});
