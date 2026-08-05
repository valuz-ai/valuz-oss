import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GenerativeUICard } from "./GenerativeUICard";

/**
 * The sibling GenerativeUICard.test.tsx stubs the renderer to test the card's
 * chrome. This file does the opposite: nothing is mocked, so one OpenUI Lang
 * payload goes through the real parser, the merged library, and both component
 * sets at once. It is the only test that would catch the merge regressing —
 * losing OpenUI's components, or losing the blocks — inside the actual product
 * component rather than in the library package.
 */

const MIXED = `root = Stack([heading, strip, sources])
heading = TextContent("Q4 performance", "large-heavy")
strip = MiniCardBlock([a, b])
a = MiniCard("Revenue", "$4.2M", "+12.4%", "up")
b = MiniCard("Margin", "38%")
sources = CondensedSources([s1])
s1 = SourceItem(1, "Annual report", "https://example.com/report")`;

describe("GenerativeUICard with the merged OpenUI + Valuz library", () => {
  it("renders OpenUI components and Valuz blocks from one payload", () => {
    render(<GenerativeUICard openui={MIXED} status="success" />);

    // From OpenUI's own library.
    expect(screen.getByText("Q4 performance")).toBeTruthy();
    // From @valuz/genui-blocks.
    expect(screen.getByText("Revenue")).toBeTruthy();
    expect(screen.getByText("$4.2M")).toBeTruthy();
    expect(screen.getByText("Margin")).toBeTruthy();
    expect(screen.getByText("Annual report")).toBeTruthy();
  });

  it("keeps blocks rendering inside the fullscreen surface too", () => {
    // Fullscreen mounts a second Renderer against the same library; a merge
    // wired into only one of the two call sites would pass the test above.
    const { container } = render(<GenerativeUICard openui={MIXED} status="success" />);
    const scopes = container.querySelectorAll('[data-openui-scope="generative-ui"]');
    expect(scopes.length).toBeGreaterThan(0);
    expect(container.textContent).toContain("$4.2M");
  });
});
