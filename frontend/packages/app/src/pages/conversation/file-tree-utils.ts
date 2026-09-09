import type { ProjectFileNode } from "@valuz/core";
import type { FileTreeNode } from "@valuz/ui";

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

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
