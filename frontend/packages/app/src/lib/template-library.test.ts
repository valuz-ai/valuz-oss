import { describe, expect, it } from "vitest";

import type { MarketplaceItemDetail } from "@valuz/core";

import {
  automationTemplatePrefill,
  playbookTemplatePrefill,
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
