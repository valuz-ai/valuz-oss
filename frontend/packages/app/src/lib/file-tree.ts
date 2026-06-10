import type { FileTreeNode } from "@valuz/ui";
import type { ProjectFileNode } from "@valuz/core";

/** Map ``projectsApi.listFiles`` raw nodes onto ``ProjectFileTree``'s
 *  ``FileTreeNode`` shape:
 *  - rename ``type: "directory"`` → ``"folder"``
 *  - compute the cumulative slash-joined ``path`` each child needs to
 *    identify itself when the tree is interacted with
 *
 *  Used by both ProjectDetailPage's main file panel and TaskDetailPage's
 *  right-rail Files tab so the two surfaces share the same tree
 *  semantics. */
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
    return result;
  });
}
