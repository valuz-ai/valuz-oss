// Re-exported so the conversation page's existing importers are unaffected;
// the implementations live in the shared lib beside the other tree helpers.
export {
  toFileTree,
  mergeFolderChildren,
  preserveLoadedChildren,
} from "../../lib/file-tree";

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
