import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  ArrowLeft,
  Beaker,
  BookOpen,
  BrainCircuit,
  BriefcaseBusiness,
  ChartBar,
  ChevronRight,
  Code2,
  Database,
  FileText,
  Folder,
  FolderKanban,
  FolderOpen,
  FolderPlus,
  Globe2,
  GraduationCap,
  HeartPulse,
  Image as ImageIcon,
  Music,
  Palette,
  Plus,
  RotateCw,
  Scale,
  ShieldCheck,
  Trash2,
  Upload,
  Users,
  Video,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  Button,
  DeleteConfirmDialog,
  Badge,
  DocumentDetailPanel,
  EmptyState,
  IndexingStatusBadge,
  PageLoader,
  SearchInput,
  cn,
} from "@valuz/ui";
import { docsApi, kbApi, usePanelStore } from "@valuz/core";
import { ResourceActionSlot } from "../components/ResourceActionSlot";
import type {
  DocDetail,
  DocsHealth,
  KbDetail,
  KbListItem,
  KbTreeNode,
} from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";
import { usePlatform } from "@valuz/app/platform";
import { useTranslation } from "@valuz/core";
import { CreateKbDialog } from "../components";

type UiStatus = "ready" | "indexing" | "failed" | "queued" | "missing";

const KB_CARD_MIN_WIDTH = 240;
const KB_CARD_PREFERRED_MIN_WIDTH = 280;
const KB_CARD_MAX_WIDTH = 360;
const KB_CARD_GAP = 12;

const KB_ICON_RULES: Array<{ keywords: string[]; icon: LucideIcon }> = [
  {
    keywords: ["ai", "agent", "智能", "模型", "大模型", "机器人"],
    icon: BrainCircuit,
  },
  {
    keywords: ["code", "api", "sdk", "dev", "代码", "开发", "前端", "后端"],
    icon: Code2,
  },
  {
    keywords: ["设计", "视觉", "品牌", "ui", "ux", "design"],
    icon: Palette,
  },
  {
    keywords: ["数据", "分析", "指标", "报表", "analytics", "metrics"],
    icon: ChartBar,
  },
  {
    keywords: ["数据库", "db", "sql", "data warehouse"],
    icon: Database,
  },
  {
    keywords: ["研究", "实验", "论文", "research", "paper", "science"],
    icon: Beaker,
  },
  {
    keywords: ["法律", "合同", "法务", "legal", "contract"],
    icon: Scale,
  },
  {
    keywords: ["医疗", "健康", "health", "medical"],
    icon: HeartPulse,
  },
  {
    keywords: ["安全", "权限", "security", "auth"],
    icon: ShieldCheck,
  },
  {
    keywords: ["教育", "课程", "学习", "培训", "course", "learning"],
    icon: GraduationCap,
  },
  {
    keywords: ["项目", "产品", "需求", "roadmap", "project", "product"],
    icon: FolderKanban,
  },
  {
    keywords: ["客户", "用户", "销售", "市场", "crm", "sales", "marketing"],
    icon: Users,
  },
  {
    keywords: ["商业", "业务", "公司", "business", "company"],
    icon: BriefcaseBusiness,
  },
  {
    keywords: ["图片", "图像", "image", "photo"],
    icon: ImageIcon,
  },
  {
    keywords: ["视频", "影像", "video"],
    icon: Video,
  },
  {
    keywords: ["音频", "音乐", "audio", "music"],
    icon: Music,
  },
  {
    keywords: ["网站", "国际", "全球", "web", "global"],
    icon: Globe2,
  },
];

const KB_ICON_FALLBACKS: LucideIcon[] = [
  BookOpen,
  Folder,
  FileText,
  FolderOpen,
  BrainCircuit,
  Code2,
  Palette,
  ChartBar,
  Database,
  Beaker,
  Scale,
  HeartPulse,
  ShieldCheck,
  GraduationCap,
  FolderKanban,
  Users,
  BriefcaseBusiness,
  ImageIcon,
  Video,
  Music,
  Globe2,
];

function getKbIconCandidates(name: string): LucideIcon[] {
  const normalized = name.toLowerCase();
  const matched = KB_ICON_RULES.filter(({ keywords }) =>
    keywords.some((keyword) => normalized.includes(keyword.toLowerCase())),
  ).map(({ icon }) => icon);
  return [...matched, ...KB_ICON_FALLBACKS];
}

function getUniqueKbIcons(kbs: KbListItem[]): Record<string, LucideIcon> {
  const used = new Set<LucideIcon>();
  const icons: Record<string, LucideIcon> = {};

  for (const kb of kbs) {
    const candidates = getKbIconCandidates(kb.name);
    const icon =
      candidates.find((candidate) => !used.has(candidate)) ?? BookOpen;
    icons[kb.id] = icon;
    used.add(icon);
  }

  return icons;
}

function toUiStatus(status: string): UiStatus {
  if (status === "processing") return "indexing";
  if (
    status === "ready" ||
    status === "failed" ||
    status === "queued" ||
    status === "missing"
  ) {
    return status;
  }
  return "queued";
}

function formatExt(mime: string | null): string {
  if (!mime) return "";
  const sub = mime.split("/").pop();
  return sub?.toUpperCase() ?? "";
}

function kbStatusLabel(
  status: KbListItem["status"],
  t: (key: string) => string,
): {
  text: string;
  variant: "success" | "brand" | "warning";
} {
  switch (status) {
    case "all_ready":
      return { text: t("knowledge.allReady"), variant: "success" };
    case "has_processing":
      return { text: "解析中", variant: "brand" };
    case "has_missing":
      return { text: t("knowledge.hasMissing"), variant: "warning" };
  }
}

export const KnowledgePage = () => {
  const { t } = useTranslation();
  const { copyFiles } = usePlatform();
  const [kbs, setKbs] = useState<KbListItem[]>([]);
  const [health, setHealth] = useState<DocsHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const kbIcons = useMemo(() => getUniqueKbIcons(kbs), [kbs]);

  const [activeKb, setActiveKb] = useState<KbDetail | null>(null);
  const [rootNodes, setRootNodes] = useState<KbTreeNode[]>([]);
  const [childrenMap, setChildrenMap] = useState<Record<string, KbTreeNode[]>>(
    {},
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [treeLoading, setTreeLoading] = useState(false);
  const [rescanning, setRescanning] = useState(false);

  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<DocDetail | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  const [createOpen, setCreateOpen] = useState(false);

  const [deleteKbOpen, setDeleteKbOpen] = useState(false);
  const [deleteDocOpen, setDeleteDocOpen] = useState(false);

  const [dragOver, setDragOver] = useState(false);
  const [dropping, setDropping] = useState(false);
  const dragCounterRef = useRef(0);
  const kbGridRef = useRef<HTMLDivElement | null>(null);
  const [kbGridWidth, setKbGridWidth] = useState(0);

  const {
    setRightPanel,
    setHeader,
    setHeaderClassName,
    setContentInnerClassName,
  } = useProjectOutlet();
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);

  useEffect(() => {
    const el = kbGridRef.current;
    if (!el) return;

    const updateWidth = () => setKbGridWidth(el.clientWidth);
    updateWidth();

    const ro = new ResizeObserver(updateWidth);
    ro.observe(el);
    return () => ro.disconnect();
  }, [loading, kbs.length, activeKb]);

  const kbGridColumns = useMemo(() => {
    if (kbGridWidth <= 0) {
      return `repeat(auto-fill, ${KB_CARD_MAX_WIDTH}px)`;
    }

    const maxColumns = Math.max(
      1,
      Math.floor(
        (kbGridWidth + KB_CARD_GAP) /
          (KB_CARD_PREFERRED_MIN_WIDTH + KB_CARD_GAP),
      ),
    );
    const columns = Math.min(kbs.length || 1, maxColumns);
    const widthAtMaxColumns =
      (kbGridWidth - KB_CARD_GAP * (columns - 1)) / columns;
    const cardWidth = Math.max(
      KB_CARD_MIN_WIDTH,
      Math.min(KB_CARD_MAX_WIDTH, Math.floor(widthAtMaxColumns)),
    );

    return `repeat(${columns}, minmax(${KB_CARD_MIN_WIDTH}px, ${cardWidth}px))`;
  }, [kbGridWidth, kbs.length]);

  // ── Load KB list ──────────────────────────────────────────────────

  const loadKbs = useCallback(async () => {
    try {
      const [kbRes, healthRes] = await Promise.all([
        kbApi.list(),
        docsApi.health(),
      ]);
      setKbs(kbRes.knowledge_bases);
      setHealth(healthRes);
    } catch {
      toast.error(t("knowledge.cannotLoadList" as Parameters<typeof t>[0]));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(loadKbs);
  }, [loadKbs]);

  // ── Enter / exit KB detail ────────────────────────────────────────

  const enterKb = useCallback(async (kbId: string) => {
    setTreeLoading(true);
    try {
      const [kb, tree] = await Promise.all([kbApi.get(kbId), kbApi.tree(kbId)]);
      setActiveKb(kb);
      setRootNodes(tree.nodes);
      setChildrenMap({});
      setExpanded(new Set());
      setSelectedDocId(null);
      setSelectedDoc(null);
      setPreview(null);
      setSearchQuery("");
    } catch {
      toast.error(t("knowledge.cannotLoad" as Parameters<typeof t>[0]));
    } finally {
      setTreeLoading(false);
    }
  }, []);

  const exitKb = useCallback(() => {
    setActiveKb(null);
    setRootNodes([]);
    setChildrenMap({});
    setExpanded(new Set());
    setSelectedDocId(null);
    setSelectedDoc(null);
    setPreview(null);
    setRightPanel(null);
    setSearchQuery("");
    loadKbs();
  }, [loadKbs, setRightPanel]);

  // ── Tree interactions ─────────────────────────────────────────────

  const toggleFolder = useCallback(
    async (folderId: string) => {
      if (expanded.has(folderId)) {
        setExpanded((prev) => {
          const n = new Set(prev);
          n.delete(folderId);
          return n;
        });
        return;
      }
      if (!childrenMap[folderId] && activeKb) {
        try {
          const res = await kbApi.tree(activeKb.id, folderId);
          setChildrenMap((prev) => ({ ...prev, [folderId]: res.nodes }));
        } catch {
          toast.error(t("knowledge.cannotLoadDir" as Parameters<typeof t>[0]));
          return;
        }
      }
      setExpanded((prev) => new Set(prev).add(folderId));
    },
    [expanded, childrenMap, activeKb],
  );

  const selectDoc = useCallback(async (docId: string) => {
    setSelectedDocId(docId);
    try {
      const [doc, prev] = await Promise.all([
        docsApi.get(docId),
        docsApi
          .preview(docId)
          .catch(() => ({ document_id: docId, markdown: "" })),
      ]);
      setSelectedDoc(doc);
      setPreview(prev.markdown || null);
    } catch {
      toast.error(t("knowledge.cannotLoadDetail" as Parameters<typeof t>[0]));
    }
  }, []);

  // ── Right panel ───────────────────────────────────────────────────

  useEffect(() => {
    if (!selectedDoc) {
      setRightPanel(null);
      return;
    }
    setRightPanel(
      <DocumentDetailPanel
        doc={{
          name: selectedDoc.filename,
          format: formatExt(selectedDoc.mime_type),
          status: toUiStatus(selectedDoc.status),
          chunks: selectedDoc.chunk_count,
          preview: preview ?? undefined,
        }}
        meta={{
          kbName: activeKb?.name,
          relativePath: selectedDoc.relative_path ?? undefined,
          sourcePath: selectedDoc.source_path ?? undefined,
          fileSize: selectedDoc.file_size_bytes,
          importedAt: selectedDoc.created_at ?? undefined,
        }}
        parse={{
          parserMode: selectedDoc.parser_mode,
          // Camel-case the wire shape so the UI package stays
          // independent of the api layer's snake_case.
          attempts: selectedDoc.parser_attempts.map((a) => ({
            pluginId: a.plugin_id,
            error: a.error,
            occurredAt: a.occurred_at,
            ok: a.ok,
          })),
          lastErrorCode: selectedDoc.last_error_code,
          lastErrorMessage: selectedDoc.last_error_message,
        }}
        onRegenerate={() => {
          // Reindex is a background task on the backend — the POST
          // returns as soon as the task is queued, so the toast fires
          // on submit, not on parse completion. The auto-poll below
          // then surfaces live ``processing`` → ``ready`` status.
          const docId = selectedDoc.id;
          docsApi
            .reindex([docId])
            .then(() => {
              toast.success(
                t("knowledge.reindexStarted" as Parameters<typeof t>[0]),
              );
              // Flip local status to ``processing`` so the auto-poll below
              // kicks in and refreshes status + attempts + preview when the
              // background parse finishes. Without this nudge the doc stays
              // at its previous ``ready`` and the poll guard never fires —
              // the panel then shows stale content until a manual reload.
              setSelectedDoc((prev) =>
                prev && prev.id === docId
                  ? { ...prev, status: "processing" }
                  : prev,
              );
              if (activeKb) enterKb(activeKb.id);
            })
            .catch(() => {
              toast.error(t("common.failed" as Parameters<typeof t>[0]));
            });
        }}
        onDelete={() => setDeleteDocOpen(true)}
      />,
    );
  }, [selectedDoc, preview, setRightPanel, activeKb, enterKb]);

  // Auto-poll the doc detail while the parse is in flight so the
  // panel reflects live state without a manual refresh. Polls every
  // 3s while ``status`` is ``queued`` or ``processing``; stops when
  // it lands on a terminal state (``ready`` / ``failed`` /
  // ``missing``) OR the user navigates to a different doc. The
  // polled fetch reuses ``setSelectedDoc`` so the right-panel useEffect
  // above re-renders with fresh parser_attempts + last_error_message —
  // a stuck-in-indexing PDF will surface its actual progress here.
  useEffect(() => {
    if (!selectedDoc) return;
    const inFlight =
      selectedDoc.status === "queued" || selectedDoc.status === "processing";
    if (!inFlight) return;
    const docId = selectedDoc.id;
    const handle = window.setInterval(async () => {
      try {
        const fresh = await docsApi.get(docId);
        // Race guard: the user may have switched docs between the
        // ``setInterval`` fire and the await resolving. Drop the
        // stale fetch so we don't briefly clobber the new doc's
        // detail with the old doc's data.
        if (selectedDocId !== docId) return;
        setSelectedDoc(fresh);
        // When the parse settles, the preview file has been rewritten with
        // the new engine's output — re-fetch it so the rendered content
        // (not just status / attempts) reflects the re-index without a
        // manual reload. Fires once: this tick flips ``inFlight`` false, so
        // the effect re-runs and clears the interval.
        const settled =
          fresh.status !== "queued" && fresh.status !== "processing";
        if (settled) {
          try {
            const freshPreview = await docsApi.preview(docId);
            if (selectedDocId === docId) {
              setPreview(freshPreview.markdown || null);
            }
          } catch {
            // Preview re-fetch failed — keep the old content; non-fatal.
          }
        }
      } catch {
        // Transient fetch failure — swallow; next tick retries.
      }
    }, 3000);
    return () => window.clearInterval(handle);
  }, [selectedDoc, selectedDocId]);

  // Auto-expand the right panel whenever the user selects a doc. The
  // layout-global ``rightPanelCollapsed`` defaults to ``true`` and the
  // conversation page actively re-collapses it on every session
  // switch — so without this nudge, navigating from a conversation
  // into the KB and clicking a doc would correctly call
  // ``setRightPanel(<DocumentDetailPanel>)`` but the layout would
  // still render ``aside={null}`` because the collapse atom is true.
  // Mirror the conversation page's "expand once on data" behaviour:
  // expand when ``selectedDocId`` transitions to a non-null id;
  // we deliberately do NOT collapse on null so a user who manually
  // toggled the panel keeps their preference for the next click.
  useEffect(() => {
    if (selectedDocId) {
      panelSetCollapsed(false);
    }
  }, [selectedDocId, panelSetCollapsed]);

  // ── KB actions ────────────────────────────────────────────────────

  const handleRescan = useCallback(async () => {
    if (!activeKb) return;
    setRescanning(true);
    try {
      await kbApi.rescan(activeKb.id);
      toast.success(t("knowledge.rescanStarted" as Parameters<typeof t>[0]));
      await enterKb(activeKb.id);
    } catch {
      toast.error(t("knowledge.rescanFailed" as Parameters<typeof t>[0]));
    } finally {
      setRescanning(false);
    }
  }, [activeKb, enterKb]);

  const handleDeleteKb = async () => {
    if (!activeKb) return;
    try {
      await kbApi.delete(activeKb.id);
      toast.success(
        t("knowledge.deleted" as Parameters<typeof t>[0], {
          name: activeKb.name,
        }),
      );
      setDeleteKbOpen(false);
      exitKb();
    } catch {
      toast.error(t("knowledge.deleteFailed" as Parameters<typeof t>[0]));
    }
  };

  const handleDeleteDoc = async () => {
    if (!selectedDoc) return;
    try {
      await docsApi.delete(selectedDoc.id);
      toast.success(t("knowledge.docDeleted" as Parameters<typeof t>[0]));
      setDeleteDocOpen(false);
      setSelectedDocId(null);
      setSelectedDoc(null);
      setPreview(null);
      if (activeKb) enterKb(activeKb.id);
    } catch {
      toast.error(t("knowledge.docDeleteFailed" as Parameters<typeof t>[0]));
    }
  };

  // ── Drag & drop ────────────────────────────────────────────────────

  const handleDragEnter = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!activeKb) return;
      dragCounterRef.current++;
      if (e.dataTransfer.types.includes("Files")) {
        setDragOver(true);
      }
    },
    [activeKb],
  );

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current--;
    if (dragCounterRef.current <= 0) {
      dragCounterRef.current = 0;
      setDragOver(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      dragCounterRef.current = 0;
      setDragOver(false);

      if (!activeKb) return;
      const files = Array.from(e.dataTransfer.files);
      if (files.length === 0) return;

      const paths = files
        .map((f) => (f as File & { path?: string }).path)
        .filter(Boolean) as string[];
      if (paths.length === 0) {
        toast.error(t("knowledge.cannotGetPath" as Parameters<typeof t>[0]));
        return;
      }

      setDropping(true);
      try {
        const result = await copyFiles(paths, activeKb.root_path);
        if (result.errors.length > 0) {
          toast.error(
            t("knowledge.copyFailedCount" as Parameters<typeof t>[0], {
              count: String(result.errors.length),
            }),
          );
        }
        if (result.copied > 0) {
          toast.success(
            t("knowledge.imported" as Parameters<typeof t>[0], {
              count: String(result.copied),
            }),
          );
          await kbApi.rescan(activeKb.id);
          await enterKb(activeKb.id);
        }
      } catch {
        toast.error(t("knowledge.importFailed" as Parameters<typeof t>[0]));
      } finally {
        setDropping(false);
      }
    },
    [activeKb, enterKb],
  );

  // ── Tree search filter ────────────────────────────────────────────

  const filterNodes = useCallback(
    (nodes: KbTreeNode[], q: string): KbTreeNode[] => {
      if (!q) return nodes;
      const lq = q.toLowerCase();
      return nodes.filter((n) => n.name.toLowerCase().includes(lq));
    },
    [],
  );

  const filteredRootNodes = useMemo(
    () => filterNodes(rootNodes, searchQuery),
    [rootNodes, searchQuery, filterNodes],
  );

  // ── Render: tree nodes ────────────────────────────────────────────

  const renderNodes = (nodes: KbTreeNode[], depth: number) =>
    nodes.map((node) => (
      <Fragment key={node.id}>
        {node.kind === "folder" ? (
          <button
            type="button"
            onClick={() => toggleFolder(node.id)}
            style={{
              paddingLeft: `${depth * 24 + 12}px`,
              paddingRight: "12px",
            }}
            className={cn(
              "mx-5 flex w-[calc(100%-40px)] items-center gap-2 rounded-[8px] border-b border-[#f7f8fa] py-2.5 text-left transition-colors",
              node.status === "missing"
                ? "opacity-60"
                : "hover:bg-surface-soft",
            )}
          >
            <ChevronRight
              className={cn(
                "h-3.5 w-3.5 shrink-0 text-ink-muted transition-transform",
                expanded.has(node.id) && "rotate-90",
              )}
            />
            {expanded.has(node.id) ? (
              <FolderOpen className="h-4 w-4 shrink-0 text-ink-muted" />
            ) : (
              <Folder className="h-4 w-4 shrink-0 text-ink-muted" />
            )}
            <span className="min-w-0 truncate text-sm text-ink-heading">
              {node.name}
            </span>
            <span className="ml-auto shrink-0 text-xs text-ink-meta">
              {node.document_count}{" "}
              {t("knowledge.docColumn" as Parameters<typeof t>[0])}
            </span>
            {node.status === "missing" && (
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning-text" />
            )}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => selectDoc(node.id)}
            style={{
              paddingLeft: `${depth * 24 + 12}px`,
              paddingRight: "12px",
            }}
            className={cn(
              "mx-5 flex w-[calc(100%-40px)] items-center gap-2 rounded-[8px] border-b border-[#f7f8fa] py-2.5 text-left transition-colors",
              selectedDocId === node.id
                ? "bg-brand-light/35"
                : "hover:bg-surface-soft",
              node.status === "missing" && "opacity-60",
            )}
          >
            <span className="w-3.5 shrink-0" />
            <FileText className="h-4 w-4 shrink-0 text-ink-muted" />
            <span className="min-w-0 truncate text-sm text-ink-heading">
              {node.name}
            </span>
            <span className="ml-auto shrink-0">
              <IndexingStatusBadge status={toUiStatus(node.status)} />
            </span>
          </button>
        )}
        {node.kind === "folder" &&
          expanded.has(node.id) &&
          childrenMap[node.id] &&
          renderNodes(childrenMap[node.id], depth + 1)}
      </Fragment>
    ));

  // ── Render: KB list view ──────────────────────────────────────────

  const renderKbList = () => {
    const isEmpty = !loading && kbs.length === 0;

    return (
      <>
        <div className="flex-1 overflow-y-auto px-5 pb-5 pt-3">
          {loading ? (
            <PageLoader logo className="py-20" />
          ) : isEmpty ? (
            <div className="flex flex-1 justify-center pt-[160px]">
              <div className="flex flex-col items-center px-5 text-center">
                <div className="flex h-11 w-11 items-center justify-center rounded-[14px] bg-surface-soft text-ink-body">
                  <FolderPlus className="h-5 w-5" />
                </div>
                <div className="mt-3 text-sm font-medium text-ink-heading">
                  {t("knowledge.createNew" as Parameters<typeof t>[0])}
                </div>
                <div className="mt-1 max-w-[460px] text-xs leading-5 text-ink-body">
                  {t("knowledge.supportedFormats" as Parameters<typeof t>[0])}
                </div>
                <Button
                  className="mt-4"
                  size="sm"
                  onClick={() => setCreateOpen(true)}
                  variant="default"
                >
                  <Plus className="h-3 w-3" />
                  {t("knowledge.addKb" as Parameters<typeof t>[0])}
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div
                ref={kbGridRef}
                className="grid gap-3"
                style={{
                  gridTemplateColumns: kbGridColumns,
                }}
              >
                {kbs.map((kb) => {
                  const st = kbStatusLabel(kb.status, t);
                  const isProcessing = kb.status === "has_processing";
                  const KbIcon = kbIcons[kb.id] ?? BookOpen;
                  return (
                    <button
                      key={kb.id}
                      type="button"
                      // Always enterable — even while 解析中, so the user can
                      // open the KB and watch per-doc parse status. The
                      // "解析中" badge below keeps the in-flight state visible.
                      onClick={() => enterKb(kb.id)}
                      className={cn(
                        "group",
                        "flex min-h-[148px] w-full flex-col rounded-[12px] border border-surface-border",
                        "bg-[#ffffff] p-4 text-left shadow-xs transition-all",
                        "hover:-translate-y-1 hover:bg-[#ffffff] hover:shadow-md",
                        isProcessing && "bg-brand-light/25",
                      )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-[#f3f2ff] text-brand">
                          <KbIcon className="h-4 w-4" />
                        </div>
                        {isProcessing ? (
                          <Badge variant={st.variant} className="border-0">
                            {st.text}
                          </Badge>
                        ) : null}
                      </div>
                      <div className="mt-4 min-w-0">
                        <div className="truncate text-sm font-medium text-ink-heading">
                          {kb.name}
                        </div>
                        <div className="mt-1 line-clamp-2 break-all text-xs leading-5 text-ink-meta">
                          {kb.root_path}
                        </div>
                      </div>
                      <div className="mt-auto flex items-center justify-between pt-4">
                        <span className="text-xs text-ink-meta">
                          {kb.document_count}{" "}
                          {t("knowledge.docColumn" as Parameters<typeof t>[0])}
                        </span>
                        <div className="flex items-center gap-1">
                          <ResourceActionSlot
                            resourceType="kb"
                            resource={kb as unknown as Record<string, unknown>}
                          />
                          <ChevronRight className="h-4 w-4 shrink-0 text-ink-muted opacity-0 transition-opacity group-hover:opacity-100" />
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </>
    );
  };

  // ── Render: KB detail (tree) view ─────────────────────────────────

  const renderKbDetail = () => (
    <>
      <div className="flex-1 overflow-y-auto">
        <div className="relative flex items-center gap-3 px-5 pb-5 pt-0 after:absolute after:bottom-0 after:left-5 after:right-5 after:h-px after:bg-[#f7f8fa]">
          <SearchInput
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder={t(
              "knowledge.searchDocPlaceholder" as Parameters<typeof t>[0],
            )}
            className="max-w-[340px] flex-1"
          />
          <div className="flex-1" />
          <div className="text-xs text-ink-meta">
            {activeKb?.document_count ?? 0}{" "}
            {t("knowledge.docColumn" as Parameters<typeof t>[0])}
            {activeKb?.auto_discover && (
              <>
                <span className="mx-1.5">·</span>
                <span>
                  {t("knowledge.autoDiscover" as Parameters<typeof t>[0])}
                </span>
              </>
            )}
          </div>
        </div>

        {treeLoading ? (
          <PageLoader logo className="py-20" />
        ) : filteredRootNodes.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20">
            <EmptyState
              icon={<FolderOpen className="h-10 w-10 text-ink-muted" />}
              message={
                searchQuery
                  ? t("knowledge.noMatchDocs" as Parameters<typeof t>[0])
                  : t("knowledge.noDocs" as Parameters<typeof t>[0])
              }
            />
          </div>
        ) : (
          <div>{renderNodes(filteredRootNodes, 0)}</div>
        )}
      </div>
    </>
  );

  // ── Main render ───────────────────────────────────────────────────

  const pageHeader = useMemo(() => {
    return (
      <div className="flex w-full items-center justify-between gap-4">
        {activeKb ? (
          <div className="flex min-w-0 items-center gap-2 text-sm leading-5">
            <button
              type="button"
              onClick={exitKb}
              className="inline-flex shrink-0 items-center gap-1 text-ink-meta transition-colors hover:text-ink-heading"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              <span>{t("knowledge.backHome" as Parameters<typeof t>[0])}</span>
            </button>
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
            <span className="min-w-0 truncate font-medium text-ink-heading">
              {activeKb.name}
            </span>
          </div>
        ) : (
          <div className="flex min-w-0 flex-col justify-center">
            <span className="text-base font-semibold leading-5 text-ink-heading">
              {t("knowledge.knowledgeBase" as Parameters<typeof t>[0])}
            </span>
            <span className="truncate text-xs leading-4 text-ink-body">
              {t("knowledge.linkLocalDir" as Parameters<typeof t>[0])}
            </span>
          </div>
        )}
        {activeKb ? (
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleRescan}
              loading={rescanning}
              aria-label={t("common.refresh" as Parameters<typeof t>[0])}
              className="h-8 w-8 p-0 text-ink-meta hover:text-ink-heading"
            >
              <RotateCw className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              aria-label={t("common.delete" as Parameters<typeof t>[0])}
              className="h-8 w-8 p-0 text-ink-meta hover:text-[#f54b4b]"
              onClick={() => setDeleteKbOpen(true)}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
            <ResourceActionSlot
              resourceType="kb"
              resource={activeKb as unknown as Record<string, unknown>}
            />
          </div>
        ) : (
          <div className="flex shrink-0 items-center gap-2">
            {!loading && health && (
              <div className="hidden h-8 items-center gap-2 rounded-lg border border-surface-border bg-surface-soft px-3 text-xs md:flex">
                <span className="text-ink-heading font-medium">
                  {health.total_documents}{" "}
                  {t("knowledge.docColumn" as Parameters<typeof t>[0])}
                </span>
                <span className="text-ink-meta">·</span>
                <span className="text-ink-meta">
                  {health.ready_count}{" "}
                  {t("knowledge.statusReady" as Parameters<typeof t>[0])}
                  {health.processing_count > 0
                    ? ` · ${health.processing_count} ${t("knowledge.indexing" as Parameters<typeof t>[0])}`
                    : ""}
                  {health.missing_count > 0
                    ? ` · ${health.missing_count} ${t("knowledge.statusFailed" as Parameters<typeof t>[0])}`
                    : ""}
                </span>
              </div>
            )}
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus className="h-3 w-3" />
              {t("knowledge.add" as Parameters<typeof t>[0])}
            </Button>
          </div>
        )}
      </div>
    );
  }, [activeKb, exitKb, handleRescan, health, loading, rescanning, t]);

  useEffect(() => {
    setHeader(pageHeader);
    setHeaderClassName(activeKb ? "h-auto px-5 py-5" : "h-auto px-5 py-5");
    setContentInnerClassName("p-0");
    return () => {
      setHeader(null);
      setHeaderClassName(undefined);
      setContentInnerClassName(undefined);
    };
  }, [
    activeKb,
    pageHeader,
    setContentInnerClassName,
    setHeader,
    setHeaderClassName,
  ]);

  return (
    <div
      className="relative flex h-full min-h-0 flex-col bg-card"
      onDragEnter={activeKb ? handleDragEnter : undefined}
      onDragLeave={activeKb ? handleDragLeave : undefined}
      onDragOver={activeKb ? handleDragOver : undefined}
      onDrop={activeKb ? handleDrop : undefined}
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center bg-brand/5 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-brand/40 bg-card/90 px-12 py-10 shadow-lg">
            <Upload className="h-8 w-8 text-brand" />
            <span className="text-sm font-medium text-ink-heading">
              {dropping
                ? t("common.processing" as Parameters<typeof t>[0])
                : t("knowledge.importFiles" as Parameters<typeof t>[0])}
            </span>
            <span className="text-xs text-ink-meta">
              {t("knowledge.importFiles" as Parameters<typeof t>[0])}
            </span>
          </div>
        </div>
      )}
      {activeKb ? renderKbDetail() : renderKbList()}

      <CreateKbDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onSubmit={async (data) => {
          const kb = await kbApi.create(data);
          toast.success(
            t("knowledge.created" as Parameters<typeof t>[0], {
              name: kb.name,
            }),
          );
          await loadKbs();
        }}
      />

      {/* Delete KB Dialog */}
      <DeleteConfirmDialog
        open={deleteKbOpen}
        onOpenChange={setDeleteKbOpen}
        itemName={activeKb?.name}
        description={t("knowledge.deleteKbDesc" as Parameters<typeof t>[0], {
          name: activeKb?.name ?? "",
        })}
        onConfirm={handleDeleteKb}
      />

      {/* Delete Document Dialog */}
      <DeleteConfirmDialog
        open={deleteDocOpen}
        onOpenChange={setDeleteDocOpen}
        itemName={selectedDoc?.filename}
        description={t("knowledge.deleteKbDesc" as Parameters<typeof t>[0], {
          name: activeKb?.name ?? "",
        })}
        onConfirm={handleDeleteDoc}
      />
    </div>
  );
};
