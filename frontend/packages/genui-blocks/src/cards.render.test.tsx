import { Renderer } from "@openuidev/react-lang";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createValuzLibrary } from "./library";

/**
 * Registration only proves a block is *reachable*. This renders the card
 * family the way the product does — one OpenUI Lang program through the real
 * parser — which is the only path that catches a schema whose positional
 * argument order does not match what the component reads, or a children slot
 * handed to React instead of `renderNode`.
 */

function renderLang(source: string) {
  return render(<Renderer library={createValuzLibrary()} response={source} />);
}

describe("card family renders through the OpenUI Lang parser", () => {
  it("renders every card block from one program", () => {
    renderLang(`root = Stack([blk, opts, tiles])
blk = MediumCardBlock([stats, dataTile, value, overview, context, composite, visual, profile])
stats = StatsCard("Revenue", "$4.2M", "Up on strong renewals", "+12.4%")
dataTile = DataTileCard("$1.8M", "across 12 accounts", "Pipeline")
value = ValueCard("Margin", "38%", "Gross, trailing twelve months")
overview = OverviewCard("Summary", "Growth held through the quarter.")
context = ContextCard("Method", "Figures are unaudited.", "Internal finance")
composite = CompositeCard("Segment", "EMEA", "$960K")
visual = VisualFirstCard("https://example.com/chart.png", "Adoption", "Steady climb", "Adoption chart")
profile = ProfileTile("Ada Lovelace", "Analyst", "Covers infrastructure")
opts = OptionCards([optA, optB])
optA = OptionCard("Conservative", "Assumes no new hires")
optB = OptionCard("Aggressive", "Assumes headcount doubles", true)
tiles = TileOptionBlock([tileA, tileB])
tileA = TileOption("Monthly", "Billed each month")
tileB = TileOption("Annual", "Two months free", true)`);

    for (const text of [
      "Revenue",
      "$4.2M",
      "across 12 accounts",
      "Margin",
      "Summary",
      "Method",
      "Segment",
      "Adoption",
      "Ada Lovelace",
      "Conservative",
      "Aggressive",
      "Monthly",
      "Annual",
    ]) {
      expect(screen.getByText(text), `missing: ${text}`).toBeTruthy();
    }
  });

  it("derives initials when a profile has no avatar", () => {
    const { container } = renderLang(`root = ProfileTile("Grace Hopper", "Engineer")`);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("GH")).toBeTruthy();
  });

  it("marks a selected option without making it interactive", () => {
    const { container } = renderLang(
      `root = OptionCards([o])\no = OptionCard("Chosen", "The picked one", true)`,
    );
    const card = container.querySelector('[data-slot="vgb-option-card"]');
    expect(card?.getAttribute("data-selected")).toBe("true");
    // Selection is presentational: these blocks render LLM output and have no
    // handler behind them, so anything that promises interaction is a lie.
    expect(card?.getAttribute("role")).toBeNull();
    expect(card?.getAttribute("tabindex")).toBeNull();
    expect(container.querySelector("button")).toBeNull();
  });
});
