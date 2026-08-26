import { useEffect, useRef, useState, type ReactNode } from "react";

import {
  ArtifactTabBar,
  ArtifactViewerShell,
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
  useGroupRef,
} from "@valuz/ui";

import type { UseArtifactFileResult } from "../hooks/use-artifact-file";
import { usePreviewCloseShortcut } from "../hooks/use-preview-close-shortcut";

/** Panel ids for the content ↔ artifact-preview split. */
const CONTENT_PANEL_ID = "content";
const ARTIFACT_PANEL_ID = "artifact";

/** Every open starts at an even 50/50. The drag is a per-viewing adjustment,
 *  not a remembered preference, so opening a document stays predictable. */
const ARTIFACT_SPLIT_DEFAULT = 50;

/**
 * Floors for the two columns, in px rather than percentages: what makes a
 * column unusable is an absolute width, not a share of whatever the window
 * happens to be.
 *
 * ``CONTENT_MIN_PX`` matches the reading column the surfaces lay out
 * (``max-w-[760px]``): narrower than that and the conversation is being
 * squeezed, not merely shortened. ``ARTIFACT_MIN_PX`` is what a document
 * needs before its code blocks and tables start wrapping badly.
 *
 * Below their sum there is no split worth showing, and the preview covers
 * the content instead — a document the reader opened must appear either
 * way, and covering keeps both panes usable where splitting keeps neither.
 */
const CONTENT_MIN_PX = 760;
const ARTIFACT_MIN_PX = 480;
const SPLIT_MIN_PX = CONTENT_MIN_PX + ARTIFACT_MIN_PX;

/** Frames to wait for the artifact panel to register with the Group before
 *  giving up on resetting the split. Registration lands on the next frame in
 *  practice; the budget only exists so a change upstream can't spin forever. */
const SPLIT_RESET_MAX_FRAMES = 10;

export interface ArtifactSplitPaneProps {
  /** The whole `useArtifactFile` result — tabs, active document, loaders. */
  file: UseArtifactFileResult;
  onReload: () => void;
  /** Dismiss the whole preview. Distinct from closing one tab. */
  onClose: () => void;
  onCopyContent: () => void;
  onOpenExternal: () => void;
  /** The surface's own content, rendered as the left column. */
  children: ReactNode;
}

/**
 * Puts a surface's content and its document preview side by side.
 *
 * The preview opens as a right-hand pane at an even 50/50, separated by a
 * hairline the reader can drag, with the open documents named in a tab strip
 * above it. Shared by the conversation, task and project surfaces so all three
 * read and behave the same.
 */
export function ArtifactSplitPane({
  file,
  onReload,
  onClose,
  onCopyContent,
  onOpenExternal,
  children,
}: ArtifactSplitPaneProps) {
  const {
    tabs,
    activePath,
    activate,
    closeTab,
    artifact,
    content,
    target,
    loading,
    error,
  } = file;

  // Measure the pane instead of the viewport: the sidebar collapses at its own
  // breakpoint and the right panel at another, so the same window width leaves
  // wildly different room here. Folding when the columns no longer fit keeps
  // the open tabs in state — widen again and they return.
  const groupElementRef = useRef<HTMLDivElement | null>(null);
  const [splitFits, setSplitFits] = useState(true);
  useEffect(() => {
    const element = groupElementRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setSplitFits(entry.contentRect.width >= SPLIT_MIN_PX);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // A document is open whenever there are tabs; ``splitFits`` only decides
  // *how* it is shown — beside the content, or over it.
  const open = tabs.length > 0;
  const overlay = open && !splitFits;
  usePreviewCloseShortcut({
    active: open && activePath !== null,
    onClose: () => {
      if (activePath) closeTab(activePath);
    },
  });

  // Force the even split on every open. The Group remembers each panel's size
  // by id across mount/unmount, so the artifact panel's `defaultSize` only
  // takes effect the very first time it registers — without this, a session
  // where the reader once dragged the divider would reopen every later
  // document at that stale ratio.
  //
  // The artifact Panel isn't in the Group's layout yet when this component's
  // effects run (`getLayout()` still reports one panel), and `setLayout` with
  // two entries against a one-panel group throws `Invalid 1 panel layout`. So
  // poll a couple of frames until the registration lands — and give up rather
  // than spin forever if the panel never shows up.
  const groupRef = useGroupRef();
  useEffect(() => {
    if (!open || overlay) return;
    let frame = 0;
    let attemptsLeft = SPLIT_RESET_MAX_FRAMES;
    const applyEvenSplit = () => {
      const group = groupRef.current;
      if (!group) return;
      if (Object.keys(group.getLayout()).length !== 2) {
        if (attemptsLeft-- > 0) frame = requestAnimationFrame(applyEvenSplit);
        return;
      }
      group.setLayout({
        [CONTENT_PANEL_ID]: 100 - ARTIFACT_SPLIT_DEFAULT,
        [ARTIFACT_PANEL_ID]: ARTIFACT_SPLIT_DEFAULT,
      });
    };
    applyEvenSplit();
    return () => cancelAnimationFrame(frame);
  }, [open, overlay, groupRef]);

  const viewer = (
    <div
      className="flex h-full min-h-0 flex-col overflow-hidden overscroll-contain bg-surface"
      onWheel={(event) => event.stopPropagation()}
      onTouchMove={(event) => event.stopPropagation()}
    >
      {/* The strip names the open documents, so the shell below runs its
          compact header — otherwise the file name would appear twice and
          cost a row of content in a half-width pane. */}
      <ArtifactTabBar
        tabs={tabs.map((tab) => ({
          path: tab.path,
          name: tab.name,
          previewKind: tab.artifact?.previewKind ?? null,
          loading: tab.loading,
          error: Boolean(tab.error),
        }))}
        activePath={activePath}
        onActivate={activate}
        onClose={closeTab}
      />
      <div className="min-h-0 flex-1">
        <ArtifactViewerShell
          artifact={artifact}
          content={content}
          target={target}
          loading={loading}
          error={error}
          framed={false}
          compactHeader
          onReload={onReload}
          onClose={onClose}
          onCopyContent={onCopyContent}
          onOpenExternal={onOpenExternal}
        />
      </div>
    </div>
  );

  return (
    <ResizablePanelGroup
      className="relative min-h-0 bg-surface"
      groupRef={groupRef}
      elementRef={groupElementRef}
    >
      <ResizablePanel
        id={CONTENT_PANEL_ID}
        minSize={`${CONTENT_MIN_PX}px`}
        style={{ overflow: "hidden" }}
      >
        {children}
      </ResizablePanel>
      {open && !overlay ? (
        <>
          <ResizableHandle className="transition-colors data-[separator=active]:bg-brand data-[separator=focus]:bg-brand data-[separator=hover]:bg-brand/60" />
          <ResizablePanel
            id={ARTIFACT_PANEL_ID}
            defaultSize={`${ARTIFACT_SPLIT_DEFAULT}%`}
            minSize={`${ARTIFACT_MIN_PX}px`}
            style={{ overflow: "hidden" }}
          >
            {viewer}
          </ResizablePanel>
        </>
      ) : null}
      {overlay ? (
        // Too narrow to seat both columns: the document covers the content
        // rather than disappearing. Same viewer, so its close button and the
        // tab strip behave exactly as they do in the split.
        <div className="absolute inset-0 z-20 bg-surface">{viewer}</div>
      ) : null}
    </ResizablePanelGroup>
  );
}
