import { render, screen } from "@testing-library/react";
import { Renderer } from "@openuidev/react-lang";
import { createLibrary } from "@openuidev/react-lang";
import type { DefinedComponent } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { describe, expect, it } from "vitest";

import { Citation, CondensedSources, SourceItem, SourceList } from "./index";
import { safeHref } from "./safe-href";

const lib = createLibrary({
  root: "Stack",
  components: [
    ...(Object.values(openuiLibrary.components) as DefinedComponent[]),
    Citation,
    SourceItem,
    SourceList,
    CondensedSources,
  ] as unknown as DefinedComponent[],
});

const DOC = `root = Stack([p, list, cond])
p = TextContent("hello")
list = SourceList([s1, s2])
s1 = SourceItem(1, "Q3 filing", "https://sec.gov/a", "Revenue rose", "SEC EDGAR")
s2 = SourceItem(2, "Bad link", "javascript:alert(1)")
cond = CondensedSources([s3])
s3 = SourceItem(3, "Third")`;

describe("citation family", () => {
  it("safeHref allows http(s) and rejects everything else", () => {
    expect(safeHref("https://a.example/x")).toBe("https://a.example/x");
    expect(safeHref("javascript:alert(1)")).toBeUndefined();
    expect(safeHref("java\tscript:alert(1)")).toBeUndefined();
    expect(safeHref("JaVaScRiPt:alert(1)")).toBeUndefined();
    expect(safeHref("data:text/html,<script>")).toBeUndefined();
    expect(safeHref("/relative")).toBeUndefined();
    expect(safeHref(undefined)).toBeUndefined();
  });

  it("renders lists, items and safe links", () => {
    const { container } = render(<Renderer library={lib} response={DOC} />);
    expect(screen.getByText("Q3 filing").closest("a")?.getAttribute("href")).toBe(
      "https://sec.gov/a",
    );
    expect(screen.getByText("Bad link").closest("a")).toBeNull();
    expect(container.querySelectorAll("ol > li").length).toBe(3);
    expect(container.querySelector("details")).toBeTruthy();
    expect(screen.getByText("SEC EDGAR")).toBeTruthy();
  });

  it("renders a citation marker with an accessible name", () => {
    const { container } = render(
      <Renderer
        library={lib}
        response={`root = Stack([c])\nc = Citation(4, "Annual report", "https://x.example/r")`}
      />,
    );
    const a = container.querySelector('[data-slot="vgb-citation"]');
    expect(a?.tagName).toBe("A");
    expect(a?.getAttribute("aria-label")).toBe("Source 4: Annual report");
    expect(a?.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("counts sources for the condensed summary", () => {
    const { container } = render(
      <Renderer
        library={lib}
        response={`root = Stack([c])\nc = CondensedSources([a, b])\na = SourceItem(1, "A")\nb = SourceItem(2, "B")`}
      />,
    );
    expect(container.querySelector("summary")?.textContent).toBe("2 sources");
  });
});
