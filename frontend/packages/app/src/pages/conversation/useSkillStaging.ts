import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  skillsApi,
  useTranslation,
  type StagingSlugView,
  type StagingSyncStrategy,
} from "@valuz/core";
import { NEW_SESSION_ID } from "./session-events";

type SkillStagingParams = {
  /** Route param (``/conversation/{id}``), defaulted to ``NEW_SESSION_ID``. */
  id: string;
  isSkillCreatorMode: boolean;
};

/**
 * ── Skill-creator staging panel ──────────────────────────────────────
 *
 * Owns the staging cluster of the conversation page: the staged-slug
 * state, the ``refreshStaging`` scan, the ``handleSyncStaging`` writer,
 * and the 3s poll effect that keeps the panel fresh while Skill-Creator
 * mode is active. Bodies are moved verbatim from ``ConversationPage``.
 */
export function useSkillStaging({ id, isSkillCreatorMode }: SkillStagingParams) {
  const { t } = useTranslation();

  // Staging panel (Skill Creator mode) ─────────────────────────────────
  const [stagingSlugs, setStagingSlugs] = useState<StagingSlugView[]>([]);
  const [stagingRefreshing, setStagingRefreshing] = useState(false);
  const [stagingSyncing, setStagingSyncing] = useState(false);

  const refreshStaging = useCallback(async () => {
    if (!isSkillCreatorMode) return;
    // Draft-first entry: no session exists yet — nothing staged to scan.
    if (id === NEW_SESSION_ID) return;
    setStagingRefreshing(true);
    try {
      const res = await skillsApi.scanStaging(id);
      setStagingSlugs(res.slugs);
    } catch {
      // Silent — most likely the session hasn't produced staging yet.
    } finally {
      setStagingRefreshing(false);
    }
  }, [id, isSkillCreatorMode]);

  const handleSyncStaging = useCallback(
    async (
      items: {
        slug: string;
        strategy: StagingSyncStrategy;
        newSlug?: string;
      }[],
    ) => {
      setStagingSyncing(true);
      try {
        const res = await skillsApi.syncStaging(id, {
          items: items.map((i) => ({
            slug: i.slug,
            strategy: i.strategy,
            new_slug: i.newSlug,
          })),
        });
        const written = res.results.filter((r) => !r.skipped).length;
        toast.success(
          t("skill.syncCount" as Parameters<typeof t>[0], {
            count: String(written),
          }),
        );
        await refreshStaging();
      } catch (err) {
        toast.error(
          t("common.saveFailed" as Parameters<typeof t>[0], {
            error: err instanceof Error ? err.message : "unknown",
          }),
        );
      } finally {
        setStagingSyncing(false);
      }
    },
    [id, refreshStaging],
  );

  useEffect(() => {
    if (!isSkillCreatorMode) return;
    void refreshStaging();
    const t = window.setInterval(() => {
      void refreshStaging();
    }, 3000);
    return () => window.clearInterval(t);
  }, [isSkillCreatorMode, refreshStaging]);

  return {
    stagingSlugs,
    stagingRefreshing,
    stagingSyncing,
    refreshStaging,
    handleSyncStaging,
  };
}
