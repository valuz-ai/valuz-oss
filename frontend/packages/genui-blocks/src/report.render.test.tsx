import { Renderer } from "@openuidev/react-lang";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createValuzLibrary } from "./library";

function renderLang(source: string) {
  return render(<Renderer library={createValuzLibrary()} response={source} />);
}

/**
 * Every assertion here is about *positional* calls, because that is how the
 * model writes OpenUI Lang and because getting the argument order wrong is the
 * one mistake this stack does not report: arguments bind by zod key order, so
 * an array landing on an optional string prop yields an empty block with no
 * parse error and no type error.
 */
describe("report family renders from positional OpenUI Lang", () => {
  it("renders a whole document — cover, contents, page, content blocks", () => {
    renderLang(`root = ReportDocument([cover, toc, page])
cover = ReportFrontPage("Annual Review", "Fiscal 2026", "Research")
toc = ReportTocPage([{ label: "Overview", page: 2 }, { label: "Outlook", page: 8 }])
page = ReportPage([section])
section = ReportSection("Summary", [headline, statement, figures])
headline = ReportHeadline("Revenue grew for a fourth straight quarter")
statement = ReportKeyStatement("Renewals, not new logos, carried the year.")
figures = ReportTable(["Segment", "Revenue"], [rowA, rowB])
rowA = ["EMEA", "$960K"]
rowB = ["AMER", "$2.1M"]`);

    for (const text of [
      "Annual Review",
      "Fiscal 2026",
      "Overview",
      "Outlook",
      "Summary",
      "Revenue grew for a fourth straight quarter",
      "Renewals, not new logos, carried the year.",
      "Segment",
      "EMEA",
      "$2.1M",
    ]) {
      expect(screen.getByText(text), `missing: ${text}`).toBeTruthy();
    }
  });

  it("puts page children on the page rather than binding them to a caption", () => {
    const { container } = renderLang(
      `root = ReportPage([body])\nbody = ReportHeadline("On the page")`,
    );
    const page = container.querySelector('[data-slot="vgb-report-page"]');
    expect(page).not.toBeNull();
    expect(page?.textContent).toContain("On the page");
  });

  it("keeps a report table scrollable inside its own box", () => {
    // A wide table must not widen the document; the page has to stay the width
    // of the paper or printing and the chat column both break.
    const { container } = renderLang(
      `root = ReportTable(["A", "B"], [r])\nr = ["1", "2"]`,
    );
    expect(container.querySelector(".vgb-scroll-x")).not.toBeNull();
  });
});
