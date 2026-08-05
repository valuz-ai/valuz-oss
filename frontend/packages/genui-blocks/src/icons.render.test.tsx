import { Renderer } from "@openuidev/react-lang";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { isKnownIcon } from "./lib/icon";
import { createValuzLibrary } from "./library";

function renderLang(source: string) {
  return render(<Renderer library={createValuzLibrary()} response={source} />);
}

describe("icons", () => {
  it("loads a lucide icon by name", async () => {
    const { container } = renderLang(`root = IconTag("TrendingUp")`);
    // The icon arrives through a dynamic import, so it is a frame or two late.
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
    expect(container.querySelector('[data-slot="vgb-icon-tag"]')).not.toBeNull();
  });

  it("renders nothing for a name lucide does not have", async () => {
    // The model will invent icon names. A thrown error from the lazy import
    // would take down the whole generated document, so an unknown name has to
    // degrade to no icon — the block around it still renders.
    const { container } = renderLang(
      `root = IconText("totally-made-up-icon", "Still here")`,
    );
    expect(screen.getByText("Still here")).toBeTruthy();
    await waitFor(() => expect(container.querySelector("svg")).toBeNull());
  });

  it("pairs an icon with text and an optional note", async () => {
    const { container } = renderLang(
      `root = IconText("dollar-sign", "Revenue", "Up on renewals")`,
    );
    expect(screen.getByText("Revenue")).toBeTruthy();
    expect(screen.getByText("Up on renewals")).toBeTruthy();
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
  });

  it("hides icons from assistive technology", async () => {
    // An icon here is always decorative: every block that carries one also
    // carries the text it marks, so announcing it would be a duplicate.
    const { container } = renderLang(`root = IconTag("star")`);
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
    expect(container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("knows which names exist", () => {
    expect(isKnownIcon("trending-up")).toBe(true);
    expect(isKnownIcon("TRENDING-UP")).toBe(true);
    expect(isKnownIcon("  dollar-sign  ")).toBe(true);
    expect(isKnownIcon("not-an-icon")).toBe(false);
    expect(isKnownIcon(undefined)).toBe(false);
    expect(isKnownIcon("")).toBe(false);
  });

  it("puts an icon on the cards the model actually reaches for", async () => {
    // A dashboard came back with emoji pasted into StatsCard labels — the model
    // wanted an icon and StatsCard had no prop for one, so it improvised in the
    // text. Every card that carries a heading now takes `icon`.
    const { container } = renderLang(
      `root = SmallCardBlock([a, b, c, d])
a = StatsCard("AI 算力", "强势主线", "算力基建投资加速", "", "", "cpu")
b = OverviewCard("半导体反弹", "板块资金大幅回流", [], "trending-up")
c = ContextCard("数据口径", "收盘价为准", "交易所", "info")
d = CompositeCard("估值", "", "18.4x", false, [], "chart-line")`,
    );
    expect(screen.getByText("AI 算力")).toBeTruthy();
    expect(screen.getByText("半导体反弹")).toBeTruthy();
    await waitFor(() =>
      expect(container.querySelectorAll("svg.vgb-card-icon").length).toBe(4),
    );
  });

  it("accepts the component spelling as well as the id", () => {
    // The prompt says only "any lucide-react icon name". The model knows that
    // set as the component exports — TrendingUp, Building2 — while the import
    // map is keyed on ids. Rejecting the spelling it is likelier to produce
    // would turn a correct icon name into a blank.
    for (const [written, id] of [
      ["TrendingUp", "trending-up"],
      ["Building2", "building-2"],
      ["ChartLine", "chart-line"],
      ["trending_up", "trending-up"],
      ["Trending Up", "trending-up"],
      ["  Star  ", "star"],
    ]) {
      expect(isKnownIcon(written), `${written} should resolve to ${id}`).toBe(true);
      expect(isKnownIcon(id)).toBe(true);
    }
  });
});
