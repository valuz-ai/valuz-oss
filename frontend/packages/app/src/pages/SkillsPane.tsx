import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Zap } from "lucide-react";
import {
  CategorizedList,
  DeleteConfirmDialog,
  EmptyState,
  PageLoader,
  SkillCard,
  SkillDetailPanel,
  Switch,
} from "@valuz/ui";
import {
  skillsApi,
  useResourceCategories,
  useResourceGuard,
  useRegistryStore,
  useTranslation,
} from "@valuz/core";
import type {
  SkillView,
  SkillCreationContext,
  SkillDeletePreview,
  SkillImportPreviewFile,
} from "@valuz/core";
import type { ResourceCategory } from "@valuz/shared";
import { SkillAddDialog } from "@valuz/app/components";
import {
  ResourceActionSlot,
  ResourceCloudDetailSlot,
  ResourceCopyMenuItemSlot,
  ResourceDetailActionSlot,
} from "../components/ResourceActionSlot";
import { isCloudOnlyResource } from "./agent-list-state";

/** Add mode driven from the shared header dropdown (null = closed). */
export type SkillAddMode = "link" | "upload" | null;

/* ── Map backend SkillView → UI component props ─────────────── */

function abbreviateHome(p: string | null | undefined): string | undefined {
  if (!p) return undefined;
  const home = "/Users/";
  if (!p.startsWith(home)) return p;
  const rest = p.slice(home.length);
  const slash = rest.indexOf("/");
  return slash === -1 ? p : `~${rest.slice(slash)}`;
}

function skillCategoryId(
  s: SkillView,
): "builtin" | "official" | "agents" | "claude" | "codex" {
  if (s.origin_label === "Built-in") return "builtin";
  if (s.scope === "official") return "official";
  if (s.source === "valuz") return "agents";
  if (s.source === "codex") return "codex";
  return "claude";
}

function toCardSkill(s: SkillView) {
  return {
    name: s.name,
    description: s.description,
    tags: s.tags,
    source: (s.scope === "official" ? "official" : "custom") as
      "official" | "custom",
    locked: s.is_locked ?? false,
    version: s.version != null ? `v${s.version}` : "–",
    versionNumber: s.version ?? null,
    originLabel: s.origin_label ?? undefined,
    path: abbreviateHome(s.path),
    category: skillCategoryId(s),
  };
}

/** Folder birthtime DESC, name ASC tiebreaker, null-last. */
function compareByBirthtimeDesc(a: SkillView, b: SkillView): number {
  const ta = a.folder_created_at
    ? new Date(a.folder_created_at).getTime()
    : null;
  const tb = b.folder_created_at
    ? new Date(b.folder_created_at).getTime()
    : null;
  if (ta === null && tb === null) return a.name.localeCompare(b.name);
  if (ta === null) return 1;
  if (tb === null) return -1;
  if (tb !== ta) return tb - ta;
  return a.name.localeCompare(b.name);
}

function buildSkillCategories(
  t: ReturnType<typeof useTranslation>["t"],
): ResourceCategory<SkillView>[] {
  return [
    {
      id: "official",
      label: t("skill.groupOfficial" as Parameters<typeof t>[0]),
      order: 0,
      filter: (s) => s.scope === "official",
      sort: compareByBirthtimeDesc,
    },
    {
      id: "agents",
      label: t("skill.groupAgents" as Parameters<typeof t>[0]),
      order: 1,
      filter: (s) => s.source === "valuz" && s.scope !== "official",
      sort: compareByBirthtimeDesc,
    },
    {
      id: "claude",
      label: t("skill.groupClaude" as Parameters<typeof t>[0]),
      order: 2,
      filter: (s) =>
        s.source === "claude" ||
        (s.source !== "valuz" &&
          s.source !== "codex" &&
          s.scope !== "official"),
      sort: compareByBirthtimeDesc,
    },
    {
      id: "codex",
      label: t("skill.groupCodex" as Parameters<typeof t>[0]),
      order: 3,
      filter: (s) => s.source === "codex",
      sort: compareByBirthtimeDesc,
    },
  ];
}

/** Origin badge for a card given the bucket it landed in. */
function badgeForCategory(
  categoryId: string,
  skill: SkillView,
  t: ReturnType<typeof useTranslation>["t"],
):
  | { label: string; tone: "default" | "valuz" | "claude" | "codex" }
  | undefined {
  if (categoryId === "official") {
    return {
      label:
        skill.origin_label === "Built-in"
          ? t("skill.originBuiltin")
          : t("skill.official"),
      tone: "default",
    };
  }
  if (skill.source === "codex") {
    return { label: "Codex", tone: "codex" };
  }
  if (skill.source === "valuz") {
    if (skill.creation_origin === "created") {
      return { label: t("skill.originCreated"), tone: "valuz" };
    }
    return undefined;
  }
  return { label: "Claude", tone: "claude" };
}

/**
 * Skills body for the unified resource page — a self-contained
 * list (left, grouped) + detail (right) fragment. Ports SkillsPage's
 * grouped catalog, library toggle, detail actions, and the URL / upload
 * add dialog (AI-create is a header navigation). The add dropdown lives
 * in the shared header and drives {@link SkillAddMode} down through
 * `addMode` / `onAddModeChange`.
 */
export function SkillsPane({
  query,
  addMode,
  onAddModeChange,
}: {
  query: string;
  addMode: SkillAddMode;
  onAddModeChange: (mode: SkillAddMode) => void;
}) {
  const { t } = useTranslation();
  const hasCopyMenuItems = useRegistryStore(
    (state) => (state.slots["resource.skill.copy.menu-items"]?.length ?? 0) > 0,
  );
  const navigate = useNavigate();
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const [skills, setSkills] = useState<SkillView[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeSkillId, setActiveSkillId] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [, setDeletePreview] = useState<SkillDeletePreview | null>(null);
  const [activeFiles, setActiveFiles] = useState<
    { skillId: string; files: SkillImportPreviewFile[] } | undefined
  >(undefined);

  const loadSkills = useCallback(async () => {
    try {
      const res = await skillsApi.list("chat-default");
      if (mountedRef.current) setSkills(res.skills);
    } catch (err) {
      if (mountedRef.current) {
        console.error("[Skills] load error", err);
        setSkills([]);
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSkills();
  }, [loadSkills]);

  useEffect(() => {
    const refresh = (event: Event) => {
      const resourceType = (event as CustomEvent<{ resourceType?: string }>)
        .detail?.resourceType;
      if (resourceType === "skill") void loadSkills();
    };
    window.addEventListener("valuz:resource-refresh", refresh);
    return () => window.removeEventListener("valuz:resource-refresh", refresh);
  }, [loadSkills]);

  const skillCreatorDraftUrl = useCallback((context: SkillCreationContext) => {
    const params = new URLSearchParams({ mode: "skill-creator" });
    params.set("skill_kind", context.kind);
    if (context.kind === "project" && context.project_id) {
      params.set("skill_project", context.project_id);
    }
    return `/conversation/new?${params.toString()}`;
  }, []);

  // Global library on/off for a skill. Optimistic flip + revert.
  const handleToggleLibrary = useCallback(
    async (skill: SkillView, enabled: boolean) => {
      setSkills((prev) =>
        prev.map((s) =>
          s.id === skill.id ? { ...s, library_enabled: enabled } : s,
        ),
      );
      try {
        await skillsApi.setLibraryState(skill.id, enabled);
      } catch (err) {
        console.error("[Skills] library toggle error", err);
        setSkills((prev) =>
          prev.map((s) =>
            s.id === skill.id ? { ...s, library_enabled: !enabled } : s,
          ),
        );
      }
    },
    [],
  );

  const categories = useResourceCategories<SkillView>(
    "skill",
    buildSkillCategories(t),
  );

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q),
    );
  }, [skills, query]);

  // Default selection follows the group display order, matching the list.
  const firstVisibleSkill = useMemo(() => {
    const assigned = new Set<string>();
    for (const cat of categories) {
      const matching = filtered
        .filter(
          (s) =>
            !isCloudOnlyResource(s) && !assigned.has(s.id) && cat.filter(s),
        )
        .sort(cat.sort ?? (() => 0));
      if (matching.length > 0) return matching[0];
      for (const s of filtered) if (cat.filter(s)) assigned.add(s.id);
    }
    return filtered.find((skill) => !isCloudOnlyResource(skill)) ?? null;
  }, [filtered, categories]);

  const currentSkill = useMemo(
    () =>
      skills.find(
        (s) =>
          (s.id === activeSkillId ||
            (!!activeSkillId && !!s.slug && s.slug === activeSkillId)),
      ) ?? firstVisibleSkill,
    [skills, activeSkillId, firstVisibleSkill],
  );
  const effectiveActiveId = currentSkill?.id ?? null;

  const currentCardSkill = useMemo(
    () => (currentSkill ? toCardSkill(currentSkill) : null),
    [currentSkill],
  );
  const currentSkillCloudOnly = isCloudOnlyResource(currentSkill);

  // Load the file tree for the selected skill (skeleton while loading).
  useEffect(() => {
    const id = currentSkill?.id;
    if (!id) return;
    if (currentSkillCloudOnly) {
      setActiveFiles({ skillId: id, files: [] });
      return;
    }
    setActiveFiles(undefined);
    let cancelled = false;
    void (async () => {
      try {
        const files = await skillsApi.listFiles(id);
        if (!cancelled && mountedRef.current)
          setActiveFiles({ skillId: id, files });
      } catch {
        if (!cancelled && mountedRef.current)
          setActiveFiles({ skillId: id, files: [] });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [currentSkill?.id, currentSkillCloudOnly]);

  const activeFilesForCurrentSkill =
    activeFiles && currentSkill && activeFiles.skillId === currentSkill.id
      ? activeFiles.files
      : undefined;

  const { canDelete: canDeleteSkill } = useResourceGuard({
    source: currentSkill?.source,
    readonly: currentSkill?.readonly,
    deletable: currentSkill?.deletable,
  });

  const handleCopy = async () => {
    if (!currentSkill) return;
    try {
      const copied = await skillsApi.copy(currentSkill.id, {
        new_name: `${currentSkill.name} (copy)`,
      });
      toast.success(
        `「${copied.name}」${t("skill.copiedAsCustom" as Parameters<typeof t>[0])}`,
      );
      await loadSkills();
    } catch (err) {
      toast.error(
        t("common.saveFailed" as Parameters<typeof t>[0], {
          error: err instanceof Error ? err.message : "unknown",
        }),
      );
    }
  };

  const handleDeleteOpen = async () => {
    if (!currentSkill) return;
    try {
      const preview = await skillsApi.deleteDryRun(currentSkill.id);
      setDeletePreview(preview);
      setDeleteOpen(true);
    } catch {
      toast.error(t("skill.operationFailed" as Parameters<typeof t>[0]));
    }
  };

  const handleDelete = async () => {
    if (!currentSkill) return;
    try {
      await skillsApi.deleteConfirm(currentSkill.id);
      toast.success(t("common.deleted" as Parameters<typeof t>[0]));
      setDeleteOpen(false);
      setDeletePreview(null);
      setActiveSkillId(null);
      await loadSkills();
    } catch (err) {
      toast.error(
        t("common.deleteFailed" as Parameters<typeof t>[0], {
          error: err instanceof Error ? err.message : "unknown",
        }),
      );
    }
  };

  return (
    <>
      <div className="w-[345px] shrink-0 overflow-y-auto border-r border-surface-border">
        {loading ? (
          <PageLoader logo className="py-16" />
        ) : (
          <div className="px-4 pt-6 pb-2">
            <CategorizedList
              items={filtered}
              categories={categories}
              selectedId={effectiveActiveId}
              getId={(s: SkillView) => s.id}
              onSelect={(s: SkillView) => setActiveSkillId(s.id)}
              renderItem={(
                skill: SkillView,
                isSelected: boolean,
                category: ResourceCategory<SkillView>,
              ) => {
                const cloudOnly = isCloudOnlyResource(skill);
                const categoryId = category.id;
                const organizationSync = (
                  skill as unknown as Record<string, unknown>
                )._org_sync;
                const actionResource =
                  category.groupBy && organizationSync
                    ? ({
                        ...(skill as unknown as Record<string, unknown>),
                        _sync: organizationSync,
                      } as Record<string, unknown>)
                    : (skill as unknown as Record<string, unknown>);
                return (
                  <SkillCard
                    skill={toCardSkill(skill)}
                    originBadge={badgeForCategory(categoryId, skill, t)}
                    active={isSelected}
                    onClick={() => setActiveSkillId(skill.id)}
                    actions={
                      <div
                        className="flex items-center gap-2"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {!cloudOnly ? (
                          <Switch
                            size="sm"
                            checked={skill.library_enabled !== false}
                            onCheckedChange={(v) =>
                              void handleToggleLibrary(skill, v)
                            }
                            aria-label={t(
                              (skill.library_enabled !== false
                                ? "skill.libraryEnabledTip"
                                : "skill.libraryDisabledTip") as Parameters<
                                typeof t
                              >[0],
                            )}
                          />
                        ) : null}
                        <ResourceActionSlot
                          resourceType="skill"
                          resource={actionResource}
                        />
                      </div>
                    }
                  />
                );
              }}
              emptyState={
                <EmptyState
                  className="py-16"
                  icon={<Zap />}
                  title={
                    skills.length === 0
                      ? t("skill.emptyTitle" as Parameters<typeof t>[0])
                      : t("skill.noMatch" as Parameters<typeof t>[0])
                  }
                  message={
                    skills.length === 0
                      ? t("skill.emptyDesc" as Parameters<typeof t>[0])
                      : undefined
                  }
                />
              }
            />
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1">
        {currentSkill && currentSkillCloudOnly ? (
          <ResourceCloudDetailSlot
            resourceType="skill"
            resource={currentSkill as unknown as Record<string, unknown>}
          />
        ) : currentSkill && currentCardSkill ? (
          <SkillDetailPanel
            key={currentSkill.id}
            skill={currentCardSkill}
            files={activeFilesForCurrentSkill}
            onLoadFile={async (path) => {
              const res = await skillsApi.getFileContent(currentSkill.id, path);
              return res.content;
            }}
            onOpenInFinder={
              currentSkill.path
                ? () => {
                    const bridge = (
                      window as Window & {
                        valuzDesktop?: {
                          invoke: <T>(ch: string, args?: unknown) => Promise<T>;
                        };
                      }
                    ).valuzDesktop;
                    void bridge?.invoke("open_in_finder", {
                      path: currentSkill.path,
                    });
                  }
                : undefined
            }
            onDelete={canDeleteSkill ? handleDeleteOpen : undefined}
            onCopy={handleCopy}
            copyMenuItems={
              hasCopyMenuItems ? (
                <ResourceCopyMenuItemSlot
                  resourceType="skill"
                  resource={currentSkill as unknown as Record<string, unknown>}
                />
              ) : undefined
            }
            headerActions={
              <ResourceDetailActionSlot
                resourceType="skill"
                resource={currentSkill as unknown as Record<string, unknown>}
              />
            }
          />
        ) : (
          <div className="flex justify-center pt-24">
            <EmptyState icon={<Zap />} message={t("resource.emptyDetail")} />
          </div>
        )}
      </div>

      <SkillAddDialog
        open={addMode !== null}
        mode={addMode ?? "link"}
        onOpenChange={(open) => {
          if (!open) onAddModeChange(null);
        }}
        onComplete={() => void loadSkills()}
        onArchivePreview={(file) => skillsApi.importArchivePreview(file)}
        onArchiveConfirm={(data) => skillsApi.importArchiveConfirm(data)}
        onStartAiCreate={(context) => navigate(skillCreatorDraftUrl(context))}
        onLinkPreview={(url) => skillsApi.importUrlPreview(url)}
        onLinkConfirm={(data) => skillsApi.importUrlConfirm(data)}
      />

      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteOpen(false);
            setDeletePreview(null);
          }
        }}
        itemName={currentSkill?.name}
        onConfirm={() => void handleDelete()}
      />
    </>
  );
}
