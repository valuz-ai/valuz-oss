import { useCallback, useState } from "react";
import { toast } from "sonner";
import { useTranslation, type SessionListItem } from "@valuz/core";
// Module-level ``t`` for the toast below: a side-effecting callback must not
// take the hook's ``t`` as a dependency (see .claude/rules/frontend.md).
import { t as _t } from "@valuz/shared/i18n";
import { type ArtifactOpenTarget } from "@valuz/ui";
import { usePlatform } from "@valuz/app/platform";
import { useConversationLocalFileLinks } from "@valuz/app/hooks";
import { type DirectoryFieldMode } from "@valuz/app/layout";
import { useArtifactFile } from "../../hooks/use-artifact-file";
import {
  toAbsoluteProjectPath,
  toProjectRelativePath,
} from "../../lib/project-paths";

type ArtifactPaneParams = {
  selectedProjectId: string | null;
  selectedSessionId: string | null;
  activeWorktree: SessionListItem["worktree"] | null;
  activeProjectRootPath: string;
  directoryFieldMode: DirectoryFieldMode;
};

/**
 * ── Artifact split pane ──────────────────────────────────────────────
 *
 * Owns the artifact preview cluster of the conversation page: the
 * ``locateArtifactFile`` root resolution, the ``useArtifactFile``
 * document state, the ``openArtifactFile`` verb, the local-file link
 * interception (``useConversationLocalFileLinks``), and the split-pane
 * toolbar handlers (reload / close / copy / open-external). Bodies are
 * moved verbatim from ``ConversationPage``.
 */
export function useArtifactPane({
  selectedProjectId,
  selectedSessionId,
  activeWorktree,
  activeProjectRootPath,
  directoryFieldMode,
}: ArtifactPaneParams) {
  const { t } = useTranslation();
  const platform = usePlatform();
  const { revealInFinder } = platform;

  const locateArtifactFile = useCallback(
    (path: string) => {
      // Session cwd = the worktree checkout when present, else the project cwd.
      const root = activeWorktree?.path ?? activeProjectRootPath;
      return {
        absolutePath: toAbsoluteProjectPath(path, root),
        relativePath: toProjectRelativePath(path, root) ?? path,
      };
    },
    [activeProjectRootPath, activeWorktree],
  );
  const artifactProjectId =
    selectedProjectId && selectedProjectId !== "chat-default"
      ? selectedProjectId
      : null;
  const artifactFile = useArtifactFile({
    projectId: artifactProjectId,
    platform,
    locate: locateArtifactFile,
    missingErrorMessage: t(
      "task.artifactOpenInFinder" as Parameters<typeof t>[0],
    ),
    // The file lives on the backend that owns the conversation — route the
    // resolve with the same ref the rest of this page uses.
    baseRef: {
      sessionId: selectedSessionId ?? undefined,
      projectId: artifactProjectId ?? undefined,
    },
    // The preview pane carries a tab strip, so opening a second document adds
    // to the set instead of replacing what's on screen.
    multiTab: true,
  });
  // The split pane consumes the loaded document itself; the page only needs
  // the active path (for the reveal action) and the open/reload/close verbs.
  const {
    selectedPath: selectedArtifactPath,
    content: artifactContent,
    open: openArtifact,
    reload: reloadArtifact,
    close: closeArtifact,
    download: downloadArtifact,
  } = artifactFile;
  // Purely presentational: the viewer's download control shows a spinner while
  // the resolve (and, on the blob fallback, the fetch) is in flight.
  const [downloading, setDownloading] = useState(false);

  const openArtifactFile = useCallback(
    async (path: string, target?: ArtifactOpenTarget) => {
      await openArtifact(path, target);
    },
    [openArtifact],
  );

  const localFileLinkRootPath = activeWorktree?.path ?? activeProjectRootPath;
  const localFileLinks = useConversationLocalFileLinks({
    projectRootPath: localFileLinkRootPath,
    runtimeMode: directoryFieldMode === "managed" ? "managed" : "local",
    previewFile: (path, target) => {
      void openArtifactFile(path, target);
    },
    openFile: (path) => {
      void revealInFinder(path);
    },
    blockFile: () => {
      toast.info(t("project.managedDirHint" as Parameters<typeof t>[0]));
    },
  });

  const handleArtifactReload = useCallback(() => {
    void reloadArtifact();
  }, [reloadArtifact]);

  const handleArtifactClose = useCallback(() => {
    closeArtifact();
  }, [closeArtifact]);

  const handleArtifactCopy = useCallback(() => {
    if (artifactContent?.kind !== "text") return;
    void navigator.clipboard
      ?.writeText(artifactContent.content)
      .then(() => toast.success(t("common.copied" as Parameters<typeof t>[0])))
      .catch(() => toast.error(t("common.failed" as Parameters<typeof t>[0])));
  }, [artifactContent, t]);

  const handleArtifactOpenExternal = useCallback(() => {
    if (!selectedArtifactPath) return;
    void revealInFinder(locateArtifactFile(selectedArtifactPath).absolutePath);
  }, [locateArtifactFile, revealInFinder, selectedArtifactPath]);

  /**
   * Save a file to the user's machine. ``path`` omitted = the open document.
   *
   * The only outcome worth a toast is a real failure: the address path hands
   * off to the browser's own download UI, and a dismissed save dialog is the
   * user saying no.
   */
  const downloadArtifactFile = useCallback(
    async (path?: string) => {
      setDownloading(true);
      try {
        const outcome = await downloadArtifact(path);
        if (outcome.ok || outcome.reason === "cancelled") return;
        toast.error(
          outcome.reason === "unavailable"
            ? _t("ui.artifact.downloadUnavailable")
            : _t("ui.artifact.downloadFailed", { error: outcome.message }),
        );
      } finally {
        setDownloading(false);
      }
    },
    [downloadArtifact],
  );

  const handleArtifactDownload = useCallback(() => {
    void downloadArtifactFile();
  }, [downloadArtifactFile]);

  return {
    artifactFile,
    closeArtifact,
    openArtifactFile,
    downloadArtifactFile,
    localFileLinks,
    handleArtifactReload,
    handleArtifactClose,
    handleArtifactCopy,
    handleArtifactOpenExternal,
    handleArtifactDownload,
    artifactDownloading: downloading,
  };
}
