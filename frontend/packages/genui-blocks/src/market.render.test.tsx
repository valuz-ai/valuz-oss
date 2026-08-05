import { Renderer, createLibrary } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BlockComponent } from "./blocks";
import { DataList, DataListItem } from "./DataList";
import { MarketBreadth } from "./MarketBreadth";
import { MarketIndexCard, MarketIndexGrid } from "./MarketIndexGrid";

/**
 * The market family through the real parser.
 *
 * The library is composed here rather than through `createValuzLibrary()`
 * because registration in `blocks.ts` is assembled centrally; swap this for
 * `createValuzLibrary()` once these five names are listed there. What the
 * detour cannot skip is the point of the file: every call below is positional,
 * which is the only way to catch a schema whose key order does not match the
 * order the model would write the arguments in.
 */
const marketBlocks: BlockComponent[] = [
  DataList,
  DataListItem,
  MarketIndexGrid,
  MarketIndexCard,
  MarketBreadth,
];

function renderLang(source: string) {
  const library = createLibrary({
    root: openuiLibrary.root ?? "Stack",
    components: [
      ...(Object.values(openuiLibrary.components) as BlockComponent[]),
      ...marketBlocks,
    ],
  });
  return render(<Renderer library={library} response={source} />);
}

describe("market family renders through the OpenUI Lang parser", () => {
  it("binds every block's shortest positional call to the props it reads", () => {
    renderLang(`root = Stack([grid, quote, list, row, breadth])
grid = MarketIndexGrid([{ name: "上证指数", code: "000001", latest: "3,830.84", changePct: "+0.56%", turnover: "7,908.59亿" }])
quote = MarketIndexCard("创业板指", "399006", "3,491.63")
list = DataList([{ title: "其他数字媒体", value: "924.24", meta: "+8.77%" }])
row = DataListItem("医疗研发外包", "7352.92", "+8.06%")
breadth = MarketBreadth(1422, 862, 66)`);

    for (const text of [
      "上证指数",
      "000001",
      "3,830.84",
      "+0.56%",
      "成交额 7,908.59亿",
      "创业板指",
      "3,491.63",
      "其他数字媒体",
      "924.24",
      "+8.77%",
      "医疗研发外包",
      "+8.06%",
      "上涨 1,422",
      "下跌 862",
      "平盘 66",
    ]) {
      expect(screen.getByText(text), `missing: ${text}`).toBeTruthy();
    }
  });

  it("emits the attribute contract the host stylesheet is keyed on", () => {
    // These attributes are the seam between the blocks and the generative-UI
    // stylesheet in the host. They are not decoration and not test hooks:
    // dropping one silently unstyles the component in the product while every
    // assertion about its text keeps passing.
    const { container } = renderLang(`root = Stack([grid, list, breadth])
grid = MarketIndexGrid([{ name: "上证指数", latest: "3,830.84" }])
list = DataList([{ title: "其他数字媒体", value: "924.24", meta: "+8.77%", rank: 1 }])
breadth = MarketBreadth(1422, 862, 66)`);

    for (const selector of [
      '[data-a2ui-component="data-list"]',
      "[data-a2ui-data-list-rows]",
      "[data-a2ui-data-list-row]",
      "[data-a2ui-data-list-main]",
      "[data-a2ui-data-list-title]",
      "[data-a2ui-data-list-value]",
      "[data-a2ui-data-list-meta]",
      '[data-a2ui-component="market-index-grid"]',
      "[data-a2ui-market-index-grid-list]",
      '[data-a2ui-component="market-index-card"]',
      "[data-a2ui-market-index-value]",
      '[data-a2ui-component="market-breadth"]',
      "[data-a2ui-market-breadth-track]",
      "[data-a2ui-market-breadth-stats]",
      '[data-a2ui-market-breadth-bar="up"]',
      '[data-a2ui-market-breadth-bar="down"]',
      '[data-a2ui-market-breadth-bar="flat"]',
    ]) {
      expect(container.querySelector(selector), `missing: ${selector}`).not.toBeNull();
    }
  });

  it("reads the aliases the model reaches for instead of the schema's names", () => {
    // Model output is inconsistent between turns, and a quote that renders
    // without its change figure looks like missing data rather than a missing
    // alias. `indices`/`change_pct` here, `items`/`name` in the list.
    renderLang(`root = Stack([grid, list])
grid = MarketIndexGrid([{ name: "沪深300", change_pct: "+1.24%", price: "4,102.55" }])
list = DataList([{ name: "医疗研发外包", amount: "7,352.92", change: "+8.06%" }])`);

    expect(screen.getByText("+1.24%")).toBeTruthy();
    expect(screen.getByText("4,102.55")).toBeTruthy();
    expect(screen.getByText("医疗研发外包")).toBeTruthy();
    expect(screen.getByText("7,352.92")).toBeTruthy();
    expect(screen.getByText("+8.06%")).toBeTruthy();
  });
});
