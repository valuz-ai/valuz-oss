import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  buildFileRef,
  filesApi,
  type ApiBaseRef,
  type ArtifactContent,
  type ArtifactDescriptor,
  type PlatformCapabilities,
} from "@valuz/core";
import type { ArtifactOpenTarget } from "@valuz/ui";

import { resolvedToArtifactFile } from "../lib/resolve-artifact";

export interface ArtifactFileLocation {
  /** Absolute identity handed to the file-address resolver. */
  absolutePath: string;
  /** Stable path shown in the shell and kept in page URL state. */
  relativePath: string;
}

/** One open document. ``path`` is the tab's identity — opening the same path
 *  twice focuses the existing tab rather than duplicating it. */
export interface ArtifactTab {
  path: string;
  /** Label for the tab strip. Falls back to the last path segment until the
   *  descriptor resolves, so a tab never renders nameless while loading. */
  name: string;
  artifact: ArtifactDescriptor | null;
  content: ArtifactContent | null;
  target: ArtifactOpenTarget | null;
  loading: boolean;
  error: string | null;
}

/**
 * Upper bound on simultaneously open documents. Each tab holds its resolved
 * content so switching is instant, and text previews are capped at 5 MiB
 * apiece — without a ceiling a long session browsing a big repo would pin an
 * unbounded amount of that in memory.
 */
export const MAX_OPEN_ARTIFACT_TABS = 10;

interface UseArtifactFileOptions {
  projectId: string | null;
  platform: PlatformCapabilities;
  locate: (path: string) => ArtifactFileLocation;
  missingErrorMessage: string;
  /**
   * Entity that owns the file, for per-entity backend routing. Pass the id the
   * surface already routes its own data with (conversation → session, task
   * detail → task); defaults to the project. Without it a cloud-owned file
   * would be resolved against the local backend and come back ``forbidden``.
   */
  baseRef?: ApiBaseRef;
  /**
   * Keep previously opened documents around as tabs instead of replacing the
   * selection. Off by default: a surface without a tab strip would otherwise
   * accumulate invisible tabs that the user has no way to close.
   */
  multiTab?: boolean;
}

export interface UseArtifactFileResult {
  /** Open documents, in the order they were opened (= tab strip order). */
  tabs: ArtifactTab[];
  /** Path of the focused tab, or null when nothing is open. */
  activePath: string | null;
  /** Focus an already-open tab. No-op for a path that isn't open. */
  activate: (path: string) => void;
  /** Close one tab; focus moves to its right neighbour, else its left. */
  closeTab: (path: string) => void;
  /** Fields of the focused tab, so single-document surfaces read unchanged. */
  selectedPath: string | null;
  artifact: ArtifactDescriptor | null;
  content: ArtifactContent | null;
  target: ArtifactOpenTarget | null;
  loading: boolean;
  error: string | null;
  open: (path: string, target?: ArtifactOpenTarget | null) => Promise<void>;
  reload: () => Promise<void>;
  /** Close every tab — the "dismiss the whole preview" action. */
  close: () => void;
}

const fileNameOf = (path: string): string =>
  path.split(/[\\/]/).filter(Boolean).pop() ?? path;

/**
 * Shared artifact-loader state for project, task, and conversation surfaces.
 *
 * A monotonically increasing request id protects each tab's contents even when
 * a transport ignores AbortSignal (notably the local Electron IPC read): a
 * slower request for a path can never overwrite a later one for that same
 * path. Ids are tracked per tab, so loading a second document never cancels
 * the first one's in-flight read.
 */
export function useArtifactFile({
  projectId,
  platform,
  locate,
  missingErrorMessage,
  baseRef,
  multiTab = false,
}: UseArtifactFileOptions): UseArtifactFileResult {
  const [tabs, setTabs] = useState<ArtifactTab[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const requestIdsRef = useRef<Map<string, number>>(new Map());
  const controllersRef = useRef<Map<string, AbortController>>(new Map());
  /** Most-recently-viewed first. Drives which tab is evicted at the ceiling. */
  const viewOrderRef = useRef<string[]>([]);

  // Depend on the ids, not on the caller's object identity: an inline literal
  // would otherwise rebuild ``open``/``reload`` on every render.
  const hasBaseRef = baseRef !== undefined;
  const {
    sessionId,
    projectId: refProjectId,
    taskId,
    automationId,
    kbId,
  } = baseRef ?? {};
  const resolveBaseRef: ApiBaseRef = useMemo(
    () =>
      hasBaseRef
        ? { sessionId, projectId: refProjectId, taskId, automationId, kbId }
        : { projectId: projectId ?? undefined },
    [
      hasBaseRef,
      sessionId,
      refProjectId,
      taskId,
      automationId,
      kbId,
      projectId,
    ],
  );

  const touchViewOrder = useCallback((path: string) => {
    viewOrderRef.current = [
      path,
      ...viewOrderRef.current.filter((p) => p !== path),
    ];
  }, []);

  const forgetTab = useCallback((path: string) => {
    requestIdsRef.current.delete(path);
    controllersRef.current.get(path)?.abort();
    controllersRef.current.delete(path);
    viewOrderRef.current = viewOrderRef.current.filter((p) => p !== path);
  }, []);

  const close = useCallback(() => {
    for (const controller of controllersRef.current.values())
      controller.abort();
    requestIdsRef.current.clear();
    controllersRef.current.clear();
    viewOrderRef.current = [];
    setTabs([]);
    setActivePath(null);
  }, []);

  const activate = useCallback(
    (path: string) => {
      setTabs((prev) => {
        if (!prev.some((tab) => tab.path === path)) return prev;
        touchViewOrder(path);
        setActivePath(path);
        return prev;
      });
    },
    [touchViewOrder],
  );

  const closeTab = useCallback(
    (path: string) => {
      forgetTab(path);
      setTabs((prev) => {
        const index = prev.findIndex((tab) => tab.path === path);
        if (index === -1) return prev;
        const next = prev.filter((tab) => tab.path !== path);
        setActivePath((current) => {
          if (current !== path) return current;
          // Right neighbour first — that's where the eye already is after a
          // close; fall back to the left one, then to nothing.
          const successor = next[index] ?? next[index - 1] ?? null;
          if (successor) touchViewOrder(successor.path);
          return successor?.path ?? null;
        });
        return next;
      });
    },
    [forgetTab, touchViewOrder],
  );

  const open = useCallback(
    async (path: string, openTarget?: ArtifactOpenTarget | null) => {
      if (!projectId) return;

      const location = locate(path);
      const key = location.relativePath;
      const alreadyOpen = tabs.some((tab) => tab.path === key);

      touchViewOrder(key);
      setActivePath(key);

      // Re-opening a document that's already loaded is a focus change, not a
      // refetch — unless a target (e.g. a PDF page) has to be applied.
      if (alreadyOpen && multiTab && !openTarget) return;

      if (!multiTab) {
        // Single-document mode replaces the selection outright, so anything
        // still in flight for another path is now dead weight — drop it before
        // it can burn a content read nobody will see.
        for (const [path, inflight] of controllersRef.current) {
          if (path === key) continue;
          inflight.abort();
          controllersRef.current.delete(path);
          requestIdsRef.current.delete(path);
        }
        viewOrderRef.current = [key];
      }

      const requestId = (requestIdsRef.current.get(key) ?? 0) + 1;
      requestIdsRef.current.set(key, requestId);
      controllersRef.current.get(key)?.abort();
      const controller = new AbortController();
      controllersRef.current.set(key, controller);

      const pending: ArtifactTab = {
        path: key,
        name: fileNameOf(key),
        artifact: null,
        content: null,
        target: openTarget ?? null,
        loading: true,
        error: null,
      };

      let evicted: string | null = null;
      setTabs((prev) => {
        if (!multiTab) return [pending];
        const index = prev.findIndex((tab) => tab.path === key);
        if (index !== -1) {
          const next = [...prev];
          next[index] = { ...next[index], ...pending };
          return next;
        }
        const next = [...prev, pending];
        if (next.length <= MAX_OPEN_ARTIFACT_TABS) return next;
        // Over the ceiling: drop the least-recently-viewed tab that isn't the
        // one being opened.
        const victim = [...viewOrderRef.current]
          .reverse()
          .find((p) => p !== key && next.some((tab) => tab.path === p));
        if (!victim) return next;
        evicted = victim;
        return next.filter((tab) => tab.path !== victim);
      });
      if (evicted) forgetTab(evicted);

      const isStale = () => requestIdsRef.current.get(key) !== requestId;
      const patch = (fields: Partial<ArtifactTab>) => {
        if (isStale()) return;
        setTabs((prev) =>
          prev.map((tab) => (tab.path === key ? { ...tab, ...fields } : tab)),
        );
      };

      try {
        const descriptor = await filesApi.resolveOne(
          buildFileRef(location.absolutePath),
          { signal: controller.signal, baseRef: resolveBaseRef },
        );
        if (isStale()) return;
        if (!descriptor || descriptor.error || !descriptor.exists) {
          patch({ error: missingErrorMessage });
          return;
        }

        const result = await resolvedToArtifactFile(descriptor, {
          projectId,
          relPath: key,
          platform,
          signal: controller.signal,
        });
        if (isStale()) return;
        patch({
          artifact: result.artifact,
          content: result.content,
          name: result.artifact?.name ?? fileNameOf(key),
        });
      } catch (cause) {
        if (
          isStale() ||
          controller.signal.aborted ||
          (cause instanceof DOMException && cause.name === "AbortError")
        ) {
          return;
        }
        patch({
          error: cause instanceof Error ? cause.message : String(cause),
        });
      } finally {
        if (!isStale()) {
          patch({ loading: false });
          if (controllersRef.current.get(key) === controller) {
            controllersRef.current.delete(key);
          }
        }
      }
    },
    [
      forgetTab,
      locate,
      missingErrorMessage,
      multiTab,
      platform,
      projectId,
      resolveBaseRef,
      tabs,
      touchViewOrder,
    ],
  );

  const activeTab = useMemo(
    () => tabs.find((tab) => tab.path === activePath) ?? null,
    [activePath, tabs],
  );

  const reload = useCallback(async () => {
    if (activeTab) await open(activeTab.path, activeTab.target);
  }, [activeTab, open]);

  useEffect(
    () => () => {
      for (const controller of controllersRef.current.values())
        controller.abort();
      controllersRef.current.clear();
      requestIdsRef.current.clear();
    },
    [],
  );

  return {
    tabs,
    activePath,
    activate,
    closeTab,
    selectedPath: activePath,
    artifact: activeTab?.artifact ?? null,
    content: activeTab?.content ?? null,
    target: activeTab?.target ?? null,
    loading: activeTab?.loading ?? false,
    error: activeTab?.error ?? null,
    open,
    reload,
    close,
  };
}
