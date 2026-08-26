/**
 * Cursor-paginated activity feed (``GET /v1/activity``) backing every history
 * list: the project-home tabs (``projectId`` set) and the global 动态 list
 * (``projectId`` omitted). "Head-poll + tail-paginate": a 4s poll refreshes the
 * first page in place while ``loadMore`` appends older pages via the keyset
 * cursor. See backend ``modules/activity``.
 *
 * Multi-target editions (registered execution targets): the GLOBAL feed fans
 * out to every target, tags each row's ``exec_origin`` with the answering
 * target, feeds the origin index, and keeps an independent keyset cursor per
 * target for ``loadMore``. A project-scoped feed lives entirely on the
 * project's backend and routes there via the entity resolver instead of
 * fanning out. Zero targets (OSS) keeps the single-backend path unchanged.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  activityApi,
  type ActivityItem,
  type ActivityTab,
} from "../api/activity-api";
import { resolveApiBase } from "../api/base-resolver";
import { getEntityOrigin, recordEntityOrigins } from "../edition/entity-origin";
import { fanOutTargets, getListFanOutTargets } from "../edition/list-fanout";
import { resolveProjectActivity } from "../edition/project-activity";

export interface ActivityFeed {
  items: ActivityItem[];
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  loadMore: () => void;
  refresh: () => void;
}

/** Per-target keyset cursors; the plain string form is the single-backend
 * cursor, the record form is one cursor per answering target. */
type CursorState =
  | { kind: "single"; cursor: string | null }
  | { kind: "multi"; cursors: Record<string, string | null> };

function hasAnyCursor(state: CursorState | null): boolean {
  if (!state) return false;
  if (state.kind === "single") return state.cursor !== null;
  return Object.values(state.cursors).some((cursor) => cursor !== null);
}

function tagAndRecord(items: ActivityItem[], targetId: string): ActivityItem[] {
  recordEntityOrigins(items.map((item) => [item.id, targetId]));
  return items.map((item) => ({ ...item, exec_origin: targetId }));
}

export function useActivityFeed(opts: {
  projectId?: string | null;
  tab: ActivityTab;
  pageSize?: number;
  pollMs?: number;
  enabled?: boolean;
}): ActivityFeed {
  const {
    projectId = null,
    tab,
    pageSize = 20,
    pollMs = 4000,
    enabled = true,
  } = opts;

  const [items, setItems] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const cursorRef = useRef<CursorState | null>(null);
  // Bumped on every project/tab switch so late responses from the old scope are
  // dropped instead of clobbering the new list.
  const genRef = useRef(0);

  /** First page across whichever backends serve this feed. */
  const fetchHead = useCallback(async (): Promise<{
    items: ActivityItem[];
    cursor: CursorState;
  }> => {
    const params = { projectId, tab, limit: pageSize };
    // An edition-provided source may own this project's feed entirely (its
    // backend cannot answer /v1/activity — see edition/project-activity).
    const override = projectId
      ? resolveProjectActivity({
          projectId,
          tab,
          limit: pageSize,
          cursor: null,
        })
      : null;
    if (override) {
      const page = await override;
      return {
        items: page.items,
        cursor: { kind: "single", cursor: page.next_cursor },
      };
    }
    // A project feed lives on the project's backend (single source).
    const fanTargets = projectId ? [] : getListFanOutTargets();
    if (fanTargets.length === 0) {
      const page = await activityApi.list(
        params,
        projectId
          ? { baseUrl: resolveApiBase({ projectId }, "") || undefined }
          : undefined,
      );
      const projectOrigin = projectId
        ? getEntityOrigin(projectId, "project")
        : undefined;
      return {
        // A project-scoped feed routes to one backend instead of fanning out,
        // but its PlaybookRun rows still need an origin before a click leaves
        // the project page for the global Run detail route.
        items: projectOrigin
          ? tagAndRecord(page.items, projectOrigin)
          : page.items,
        cursor: { kind: "single", cursor: page.next_cursor },
      };
    }
    const outcome = await fanOutTargets((target, signal) =>
      activityApi.list(params, { baseUrl: target.baseUrl, signal }),
    );
    const merged: ActivityItem[] = [];
    const seen = new Set<string>();
    const cursors: Record<string, string | null> = {};
    for (const { target, value } of outcome.values) {
      cursors[target.id] = value.next_cursor;
      for (const item of tagAndRecord(value.items, target.id)) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        merged.push(item);
      }
    }
    merged.sort((a, b) => b.sort_at - a.sort_at);
    return { items: merged, cursor: { kind: "multi", cursors } };
  }, [projectId, tab, pageSize]);

  const loadFirst = useCallback(async () => {
    const gen = ++genRef.current;
    setLoading(true);
    try {
      const head = await fetchHead();
      if (gen !== genRef.current) return;
      setItems(head.items);
      cursorRef.current = head.cursor;
      setHasMore(hasAnyCursor(head.cursor));
    } catch {
      if (gen !== genRef.current) return;
      setItems([]);
      cursorRef.current = null;
      setHasMore(false);
    } finally {
      if (gen === genRef.current) setLoading(false);
    }
  }, [fetchHead]);

  useEffect(() => {
    if (!enabled) return;
    void loadFirst();
  }, [enabled, loadFirst]);

  const loadMore = useCallback(() => {
    const state = cursorRef.current;
    if (!hasAnyCursor(state) || loadingMore) return;
    const gen = genRef.current;
    setLoadingMore(true);

    const fetchOlder = async (): Promise<{
      older: ActivityItem[];
      next: CursorState;
    }> => {
      if (!state) throw new Error("unreachable");
      if (state.kind === "single") {
        const override = projectId
          ? resolveProjectActivity({
              projectId,
              tab,
              limit: pageSize,
              cursor: state.cursor,
            })
          : null;
        if (override) {
          const page = await override;
          return {
            older: page.items,
            next: { kind: "single", cursor: page.next_cursor },
          };
        }
        const page = await activityApi.list(
          { projectId, tab, limit: pageSize, cursor: state.cursor },
          projectId
            ? { baseUrl: resolveApiBase({ projectId }, "") || undefined }
            : undefined,
        );
        const projectOrigin = projectId
          ? getEntityOrigin(projectId, "project")
          : undefined;
        return {
          older: projectOrigin
            ? tagAndRecord(page.items, projectOrigin)
            : page.items,
          next: { kind: "single", cursor: page.next_cursor },
        };
      }
      // Multi-target: page each target that still has a cursor; targets
      // without one are exhausted and keep their null.
      const pending = getListFanOutTargets().filter(
        (target) => state.cursors[target.id],
      );
      const settled = await Promise.allSettled(
        pending.map((target) =>
          activityApi
            .list(
              {
                projectId,
                tab,
                limit: pageSize,
                cursor: state.cursors[target.id],
              },
              { baseUrl: target.baseUrl },
            )
            .then((page) => ({ target, page })),
        ),
      );
      const older: ActivityItem[] = [];
      const cursors = { ...state.cursors };
      for (const result of settled) {
        if (result.status !== "fulfilled") continue;
        const { target, page } = result.value;
        cursors[target.id] = page.next_cursor;
        older.push(...tagAndRecord(page.items, target.id));
      }
      older.sort((a, b) => b.sort_at - a.sort_at);
      return { older, next: { kind: "multi", cursors } };
    };

    fetchOlder()
      .then(({ older, next }) => {
        if (gen !== genRef.current) return;
        setItems((prev) => {
          const seen = new Set(prev.map((i) => i.id));
          return [...prev, ...older.filter((i) => !seen.has(i.id))];
        });
        cursorRef.current = next;
        setHasMore(hasAnyCursor(next));
      })
      .catch(() => {
        /* keep the current list; the next loadMore retries */
      })
      .finally(() => {
        if (gen === genRef.current) setLoadingMore(false);
      });
  }, [projectId, tab, pageSize, loadingMore]);

  // Head-poll: pull the newest page and merge it over the loaded list — updates
  // in place, prepends new items, and keeps everything paged in below.
  useEffect(() => {
    if (!enabled || pollMs <= 0) return;
    const handle = window.setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      const gen = genRef.current;
      fetchHead()
        .then((head) => {
          if (gen !== genRef.current) return;
          setItems((prev) => {
            const freshIds = new Set(head.items.map((i) => i.id));
            const tail = prev.filter((i) => !freshIds.has(i.id));
            return [...head.items, ...tail];
          });
        })
        .catch(() => {
          /* transient; the next tick retries */
        });
    }, pollMs);
    return () => window.clearInterval(handle);
  }, [enabled, pollMs, fetchHead]);

  return { items, loading, loadingMore, hasMore, loadMore, refresh: loadFirst };
}
