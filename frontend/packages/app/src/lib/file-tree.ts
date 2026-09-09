import type { FileTreeNode } from "@valuz/ui";
import type { ProjectFileNode } from "@valuz/core";

/** Map ``projectsApi.listFiles`` raw nodes onto ``ProjectFileTree``'s
 *  ``FileTreeNode`` shape:
 *  - rename ``type: "directory"`` → ``"folder"``
 *  - compute the cumulative slash-joined ``path`` each child needs to
 *    identify itself when the tree is interacted with
 *  - carry ``truncated`` through, so a folder the walk did not enter is
 *    distinguishable from an empty one
 *
 *  The single home for these: the conversation panel, the project home page
 *  and the task detail page all render the same tree, and when each kept its
 *  own copy of this mapping only one of them learned about ``truncated`` —
 *  the other two silently drew every unlisted subtree as an empty folder. */
export function toFileTree(
  nodes: ProjectFileNode[],
  prefix = "",
): FileTreeNode[] {
  return nodes.map((n) => {
    const path = prefix ? `${prefix}/${n.name}` : n.name;
    const result: FileTreeNode = {
      name: n.name,
      type: n.type === "directory" ? "folder" : "file",
      path,
    };
    if (n.children) result.children = toFileTree(n.children, path);
    // A directory the walk stopped at. Carried through so the tree can tell
    // "not listed yet" from "empty" and ask for the level below on expand.
    if (n.truncated) result.truncated = true;
    return result;
  });
}

/**
 * Return a copy of ``tree`` with ``folderPath``'s children replaced.
 *
 * The node stops being ``truncated``: the level below is now present, and its
 * own children carry their own flags, so expansion continues one level at a
 * time from there. Returns ``tree`` unchanged when the path is not in it —
 * a load that resolves after the tree was refreshed out from under it must not
 * graft stale children onto a folder that no longer exists.
 */
export function mergeFolderChildren(
  tree: FileTreeNode[],
  folderPath: string,
  children: FileTreeNode[],
): FileTreeNode[] {
  let changed = false;
  const walk = (nodes: FileTreeNode[]): FileTreeNode[] =>
    nodes.map((node) => {
      if (node.path === folderPath) {
        changed = true;
        return { ...node, children, truncated: false };
      }
      // Only descend where the target could be — every path is prefixed by its
      // ancestors', so a sibling subtree can be skipped whole.
      if (node.children?.length && folderPath.startsWith(`${node.path}/`)) {
        return { ...node, children: walk(node.children) };
      }
      return node;
    });
  const next = walk(tree);
  return changed ? next : tree;
}

/**
 * Re-apply what a previous tree had already loaded onto a freshly fetched one.
 *
 * A refresh re-fetches the tree at the initial depth, so every level the user
 * expanded is missing from the new listing and comes back marked ``truncated``.
 * Dropping those would empty each expanded folder on screen — and nothing
 * would refetch them, because the tree only loads a level when the user clicks
 * to open it. So the loaded children are carried across, and the folder keeps
 * showing what it was showing.
 *
 * Only folders the refresh itself declined to list are restored: where the new
 * listing has its own children, those are authoritative — that is the whole
 * point of refreshing.
 */
export function preserveLoadedChildren(
  previous: FileTreeNode[],
  next: FileTreeNode[],
): FileTreeNode[] {
  if (previous.length === 0) return next;
  const before = new Map(previous.map((node) => [node.path, node]));
  return next.map((node) => {
    const old = before.get(node.path);
    if (!old || node.type !== "folder") return node;
    if (node.truncated && old.children?.length) {
      return { ...node, children: old.children, truncated: false };
    }
    if (node.children?.length && old.children?.length) {
      return {
        ...node,
        children: preserveLoadedChildren(old.children, node.children),
      };
    }
    return node;
  });
}
