import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  FileText,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Store,
  Upload,
  Zap,
} from "lucide-react";
import {
  CategorizedList,
  Button,
  DeleteConfirmDialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  EmptyState,
  PageLoader,
  SkillCard,
  SkillDetailPanel,
  Switch,
} from "@valuz/ui";
import {
  ResourceActionSlot,
  ResourceCopyMenuItemSlot,
  ResourceDetailActionSlot,
} from "../components/ResourceActionSlot";
import {
  skillsApi,
  usePanelStore,
  useResourceCategories,
  useResourceGuard,
  useRegistryStore,
} from "@valuz/core";
import type {
  SkillView,
  SkillCreationContext,
  SkillDeletePreview,
  SkillImportPreviewFile,
} from "@valuz/core";
import type { ResourceCategory } from "@valuz/shared";
import { useProjectOutlet } from "@valuz/app/layout";
import { SkillAddDialog, SkillEditDialog } from "@valuz/app/components";
import { useTranslation } from "@valuz/core";
import { isCloudOnlyResource } from "./agent-list-state";
import { usePluginMemberships } from "../components/plugins/use-plugin-memberships";

type AddSkillDialogMode = "link" | "upload";
type ResourceRefreshEvent = CustomEvent<{ resourceType?: string }>;

const RESOURCE_REFRESH_EVENT = "valuz:resource-refresh";

/* ── Map backend SkillView → UI component props ─────────────── */

function abbreviateHome(p: string | null | undefined): string | undefined {
  if (!p) return undefined;
  const home = "/Users/";
  if (!p.startsWith(home)) return p;
  const rest = p.slice(home.length);
  const slash = rest.indexOf("/");
  return slash === -1 ? p : `~${rest.slice(slash)}`;
}

/**
 * Map a skill's source/scope fields to a category ID matching the
 * SKILL_CATEGORIES filter predicates below. Used by ``toCardSkill``
 * to populate the card's ``category`` field for the detail panel
 * subtitle.
 */
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
  const category = skillCategoryId(s);
  return {
    name: s.name,
    description: s.description,
    tags: s.tags,
    source: (s.scope === "official" ? "official" : "custom") as
      "official" | "custom",
    locked: s.is_locked ?? false,
    protected: s.protected ?? false,
    version: s.version != null ? `v${s.version}` : "–",
    versionNumber: s.version ?? null,
    originLabel: s.origin_label ?? undefined,
    path: abbreviateHome(s.path),
    category,
  };
}

/**
 * Built-in skill categories — data-driven replacements for the old
 * hardcoded bucket rendering. Each category carries its own filter
 * predicate and the shared birthtime-DESC sort. Injected categories
 * (plugins / enterprise) are merged in at runtime via
 * ``useResourceCategories``.
 */
function buildSkillCategories(
  t: ReturnType<typeof useTranslation>["t"],
): ResourceCategory<SkillView>[] {
  return [
    {
      id: "official",
      label: t("skill.groupOfficial" as Parameters<typeof t>[0]),
      order: 0,
      filter: (s: SkillView) => s.scope === "official",
      sort: compareByBirthtimeDesc,
    },
    {
      id: "agents",
      label: t("skill.groupAgents" as Parameters<typeof t>[0]),
      order: 1,
      filter: (s: SkillView) => s.source === "valuz" && s.scope !== "official",
      sort: compareByBirthtimeDesc,
    },
    {
      id: "claude",
      label: t("skill.groupClaude" as Parameters<typeof t>[0]),
      order: 2,
      filter: (s: SkillView) =>
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
      filter: (s: SkillView) => s.source === "codex",
      sort: compareByBirthtimeDesc,
    },
  ];
}

/**
 * Sort comparator for a single bucket. Folder birthtime DESC (newest
 * first), name ASC as the tiebreaker. ``null`` birthtime sorts last so
 * legacy / unreadable rows don't push freshly-created skills down.
 *
 * Mirrors the backend ``SkillLibraryService.list_catalog`` sort key —
 * we resort frontend-side because the API returns a flat dedup'd list
 * and the per-bucket slice may reshuffle the ordering relative to the
 * global sort.
 */
function compareByBirthtimeDesc(a: SkillView, b: SkillView): number {
  const ta = a.folder_created_at
    ? new Date(a.folder_created_at).getTime()
    : null;
  const tb = b.folder_created_at
    ? new Date(b.folder_created_at).getTime()
    : null;
  // Both null → fall through to name ASC.
  // One null → null sorts after non-null.
  if (ta === null && tb === null) return a.name.localeCompare(b.name);
  if (ta === null) return 1;
  if (tb === null) return -1;
  if (tb !== ta) return tb - ta;
  return a.name.localeCompare(b.name);
}

/** Pick the right origin badge for a card given which category it
 *  landed in. Returns ``undefined`` when no badge should render (a
 *  skill that was merely scanned into ~/.agents/skills/, not
 *  Valuz-originated). Accepts the category ID string from
 *  CategorizedList (or the "_other" fallback category). */
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
  if (categoryId === "agents") {
    if (skill.creation_origin === "created") {
      return { label: t("skill.originCreated"), tone: "valuz" };
    }
    return undefined;
  }
  if (categoryId === "codex") return { label: "Codex", tone: "codex" };
  // categoryId === "claude" or "_other"
  return { label: "Claude", tone: "claude" };
}

export const SkillsPage = () => {
  const { t } = useTranslation();
  const hasCopyMenuItems = useRegistryStore(
    (state) => (state.slots["resource.skill.copy.menu-items"]?.length ?? 0) > 0,
  );
  const navigate = useNavigate();
  const {
    setHeader,
    setHideHeader,
    setRightPanel,
    setAsideClassName,
    setMainClassName,
  } = useProjectOutlet();
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);
  const [searchParams] = useSearchParams();
  const [skills, setSkills] = useState<SkillView[]>([]);
  const [loading, setLoading] = useState(true);
  const [rescanning, setRescanning] = useState(false);
  // Honor a ``?skill=<id>`` deep link (e.g. from an agent's 装备 list) as the
  // initial selection; falls back to the first skill once the list loads if the
  // id isn't found. Lazy initializer so there's no setState-in-effect.
  const [activeSkillId, setActiveSkillId] = useState<string | null>(() =>
    searchParams.get("skill"),
  );
  // The ``?skill=`` deep link only positions the list once: scroll the
  // pre-selected card into view on first paint, then leave the scroll alone so
  // ordinary clicks don't yank the viewport around.
  const skillParam = searchParams.get("skill");
  const scrolledToParamRef = useRef(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);

  // Create / Edit dialogs
  const [addOpen, setAddOpen] = useState(false);
  const [addMode, setAddMode] = useState<AddSkillDialogMode>("link");
  const [editOpen, setEditOpen] = useState(false);

  // Delete
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [, setDeletePreview] = useState<SkillDeletePreview | null>(null);

  /* ── Data loading ──────────────────────────────────────────── */

  const mountedRef = useRef(true);
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

  // Manual rescan: re-index the skill library on disk, then reload the list
  // (the focus revalidation below also picks up out-of-band index changes).
  const handleRescan = useCallback(async () => {
    if (rescanning) return;
    setRescanning(true);
    try {
      const res = await skillsApi.rescan();
      await loadSkills();
      toast.success(
        t("skill.rescanDone" as Parameters<typeof t>[0], {
          count: res.indexed,
        }),
      );
    } catch (err) {
      console.error("[Skills] rescan error", err);
      toast.error(t("skill.rescanFailed" as Parameters<typeof t>[0]));
    } finally {
      if (mountedRef.current) setRescanning(false);
    }
  }, [rescanning, t, loadSkills]);

  // Global library on/off for a skill (slug-keyed on the backend). Optimistic:
  // flip local state immediately, revert if the request fails. Off hides the
  // skill from new (non-project) conversations' ``/`` picker.
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

  // Draft-first (no pre-created session): land on the same draft page as
  // 新对话 so the composer's default-agent pick + agent switching work; the
  // session is minted with the skill-creator context on the first send.
  const skillCreatorDraftUrl = useCallback((context: SkillCreationContext) => {
    const params = new URLSearchParams({ mode: "skill-creator" });
    params.set("skill_kind", context.kind);
    if (context.kind === "project" && context.project_id) {
      params.set("skill_project", context.project_id);
    }
    return `/conversation/new?${params.toString()}`;
  }, []);

  const handleStartAiCreate = useCallback(() => {
    navigate(skillCreatorDraftUrl({ kind: "skills_library" }));
  }, [navigate, skillCreatorDraftUrl]);

  const openAddDialog = (mode: AddSkillDialogMode) => {
    setAddMode(mode);
    setAddOpen(true);
  };

  useEffect(() => {
    mountedRef.current = true;
    loadSkills();
    return () => {
      mountedRef.current = false;
    };
  }, [loadSkills]);

  // Render header inline (see JSX below) so the scroll container can run
  // edge-to-edge of the main card and its scrollbar sits flush against the
  // bordered card edge. Layout-level header is hidden via setHideHeader.
  //
  // Skills page inverts the usual main-vs-aside proportions: the main
  // column is a fixed-width 345 px skill *list*, and the aside takes
  // every remaining pixel to show the *detail* (file tree + content
  // preview — the meaty surface). ``main`` overrides the layout
  // default ``flex-1`` with ``w-[345px] flex-none``; ``aside`` flips
  // its default fixed ``w-[345px]`` to ``flex-1 w-auto`` so the two
  // proportions are mirrored.
  useEffect(() => {
    setHideHeader(true);
    setMainClassName("w-[345px] flex-none");
    setAsideClassName("flex-1 w-auto");
    return () => {
      setHideHeader(false);
      setHeader(null);
      setMainClassName(undefined);
      setAsideClassName(undefined);
    };
  }, [setHideHeader, setHeader, setMainClassName, setAsideClassName]);

  // Skills lib defaults the right preview panel to expanded (the
  // panel *is* the page's main payload — the left column is a
  // narrow list). Layout's global default is collapsed to fit chat,
  // so each non-chat page that wants a different default sets it
  // once on mount. Don't depend on ``setRightPanelCollapsed`` here:
  // we want this to fire exactly once per mount, not snap back when
  // the user manually toggles.
  const didInitRightPanel = useRef(false);
  useEffect(() => {
    if (didInitRightPanel.current) return;
    didInitRightPanel.current = true;
    panelSetCollapsed(false);
  }, [panelSetCollapsed]);

  // Revalidate the catalog when the user returns to the window/tab. Skills can
  // change out-of-band (periodic auto-scan re-index, the file-watcher re-index
  // after an external SKILL.md edit, or an agent authoring a skill in another
  // session), so we refetch on focus rather than holding an SSE push channel
  // open — mount + on-focus covers every realistic staleness window.
  useEffect(() => {
    const revalidate = () => {
      if (document.visibilityState === "visible") void loadSkills();
    };
    const refreshResource = (event: Event) => {
      const detail = (event as ResourceRefreshEvent).detail;
      if (detail?.resourceType === "skill") void loadSkills();
    };
    window.addEventListener("focus", revalidate);
    window.addEventListener(RESOURCE_REFRESH_EVENT, refreshResource);
    document.addEventListener("visibilitychange", revalidate);
    return () => {
      window.removeEventListener("focus", revalidate);
      window.removeEventListener(RESOURCE_REFRESH_EVENT, refreshResource);
      document.removeEventListener("visibilitychange", revalidate);
    };
  }, [loadSkills]);

  /* ── Derived state ─────────────────────────────────────────── */

  // Data-driven categories merged with any injected ones. The sort
  // comparator is attached per-category so CategorizedList handles
  // per-bucket ordering internally (birthtime DESC, name ASC).
  const categories = useResourceCategories<SkillView>(
    "skill",
    buildSkillCategories(t),
  );

  // Search-filtered list — categories + CategorizedList handle the
  // per-bucket partitioning and sorting.
  const filteredSkills = useMemo(
    () =>
      skills.filter(
        (s) =>
          s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          s.description.toLowerCase().includes(searchQuery.toLowerCase()),
      ),
    [skills, searchQuery],
  );

  // First skill across all categories in display order. The categories
  // list is already sorted by ``order`` and CategorizedList partitions
  // items by the filter predicates — we replicate the same logic here
  // to determine the default selection for the right preview panel.
  const firstVisibleSkill = useMemo(() => {
    const assigned = new Set<string>();
    for (const cat of categories) {
      const matching = filteredSkills
        .filter(
          (s) =>
            !isCloudOnlyResource(s) && !assigned.has(s.id) && cat.filter(s),
        )
        .sort(cat.sort);
      if (matching.length > 0) return matching[0];
      for (const s of filteredSkills) {
        if (cat.filter(s)) assigned.add(s.id);
      }
    }
    return filteredSkills.find((skill) => !isCloudOnlyResource(skill)) ?? null;
  }, [filteredSkills, categories]);
  const currentSkill =
    skills.find(
      (s) =>
        (s.id === activeSkillId ||
          (!!activeSkillId && !!s.slug && s.slug === activeSkillId)) &&
        !isCloudOnlyResource(s),
    ) ?? firstVisibleSkill;
  const effectiveActiveId = currentSkill?.id ?? null;

  // Plugin ownership badges (D6): one batched lookup per catalog load.
  const skillSlugs = useMemo(
    () => skills.map((s) => s.slug ?? s.name),
    [skills],
  );
  const pluginBadgeFor = usePluginMemberships("skill", skillSlugs);

  const { canDelete: canDeleteSkill } = useResourceGuard({
    source: currentSkill?.source,
    readonly: currentSkill?.readonly,
    deletable: currentSkill?.deletable,
  });

  // File tree for the active skill (drives the right detail panel preview).
  // `undefined` while loading, then populated from /v1/skills/{id}/files.
  // Pass the backend's nested tree through verbatim — the panel renders
  // directories + files recursively with depth-based indentation.
  const [activeFiles, setActiveFiles] = useState<
    | {
        skillId: string;
        files: SkillImportPreviewFile[];
      }
    | undefined
  >(undefined);

  useEffect(() => {
    if (!currentSkill) {
      setActiveFiles(undefined);
      return;
    }
    let cancelled = false;
    setActiveFiles(undefined);
    skillsApi
      .listFiles(currentSkill.id)
      .then((res) => {
        if (cancelled) return;
        setActiveFiles({ skillId: currentSkill.id, files: res });
      })
      .catch(() => {
        if (!cancelled) {
          setActiveFiles({ skillId: currentSkill.id, files: [] });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentSkill?.id]);

  const activeFilesForCurrentSkill =
    activeFiles && currentSkill && activeFiles.skillId === currentSkill.id
      ? activeFiles.files
      : undefined;

  /* ── Handlers ──────────────────────────────────────────────── */

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

  /* ── Render ────────────────────────────────────────────────── */

  // Memoised on the skill so the right-panel effect's dep array stays
  // stable across re-renders. Without ``useMemo`` ``toCardSkill`` returns
  // a fresh object each render → effect re-runs → ``setRightPanel`` →
  // project re-renders → effect re-runs → infinite loop.
  const currentCardSkill = useMemo(
    () => (currentSkill ? toCardSkill(currentSkill) : null),
    [currentSkill],
  );

  // Hand the SkillDetailPanel off to the project's right panel slot
  // instead of rendering it inline. The page-level grid now has a
  // single column and the cards can fill the full main width; the
  // panel sits in the project aside the same way the conversation
  // and project-detail pages do. Cleared on unmount so other routes
  // don't inherit a stale skill panel.
  useEffect(() => {
    if (!currentCardSkill || !currentSkill) {
      setRightPanel(null);
      return;
    }
    setRightPanel(
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
      />,
    );
    return () => {
      setRightPanel(null);
    };
  }, [
    currentSkill,
    currentCardSkill,
    activeFilesForCurrentSkill,
    hasCopyMenuItems,
    navigate,
    setRightPanel,
  ]);

  return (
    <div className="flex h-full flex-col">
      {/* Page header — title left, search + add icons right.
          Search is collapsed by default (icon only); clicking the
          magnifier expands an inline input next to it, and Esc / blur-
          while-empty collapses it back. Add icon opens the create
          dialog directly. */}
      <header className="flex shrink-0 items-center justify-between gap-4 h-15 px-5">
        <div className="flex min-w-0 flex-col justify-center">
          <span className="text-base font-semibold leading-5 text-ink-heading">
            {t("sidebar.skills" as Parameters<typeof t>[0])}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-brand transition-colors hover:bg-brand-light/60 hover:text-brand"
            onClick={() => navigate("/marketplace?tab=skills&from=skills")}
          >
            <Store className="h-3.5 w-3.5" />
            {t("marketplace.title" as Parameters<typeof t>[0])}
          </button>
          {searchOpen ? (
            <input
              type="text"
              autoFocus
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onBlur={() => {
                if (!searchQuery) setSearchOpen(false);
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setSearchQuery("");
                  setSearchOpen(false);
                }
              }}
              placeholder={t(
                "skill.searchPlaceholder" as Parameters<typeof t>[0],
              )}
              className="h-7 w-full min-w-0 max-w-[200px] rounded-none border-0 border-b border-brand bg-transparent px-1 text-xs text-ink-heading placeholder:text-ink-meta outline-none"
            />
          ) : null}
          <button
            type="button"
            aria-label={t("common.search" as Parameters<typeof t>[0])}
            onClick={() => setSearchOpen((o) => !o)}
            className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
          >
            <Search className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label={t("skill.rescan" as Parameters<typeof t>[0])}
            title={t("skill.rescan" as Parameters<typeof t>[0])}
            onClick={() => void handleRescan()}
            disabled={rescanning}
            className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body disabled:pointer-events-none disabled:opacity-50"
          >
            <RefreshCw
              className={`h-3.5 w-3.5${rescanning ? " animate-spin" : ""}`}
            />
          </button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={t("skill.addBtn" as Parameters<typeof t>[0])}
                className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-[160px]">
              <DropdownMenuItem onSelect={handleStartAiCreate}>
                <Sparkles className="h-4 w-4" />
                {t("skill.aiCreate" as Parameters<typeof t>[0])}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => openAddDialog("link")}>
                <FileText className="h-4 w-4" />
                {t("skill.linkImportShort" as Parameters<typeof t>[0])}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => openAddDialog("upload")}>
                <Upload className="h-4 w-4" />
                {t("skill.upload" as Parameters<typeof t>[0])}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {/* Content area */}
      {loading ? (
        <PageLoader logo />
      ) : (
        <div className="flex-1 overflow-y-auto py-4">
          <div className="mb-4 px-4">
            <CategorizedList
              items={filteredSkills}
              categories={categories}
              selectedId={effectiveActiveId}
              getId={(s: SkillView) => s.id}
              onSelect={(s: SkillView) => {
                if (!isCloudOnlyResource(s)) setActiveSkillId(s.id);
              }}
              renderItem={(skill: SkillView, isSelected: boolean) => {
                const cloudOnly = isCloudOnlyResource(skill);
                // Determine which category this item belongs to so we
                // can pass the right origin badge. CategorizedList
                // partitions by filter predicates — we match the same
                // logic here for the badge lookup.
                const cat = categories.find((c) => c.filter(skill));
                const categoryId = cat?.id ?? "_other";
                // Scroll the deep-linked (``?skill=``) card into view once.
                const scrollTarget =
                  isSelected && !!skillParam && !scrolledToParamRef.current;
                const card = (
                  <SkillCard
                    skill={toCardSkill(skill)}
                    originBadge={badgeForCategory(categoryId, skill, t)}
                    pluginBadge={pluginBadgeFor(skill.slug ?? skill.name)}
                    active={!cloudOnly && isSelected}
                    onClick={() => {
                      if (!cloudOnly) setActiveSkillId(skill.id);
                    }}
                    actions={
                      <div
                        className="flex items-center gap-2"
                        // The switch lives inside the card's click target; stop
                        // propagation so toggling never opens the detail panel.
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
                          resource={skill as unknown as Record<string, unknown>}
                        />
                      </div>
                    }
                  />
                );
                if (!scrollTarget) return card;
                return (
                  <div
                    ref={(el) => {
                      if (el) {
                        el.scrollIntoView({ block: "center" });
                        scrolledToParamRef.current = true;
                      }
                    }}
                  >
                    {card}
                  </div>
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
                  action={
                    skills.length === 0 ? (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="default" size="sm">
                            <Plus className="h-3 w-3" />
                            {t("skill.emptyAction" as Parameters<typeof t>[0])}
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent
                          align="center"
                          className="min-w-[160px]"
                        >
                          <DropdownMenuItem
                            onSelect={() => void handleStartAiCreate()}
                          >
                            <Sparkles className="h-4 w-4" />
                            {t("skill.aiCreate" as Parameters<typeof t>[0])}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onSelect={() => openAddDialog("link")}
                          >
                            <FileText className="h-4 w-4" />
                            {t(
                              "skill.linkImportShort" as Parameters<
                                typeof t
                              >[0],
                            )}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onSelect={() => openAddDialog("upload")}
                          >
                            <Upload className="h-4 w-4" />
                            {t("skill.upload" as Parameters<typeof t>[0])}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    ) : undefined
                  }
                />
              }
            />
          </div>
        </div>
      )}

      {/* ── Add Skill Dialog ─────────────────────────────────── */}
      <SkillAddDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        mode={addMode}
        onComplete={() => void loadSkills()}
        onArchivePreview={(file) => skillsApi.importArchivePreview(file)}
        onArchiveConfirm={(data) => skillsApi.importArchiveConfirm(data)}
        onStartAiCreate={(context) => navigate(skillCreatorDraftUrl(context))}
        onLinkPreview={(url) => skillsApi.importUrlPreview(url)}
        onLinkConfirm={(data) => skillsApi.importUrlConfirm(data)}
      />

      {/* ── Edit Skill Dialog ────────────────────────────────── */}
      <SkillEditDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        skill={currentSkill}
        onSubmit={async (skillId, data) => {
          await skillsApi.update(skillId, data);
          toast.success(t("common.saved" as Parameters<typeof t>[0]));
        }}
        onComplete={() => void loadSkills()}
      />

      {/* ── Delete Skill Dialog ─────────────────────────── */}
      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteOpen(false);
            setDeletePreview(null);
          }
        }}
        title={
          currentSkill
            ? t("skill.deleteConfirm" as Parameters<typeof t>[0], {
                name: currentSkill.name,
              })
            : undefined
        }
        description={t("skill.deleteConfirmDesc" as Parameters<typeof t>[0])}
        confirmLabel={t("common.confirm" as Parameters<typeof t>[0])}
        onConfirm={handleDelete}
      />
    </div>
  );
};
