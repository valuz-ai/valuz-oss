/**
 * Tree shaping for on-demand expansion.
 *
 * ``truncated`` has to survive the wire→UI conversion (it is the only thing
 * separating "not listed yet" from "empty"), and grafting a loaded level in
 * must clear it — otherwise the folder would re-fetch on every reopen.
 */

import { describe, expect, it } from "vitest";
import type { ProjectFileNode } from "@valuz/core";
import type { FileTreeNode } from "@valuz/ui";

import { mergeFolderChildren, toFileTree } from "./file-tree-utils";

const dir = (
  name: string,
  extra: Partial<ProjectFileNode> = {},
): ProjectFileNode => ({
  name,
  type: "directory",
  size: null,
  modified: null,
  ...extra,
});

const file = (name: string): ProjectFileNode => ({
  name,
  type: "file",
  size: 1,
  modified: null,
});

describe("toFileTree", () => {
  it("carries the truncated marker through", () => {
    const [node] = toFileTree([dir("deep", { truncated: true })]);
    expect(node.truncated).toBe(true);
  });

  it("leaves an untruncated folder unmarked", () => {
    const [node] = toFileTree([dir("empty")]);
    expect(node.truncated).toBeUndefined();
  });

  it("builds paths from the prefix so a lazily loaded level is addressable", () => {
    const nodes = toFileTree([file("deep.txt")], "a/b/c");
    expect(nodes[0].path).toBe("a/b/c/deep.txt");
  });
});

describe("mergeFolderChildren", () => {
  const tree: FileTreeNode[] = [
    {
      name: "a",
      type: "folder",
      path: "a",
      children: [
        { name: "b", type: "folder", path: "a/b", truncated: true },
        { name: "sibling", type: "folder", path: "a/sibling", truncated: true },
      ],
    },
  ];

  it("grafts the loaded level onto the right folder and clears the flag", () => {
    const next = mergeFolderChildren(tree, "a/b", [
      { name: "c", type: "folder", path: "a/b/c", truncated: true },
    ]);
    const b = next[0].children![0];
    expect(b.children).toHaveLength(1);
    expect(b.truncated).toBe(false);
    // The level below keeps its own flag: expansion continues one at a time.
    expect(b.children![0].truncated).toBe(true);
  });

  it("leaves sibling subtrees untouched", () => {
    const next = mergeFolderChildren(tree, "a/b", []);
    expect(next[0].children![1]).toBe(tree[0].children![1]);
  });

  it("ignores a load that lands after the tree was refreshed away", () => {
    // The folder is gone (a refresh replaced the tree). Grafting stale children
    // onto whatever occupies that path now would be worse than dropping them.
    const next = mergeFolderChildren(tree, "a/gone", [file("x") as never]);
    expect(next).toBe(tree);
  });
});
