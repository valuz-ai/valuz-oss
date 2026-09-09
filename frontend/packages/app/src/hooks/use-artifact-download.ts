import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";
// Module-level ``t``: these are one-shot toasts fired from a callback, which
// must not take the hook's ``t`` as a dependency (see .claude/rules/frontend.md).
import { t } from "@valuz/shared/i18n";

import type { UseArtifactFileResult } from "./use-artifact-file";

export interface UseArtifactDownloadResult {
  /**
   * Save a file. ``path`` omitted = the open document. Safe to wire straight
   * to a list row: the busy state is tracked per path, so a row's save never
   * spins the viewer's control for a different file.
   */
  download: (path?: string) => Promise<void>;
  /** Click handler for the viewer's own control (always the open document). */
  handleDownload: () => void;
  /** Whether the *open document* is saving. */
  downloading: boolean;
}

/**
 * The download verb, with its busy state and its failure messages.
 *
 * Shared by every surface that shows an artifact (conversation, project home,
 * task detail) so the three do not drift on which outcomes deserve a toast —
 * they already drifted once on which of them offered downloading at all.
 *
 * Only real failures are announced: the address path hands off to the
 * browser's own download UI, and a dismissed save dialog is the user saying no.
 */
export function useArtifactDownload(
  artifactFile: UseArtifactFileResult,
): UseArtifactDownloadResult {
  const { download: downloadArtifact, selectedPath } = artifactFile;
  // Which paths are saving right now. A set, not a flag: one boolean would be
  // cleared by whichever of two concurrent saves finished first.
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());
  // Read through a ref so this callback keeps a stable identity across tab
  // switches — surfaces thread it into memos that rebuild whole panels.
  const selectedPathRef = useRef(selectedPath);
  selectedPathRef.current = selectedPath;

  const download = useCallback(
    async (path?: string) => {
      const key = path ?? selectedPathRef.current;
      if (key) setBusy((prev) => new Set(prev).add(key));
      try {
        const outcome = await downloadArtifact(path);
        if (outcome.ok || outcome.reason === "cancelled") return;
        toast.error(
          outcome.reason === "unavailable"
            ? t("ui.artifact.downloadUnavailable")
            : outcome.reason === "too_large"
              ? t("ui.artifact.downloadTooLarge")
              : t("ui.artifact.downloadFailed", { error: outcome.message }),
        );
      } finally {
        if (key) {
          setBusy((prev) => {
            const next = new Set(prev);
            next.delete(key);
            return next;
          });
        }
      }
    },
    [downloadArtifact],
  );

  const handleDownload = useCallback(() => {
    void download();
  }, [download]);

  return {
    download,
    handleDownload,
    downloading: selectedPath ? busy.has(selectedPath) : false,
  };
}
