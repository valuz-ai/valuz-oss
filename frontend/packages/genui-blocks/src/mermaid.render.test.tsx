import { Renderer } from "@openuidev/react-lang";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createValuzLibrary } from "./library";

const SOURCE = `graph TD
  A[Ingest] --> B[Parse]
  B --> C[Render]`;

function renderLang(source: string) {
  return render(<Renderer library={createValuzLibrary()} response={source} />);
}

describe("Mermaid block", () => {
  it("preserves the diagram source verbatim", () => {
    // The source is the payload, not decoration: a host that hydrates this
    // into a real diagram re-parses exactly this text, so any whitespace or
    // arrow the renderer mangles becomes a broken diagram downstream.
    const { container } = renderLang(`root = Mermaid(${JSON.stringify(SOURCE)}, "Pipeline")`);
    const pre = container.querySelector("pre");
    expect(pre?.textContent).toBe(SOURCE);
    expect(screen.getByText("Pipeline")).toBeTruthy();
  });

  it("exposes the hydration hook on the root, outside the source element", () => {
    // Hosts swap the <pre> for rendered SVG. The hook has to sit on an
    // ancestor of it, or hydration would delete the element it was attached to
    // along with the caption.
    const { container } = renderLang(`root = Mermaid(${JSON.stringify(SOURCE)}, "Pipeline")`);
    const hook = container.querySelector("[data-vgb-mermaid]");
    expect(hook).not.toBeNull();
    expect(hook?.querySelector("pre")).not.toBeNull();
  });

  it("renders a badge label", () => {
    renderLang(`root = MermaidBadge("flowchart")`);
    expect(screen.getByText("flowchart")).toBeTruthy();
  });
});
