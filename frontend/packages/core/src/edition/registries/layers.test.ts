import { describe, expect, it } from "vitest";

import { applyLayers, type LayerOp, type Placement } from "./layers";

interface Item {
  id: string;
  tag?: string;
}

const item = (id: string, tag?: string): Item => ({ id, tag });
const put = (entry: Item, placement?: Placement): LayerOp<Item> => ({
  op: "put",
  item: entry,
  placement,
});
const remove = (key: string): LayerOp<Item> => ({ op: "remove", key });
const apply = (base: Item[], layers: LayerOp<Item>[]) =>
  applyLayers(base, layers, (entry) => entry.id).map((entry) =>
    entry.tag ? `${entry.id}:${entry.tag}` : entry.id,
  );

describe("applyLayers", () => {
  const base = [item("a"), item("b"), item("c")];

  it("returns a copy of the base when there are no layers", () => {
    const out = applyLayers(base, [], (entry) => entry.id);
    expect(out).toEqual(base);
    expect(out).not.toBe(base);
  });

  it("appends new ids and replaces existing ids in place", () => {
    expect(apply(base, [put(item("d")), put(item("b", "new"))])).toEqual([
      "a",
      "b:new",
      "c",
      "d",
    ]);
  });

  it("lets the last of several replacements win", () => {
    expect(apply(base, [put(item("b", "1")), put(item("b", "2"))])).toEqual([
      "a",
      "b:2",
      "c",
    ]);
  });

  it("applies removals to the list as it stands at that point", () => {
    expect(apply(base, [remove("b"), put(item("b", "again"))])).toEqual([
      "a",
      "c",
      "b:again",
    ]);
    expect(apply(base, [put(item("b", "x")), remove("b")])).toEqual([
      "a",
      "c",
    ]);
  });

  it("prefers `after` when both anchors resolve, and falls back to `before`", () => {
    expect(
      apply(base, [put(item("x"), { after: "a", before: "c" })]),
    ).toEqual(["a", "x", "b", "c"]);
    expect(
      apply(base, [put(item("x"), { after: "missing", before: "c" })]),
    ).toEqual(["a", "b", "x", "c"]);
  });

  it("puts later `after` entries closer to the anchor", () => {
    expect(
      apply(base, [
        put(item("x"), { after: "a" }),
        put(item("y"), { after: "a" }),
      ]),
    ).toEqual(["a", "y", "x", "b", "c"]);
  });

  it("keeps `before` entries in registration order", () => {
    expect(
      apply(base, [
        put(item("x"), { before: "c" }),
        put(item("y"), { before: "c" }),
      ]),
    ).toEqual(["a", "b", "x", "y", "c"]);
  });

  it("takes the first anchor of a preference list that is in the list", () => {
    expect(
      apply(base, [put(item("x"), { after: ["gone", "b", "a"] })]),
    ).toEqual(["a", "b", "x", "c"]);
    expect(
      apply(base, [put(item("x"), { before: ["gone", "c", "a"] })]),
    ).toEqual(["a", "b", "x", "c"]);
  });

  it("keeps a chain intact when a middle link is missing, via its fallbacks", () => {
    // y follows x, then a; z follows y, x, then a. x never loads.
    const layers = [
      put(item("z"), { after: ["y", "x", "a"] }),
      put(item("y"), { after: ["x", "a"] }),
    ];
    expect(apply(base, layers)).toEqual(["a", "y", "z", "b", "c"]);
    // And once x does load, the whole chain lines up behind it.
    expect(
      apply(base, [...layers, put(item("x"), { after: "a" })]),
    ).toEqual(["a", "x", "y", "z", "b", "c"]);
  });

  it("parks an entry until its anchor shows up, and drops the wait if it is removed", () => {
    expect(
      apply(base, [put(item("p"), { after: "q" }), put(item("q"))]),
    ).toEqual(["a", "b", "c", "q", "p"]);
    expect(
      apply(base, [put(item("p"), { after: "q" }), remove("p")]),
    ).toEqual(["a", "b", "c"]);
  });

  it("lets a newer registration supersede one still waiting", () => {
    expect(
      apply(base, [
        put(item("p", "old"), { after: "never" }),
        put(item("p", "new"), { after: "a" }),
      ]),
    ).toEqual(["a", "p:new", "b", "c"]);
  });

  it("terminates on a placement cycle and keeps registration order", () => {
    expect(
      apply(base, [
        put(item("x"), { after: "y" }),
        put(item("y"), { after: "x" }),
      ]),
    ).toEqual(["a", "b", "c", "x", "y"]);
  });
});
