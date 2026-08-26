export type ArtifactPreviewKind =
  | "markdown"
  | "code"
  | "image"
  | "pdf"
  | "html"
  | "docx"
  | "media"
  | "spreadsheet"
  | "plain"
  | "unsupported";

export interface ArtifactDescriptor {
  id: string;
  kind: string;
  projectId?: string;
  path?: string;
  name: string;
  mimeType?: string | null;
  extension?: string | null;
  size?: number | null;
  modifiedAt?: string | null;
  previewKind: ArtifactPreviewKind;
  capabilities: {
    canPreview: boolean;
    canEdit: boolean;
    canOpenExternal: boolean;
    canCopyContent: boolean;
    canDownload: boolean;
  };
}

export type ArtifactContent =
  | {
      kind: "text";
      encoding: "utf-8";
      content: string;
      truncated: boolean;
      etag?: string | null;
      modifiedAt?: string | null;
    }
  | {
      kind: "binary";
      openUrl: string;
      mimeType: string;
      size?: number | null;
      reason?: string | null;
    }
  | {
      kind: "external";
      openUrl?: string | null;
      reason: string;
    };

export interface ArtifactOpenTarget {
  /** One-based physical page number. */
  page?: number;
}

export interface ArtifactViewerShellProps {
  artifact: ArtifactDescriptor | null;
  content: ArtifactContent | null;
  target?: ArtifactOpenTarget | null;
  loading?: boolean;
  error?: string | null;
  /** Draw the viewer's own panel frame. Disable when embedded in a framed host. */
  framed?: boolean;
  /**
   * Collapse the header to a single metadata row. Use when the host already
   * names the document above the shell (e.g. a tab strip) — the tall title
   * block would just repeat the tab label and eat the content area.
   */
  compactHeader?: boolean;
  onReload?: () => void;
  onClose?: () => void;
  onCopyContent?: () => void;
  onOpenExternal?: () => void;
}

export interface ArtifactRendererProps {
  artifact: ArtifactDescriptor;
  content: ArtifactContent | null;
  target?: ArtifactOpenTarget | null;
  /** Wrap long source lines instead of requiring horizontal scrolling. */
  wrapLines?: boolean;
  onOpenExternal?: () => void;
  /**
   * Re-resolve the file and rebuild ``content``. Renderers that fetch from
   * ``openUrl`` must offer this on load failure: a remote address is a
   * short-lived presigned URL, so retrying the SAME url after it expires can
   * only fail again — recovery needs a fresh resolve.
   */
  onReload?: () => void;
}
