import { render, screen } from "@testing-library/react";
import { Renderer } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";
import { describe, expect, it } from "vitest";

import { blockComponentGroups, blockComponents, createValuzLibrary } from "./library";

/** The slice of a zod field this file needs; avoids depending on zod internals. */
type ZodField = { safeParse: (value: unknown) => { success: boolean } };

const KPI_STRIP = `root = Stack([strip])
strip = MiniCardBlock([a, b, c])
a = MiniCard("Revenue", "$4.2M", "+12.4%", "up")
b = MiniCard("Churn", "2.1%", "-0.3pp", "down")
c = MiniCard("Headcount", "184")`;

describe("valuz genui blocks", () => {
  it("renders blocks alongside OpenUI components from one OpenUI Lang program", () => {
    render(<Renderer library={createValuzLibrary()} response={KPI_STRIP} />);
    expect(screen.getByText("Revenue")).toBeTruthy();
    expect(screen.getByText("$4.2M")).toBeTruthy();
    expect(screen.getByText("+12.4%")).toBeTruthy();
    expect(screen.getByText("Headcount")).toBeTruthy();
  });

  it("keeps every OpenUI component available after merging", () => {
    const lib = createValuzLibrary();
    // Stack is OpenUI's root component; losing it would break every document.
    expect(lib.components["Stack"]).toBeTruthy();
    expect(lib.components["MiniCardBlock"]).toBeTruthy();
  });

  it("registers every block component in a component group", () => {
    const grouped = new Set(blockComponentGroups.flatMap((g) => g.components));
    const missing = blockComponents.map((c) => c.name).filter((n) => !grouped.has(n));
    expect(missing).toEqual([]);
  });

  it("names every grouped component in the component list", () => {
    const defined = new Set(blockComponents.map((c) => c.name));
    const dangling = blockComponentGroups
      .flatMap((g) => g.components)
      .filter((n) => !defined.has(n));
    expect(dangling).toEqual([]);
  });

  it("contributes every block to the generated prompt", () => {
    const prompt = createValuzLibrary().prompt();
    for (const c of blockComponents) expect(prompt).toContain(c.name);
  });

  it("never shadows an OpenUI component", () => {
    // Merging puts blocks last, so a block sharing a name with an OpenUI
    // component would silently replace it for every document — losing e.g.
    // Card or Table with no error anywhere. Names are chosen by whoever adds a
    // block, so this has to be checked rather than assumed.
    const openuiNames = new Set(Object.keys(openuiLibrary.components));
    const shadowed = blockComponents.map((c) => c.name).filter((n) => openuiNames.has(n));
    expect(shadowed).toEqual([]);
  });

  it("gives every block a distinct name", () => {
    const names = blockComponents.map((c) => c.name);
    expect(names.length).toBe(new Set(names).size);
  });

  it("declares every required prop before the first optional one", () => {
    // OpenUI Lang binds arguments positionally in zod key order, so a required
    // prop declared after an optional one cannot be reached by the shortest
    // call that supplies it — the argument silently lands on the optional prop
    // instead. Nothing reports this: not the parser, not TypeScript. The whole
    // block just renders empty. Checking the invariant is the only defence,
    // and it caught the entire report family once already.
    const offenders: string[] = [];
    for (const block of blockComponents) {
      const shape = (block.props as unknown as { shape?: Record<string, ZodField> }).shape;
      if (!shape) continue;
      let seenOptional: string | null = null;
      for (const [key, field] of Object.entries(shape)) {
        const optional = field.safeParse(undefined).success;
        if (optional) {
          seenOptional ??= key;
        } else if (seenOptional) {
          offenders.push(`${block.name}: required "${key}" declared after optional "${seenOptional}"`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("describes every block for the model", () => {
    // `description` is prompt text, not documentation: a block with a thin one
    // is a block the model will reach for at the wrong moment.
    const thin = blockComponents
      .filter((c) => (c.description ?? "").trim().length < 40)
      .map((c) => c.name);
    expect(thin).toEqual([]);
  });
});
