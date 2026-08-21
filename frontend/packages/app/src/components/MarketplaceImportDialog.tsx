import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  Activity,
  Download,
  FileText,
  Folder,
  Info,
  KeyRound,
  Link2,
  LoaderCircle,
  MessageSquare,
  ShieldCheck,
} from "lucide-react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@valuz/ui";
import type {
  MarketplaceInstallResult,
  MarketplaceItem,
  MarketplaceItemDetail,
  ProjectListItem,
} from "@valuz/core";
import {
  agentsApi,
  ApiError,
  marketplaceApi,
  projectsApi,
  useTranslation,
} from "@valuz/core";
import { usePlatform } from "../platform";
import {
  MarketplaceBadgePill,
  formatCount,
  formatSize,
  marketplaceIcon,
  tintFor,
} from "./marketplace-ui";

interface MarketplaceImportDialogProps {
  item: MarketplaceItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a successful install so the browse page can flip the
   * card's installed state without a full reload. */
  onInstalled: (item: MarketplaceItem, result: MarketplaceInstallResult) => void;
}

/** One import-preview dialog, three bodies — skill (security + files),
 * agent template (instructions + equipment), team (member roster). */
export function MarketplaceImportDialog({
  item,
  open,
  onOpenChange,
  onInstalled,
}: MarketplaceImportDialogProps) {
  const navigate = useNavigate();
  const platform = usePlatform();
  const { t } = useTranslation();
  const tr = (key: string, params?: Record<string, string | number>) =>
    t(key as Parameters<typeof t>[0], params);
  const [detail, setDetail] = useState<MarketplaceItemDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installProgress, setInstallProgress] = useState(0);
  const [activatingTeam, setActivatingTeam] = useState(false);
  const [projectMode, setProjectMode] = useState<"new" | "existing">("new");
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectRootPath, setNewProjectRootPath] = useState("");
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    if (!open || !item) return;
    let cancelled = false;
    setDetail(null);
    setActivatingTeam(false);
    setProjectMode("new");
    setProjects([]);
    setSelectedProjectId("");
    setNewProjectName("");
    setNewProjectRootPath("");
    setLoading(true);
    marketplaceApi
      .get(item.id)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch(() => {
        if (!cancelled) toast.error(tr("marketplace.error.installFailed"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, item?.id]);

  useEffect(() => {
    if (!open || item?.type !== "agent_team_template" || !item.installed) return;
    void loadProjects().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, item?.id, item?.installed]);

  useEffect(() => {
    if (!installing) {
      setInstallProgress(0);
      return;
    }
    setInstallProgress(8);
    const timer = window.setInterval(() => {
      setInstallProgress((current) => {
        if (current >= 92) return current;
        if (current < 35) return current + 4;
        if (current < 70) return current + 2;
        return current + 1;
      });
    }, 700);
    return () => window.clearInterval(timer);
  }, [installing]);

  if (!item) return null;

  const view = detail
    ? ({
        ...detail,
        description: detail.description || item.description,
        subtitle: detail.subtitle ?? item.subtitle,
        category_label: detail.category_label ?? item.category_label,
        subcategories: detail.subcategories.length ? detail.subcategories : item.subcategories,
      } as MarketplaceItemDetail)
    : (item as MarketplaceItemDetail);
  const Icon = marketplaceIcon(view.icon);
  const tint = tintFor(item.id);
  const isImageIcon = !!view.icon && /^https?:\/\//.test(view.icon);
  const headerBadges = view.badges.filter(
    (b) =>
      item.type !== "skill" &&
      !["free_install", "reviewed_skillhub", "reviewed_valuz"].includes(b),
  );

  const typeLabel =
    item.type === "skill"
      ? tr("marketplace.modalTypeSkill")
      : item.type === "agent_template"
        ? tr("marketplace.modalTypeAgent")
        : tr("marketplace.modalTypeTeam");

  const installLabel =
    item.type === "skill"
      ? tr("marketplace.installSkill")
      : item.type === "agent_template"
        ? tr("marketplace.installAgent")
        : tr("marketplace.installTeam");

  const installTarget =
    item.type === "skill"
      ? tr("marketplace.installTargetSkill")
      : tr("marketplace.installTargetAgent");

  const loadProjects = async () => {
    const res = await projectsApi.list();
    const projectItems = res.projects.filter((p) => p.kind === "project");
    setProjects(projectItems);
  };

  const openTeamActivation = async () => {
    setActivatingTeam(true);
    setProjectMode("new");
    setNewProjectName((current) => current || view.title);
    await loadProjects().catch(() => undefined);
  };

  const handleSelectProjectRoot = async () => {
    try {
      const path = await platform.selectDirectory();
      if (path) setNewProjectRootPath(path);
    } catch {
      toast.error(tr("marketplace.error.projectDirectoryRequired"));
    }
  };

  const handleInstall = async () => {
    if (item.type === "agent_team_template" && item.installed) {
      await openTeamActivation();
      return;
    }
    setInstalling(true);
    try {
      const result = await marketplaceApi.install(item.id);
      const name = view.title;
      if (result.status === "already_installed") {
        toast.info(tr("marketplace.toastAlreadyInstalled", { name }));
      } else if (item.type === "skill") {
        toast.success(tr("marketplace.toastSkillInstalled", { name }));
      } else if (item.type === "agent_template") {
        toast.success(tr("marketplace.toastAgentInstalled", { name }));
      } else {
        toast.success(tr("marketplace.toastTeamInstalled", { name }));
      }
      setInstallProgress(100);
      onInstalled(item, result);
      if (item.type === "agent_team_template") {
        await openTeamActivation();
      } else {
        onOpenChange(false);
      }
    } catch (err) {
      if (err instanceof ApiError && err.i18nKey) {
        toast.error(t(err.i18nKey as Parameters<typeof t>[0], err.i18nParams as never));
      } else if (err instanceof ApiError && err.message) {
        toast.error(err.message);
      } else {
        toast.error(tr("marketplace.error.installFailed"));
      }
    } finally {
      setInstalling(false);
    }
  };

  const handleLaunchTeam = async () => {
    const memberSlugs = (view.members ?? [])
      .map((m) => m.slug)
      .filter((slug): slug is string => !!slug);
    const leadSlug = (view.members ?? []).find((m) => m.lead && m.slug)?.slug ?? memberSlugs[0];
    if (!leadSlug || memberSlugs.length === 0) {
      toast.error(tr("marketplace.error.launchFailed"));
      return;
    }

    setLaunching(true);
    try {
      let projectId = projectMode === "existing" ? selectedProjectId : "";
      if (projectMode === "new") {
        const name = newProjectName.trim() || view.title;
        const rootPath = newProjectRootPath.trim();
        if (!rootPath) {
          toast.error(tr("marketplace.error.projectDirectoryRequired"));
          return;
        }
        const project = await projectsApi.create({ name, root_path: rootPath });
        projectId = project.id;
      }
      if (!projectId) {
        toast.error(tr("marketplace.error.launchFailed"));
        return;
      }

      await Promise.all(
        memberSlugs.map(async (slug) => {
          try {
            await agentsApi.deploy(projectId, { source_agent_slug: slug, agent_slug: slug });
          } catch (err) {
            const message = err instanceof Error ? err.message : "";
            if (!message.includes("409") && !message.includes("already deployed")) {
              throw err;
            }
          }
        }),
      );

      const projectMembers = await agentsApi.listMembers(projectId);
      const leadMember = projectMembers.agents.find(
        (agent) =>
          agent.member.source_agent_slug === leadSlug || agent.member.agent_slug === leadSlug,
      );
      const projectLeadSlug = leadMember?.member.agent_slug ?? leadSlug;
      toast.success(tr("marketplace.toastTeamLaunched", { name: view.title }));
      onOpenChange(false);
      navigate(
        `/projects/${encodeURIComponent(projectId)}?agent=${encodeURIComponent(projectLeadSlug)}`,
      );
    } catch (err) {
      if (err instanceof ApiError && err.i18nKey) {
        toast.error(t(err.i18nKey as Parameters<typeof t>[0], err.i18nParams as never));
      } else if (err instanceof Error && err.message) {
        toast.error(err.message);
      } else {
        toast.error(tr("marketplace.error.launchFailed"));
      }
    } finally {
      setLaunching(false);
    }
  };

  const meta: { k: string; v: string }[] = [];
  if (item.type === "skill") {
    if (view.version) meta.push({ k: tr("marketplace.modalVersion"), v: view.version });
    if (view.owner) meta.push({ k: tr("marketplace.modalSource"), v: view.owner });
    if (view.updated_at) meta.push({ k: tr("marketplace.modalUpdated"), v: view.updated_at });
    if (view.stats?.downloads != null)
      meta.push({ k: tr("marketplace.modalDownloads"), v: formatCount(view.stats.downloads) });
  } else if (item.type === "agent_template") {
    if (view.runtime) meta.push({ k: tr("marketplace.modalRuntime"), v: view.runtime });
    if (view.category_label)
      meta.push({ k: tr("marketplace.modalCategory"), v: view.category_label });
  }
  const canLaunchTeam =
    !activatingTeam ||
    (projectMode === "existing"
      ? selectedProjectId.length > 0
      : newProjectRootPath.trim().length > 0);

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && (installing || launching)) return;
        onOpenChange(nextOpen);
      }}
    >
      <DialogContent className="flex max-h-[88vh] w-[760px] max-w-[94vw] flex-col gap-0 overflow-hidden p-0">
        {/* header */}
        <DialogHeader className="border-b border-surface-border px-6 py-5 text-left">
          <div className="flex items-start gap-3.5">
            <div
              className="flex h-12 w-12 flex-none items-center justify-center overflow-hidden rounded-xl"
              style={isImageIcon ? undefined : { background: tint.bg, color: tint.fg }}
            >
              {isImageIcon ? (
                <img src={view.icon ?? undefined} alt="" className="h-full w-full object-cover" />
              ) : (
                <Icon className="h-6 w-6" />
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="mb-1.5 flex flex-wrap gap-1.5">
                {item.type === "skill" ? null : (
                  <span className="rounded border border-surface-border bg-surface-soft px-1.5 py-px font-mono text-micro text-ink-meta">
                    {typeLabel}
                  </span>
                )}
                {headerBadges.map((b) => (
                  <MarketplaceBadgePill key={b} badge={b} />
                ))}
              </div>
              <div className="min-w-0">
                <DialogTitle
                  title={activatingTeam ? tr("marketplace.teamActivationTitle") : view.title}
                  className="line-clamp-2 min-w-0 break-words text-[17px] font-semibold leading-snug tracking-tight text-ink-heading"
                >
                  {activatingTeam ? tr("marketplace.teamActivationTitle") : view.title}
                </DialogTitle>
              </div>
              {(() => {
                if (activatingTeam) {
                  return (
                    <DialogDescription className="mt-2 max-w-[520px] text-[12.5px] leading-relaxed text-ink-body">
                      {tr("marketplace.teamActivationSubtitle", { name: view.title })}
                    </DialogDescription>
                  );
                }
                if (item.type === "skill") return null;
                const desc =
                  item.type === "agent_template"
                    ? (view.subtitle ?? view.description)
                    : (view.description || view.subtitle);
                if (!desc || desc === view.title) return null;
                return (
                  <DialogDescription className="mt-2 line-clamp-3 max-w-[520px] text-[12.5px] leading-relaxed text-ink-body">
                    {desc}
                  </DialogDescription>
                );
              })()}
            </div>
          </div>
        </DialogHeader>

        {/* body */}
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {installing ? (
            <InstallProgressCard view={view} progress={installProgress} tr={tr} />
          ) : null}
          {loading && !detail ? (
            <div className="py-8 text-center text-sm text-ink-meta">
              {tr("marketplace.loading")}
            </div>
          ) : item.type === "skill" ? (
            <SkillBody view={view} tr={tr} />
          ) : item.type === "agent_template" ? (
            <AgentBody view={view} meta={meta} tr={tr} />
          ) : activatingTeam ? (
            <TeamActivationBody
              view={view}
              tr={tr}
              projects={projects}
              projectMode={projectMode}
              selectedProjectId={selectedProjectId}
              newProjectName={newProjectName}
              newProjectRootPath={newProjectRootPath}
              onProjectModeChange={setProjectMode}
              onProjectChange={setSelectedProjectId}
              onNewProjectNameChange={setNewProjectName}
              onSelectProjectRoot={() => void handleSelectProjectRoot()}
              onRefreshProjects={() => void loadProjects()}
            />
          ) : (
            <TeamBody view={view} tr={tr} />
          )}
        </div>

        {/* footer */}
        <div className="flex items-center justify-between gap-3 border-t border-surface-border bg-surface px-6 py-3.5">
          <div className="flex items-center gap-1.5 text-xs text-ink-meta">
            <Info className="h-3.5 w-3.5" />
            {activatingTeam ? tr("marketplace.teamActivationTarget") : installTarget}
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={installing || launching}
              onClick={() => onOpenChange(false)}
            >
              {activatingTeam ? tr("marketplace.teamActivationSkip") : t("common.cancel")}
            </Button>
            <Button
              size="sm"
              disabled={
                activatingTeam
                  ? launching || !canLaunchTeam
                  : installing || !!view.locked || (item.installed && item.type === "skill")
              }
              onClick={() => {
                if (activatingTeam) {
                  void handleLaunchTeam();
                } else {
                  void handleInstall();
                }
              }}
            >
              {activatingTeam ? (
                <MessageSquare className="mr-1 h-3.5 w-3.5" />
              ) : (
                <Download className="mr-1 h-3.5 w-3.5" />
              )}
              {activatingTeam
                ? launching
                  ? tr("marketplace.teamLaunching")
                  : tr("marketplace.teamLaunch")
                : installing
                  ? tr("marketplace.installing")
                  : item.installed && item.type === "agent_team_template"
                    ? tr("marketplace.teamUse")
                    : item.installed
                      ? tr("marketplace.installed")
                      : installLabel}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

type Tr = (key: string, params?: Record<string, string | number>) => string;

function MetaRow({ meta }: { meta: { k: string; v: string }[] }) {
  if (meta.length === 0) return null;
  return (
    <div className="mb-4 flex flex-wrap gap-x-6 gap-y-2">
      {meta.map((m) => (
        <div key={m.k}>
          <div className="mb-0.5 font-mono text-micro uppercase tracking-wider text-ink-meta">
            {m.k}
          </div>
          <div className="text-sm text-ink-heading">{m.v}</div>
        </div>
      ))}
    </div>
  );
}

function InstallProgressCard({
  view,
  progress,
  tr,
}: {
  view: MarketplaceItemDetail;
  progress: number;
  tr: Tr;
}) {
  const membersCount = view.members?.length ?? 0;
  const skillCount =
    view.bound_skills?.length ??
    view.skill_count ??
    (view.members ?? []).reduce((sum, member) => sum + (member.skill_count ?? 0), 0);
  const stageKey =
    progress < 30
      ? "marketplace.installProgressPreparing"
      : progress < 70
        ? "marketplace.installProgressDownloading"
        : progress < 92
          ? "marketplace.installProgressCreatingAgents"
          : "marketplace.installProgressFinishing";
  const hint =
    view.type === "agent_team_template"
      ? tr("marketplace.installProgressTeamHint", {
          members: membersCount,
          skills: skillCount,
        })
      : view.type === "agent_template"
        ? tr("marketplace.installProgressAgentHint", { skills: skillCount })
        : tr("marketplace.installProgressSkillHint");

  return (
    <section className="mb-5 rounded-lg border border-brand/20 bg-brand-light/40 px-3.5 py-3">
      <div className="mb-2.5 flex items-start gap-2.5">
        <LoaderCircle className="mt-0.5 h-4 w-4 flex-none animate-spin text-brand" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <div className="text-[12.5px] font-semibold text-ink-heading">
              {tr("marketplace.installProgressTitle")}
            </div>
            <div className="font-mono text-2xs tabular-nums text-brand">
              {Math.round(progress)}%
            </div>
          </div>
          <div className="mt-1 text-xs leading-relaxed text-ink-body">{hint}</div>
        </div>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-border/70">
        <div
          className="h-full rounded-full bg-brand transition-[width] duration-500 ease-out"
          style={{ width: `${Math.max(6, Math.min(96, progress))}%` }}
        />
      </div>
      <div className="mt-2 text-[11.5px] text-ink-meta">{tr(stageKey)}</div>
    </section>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-2 flex items-center gap-1.5 text-[12.5px] font-semibold text-ink-heading">
      {children}
    </div>
  );
}

function SkillBody({ view, tr }: { view: MarketplaceItemDetail; tr: Tr }) {
  const meta: { k: string; v: string }[] = [];
  if (view.version) meta.push({ k: tr("marketplace.modalVersion"), v: view.version });
  if (view.owner) meta.push({ k: tr("marketplace.modalSource"), v: view.owner });
  if (view.updated_at) meta.push({ k: tr("marketplace.modalUpdated"), v: view.updated_at });
  if (view.stats?.downloads != null)
    meta.push({ k: tr("marketplace.modalDownloads"), v: formatCount(view.stats.downloads) });
  const fileStructure = summarizeFileStructure(view.files ?? []);
  return (
    <div>
      <MetaRow meta={meta} />
      <SkillOverviewCard view={view} tr={tr} />
      <EvaluationReportCard view={view} tr={tr} />
      <SecurityReportCard view={view} tr={tr} />
      <FileStructureCard
        rows={fileStructure}
        total={view.files?.length ?? 0}
        tr={tr}
      />
    </div>
  );
}

function SkillOverviewCard({ view, tr }: { view: MarketplaceItemDetail; tr: Tr }) {
  const tags = [
    view.category_label,
    ...(view.subcategories ?? []),
  ].filter((tag): tag is string => !!tag);
  const needsApiKey = view.badges.includes("requires_api_key");
  return (
    <section className="mb-5 rounded-lg border border-surface-border bg-surface px-3.5 py-3">
      <SectionTitle>
        <FileText className="h-3.5 w-3.5 text-ink-meta" />
        {tr("marketplace.modalSkillOverview")}
      </SectionTitle>
      {needsApiKey ? (
        <div className="mb-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900">
          <KeyRound className="mt-0.5 h-3.5 w-3.5 flex-none" />
          <div>
            <span className="font-medium">{tr("marketplace.badgeRequiresApiKey")}</span>
            <span className="ml-1">{tr("marketplace.modalRequiresApiKeyHint")}</span>
          </div>
        </div>
      ) : null}
      <div className="text-sm leading-relaxed text-ink-body">
        {view.description || tr("marketplace.modalSkillOverviewNone")}
      </div>
      {tags.length ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {tags.slice(0, 8).map((tag) => (
            <span
              key={tag}
              className="rounded-md border border-surface-border bg-surface-soft px-2 py-1 text-2xs text-ink-meta"
            >
              {tag}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function SecurityReportCard({ view, tr }: { view: MarketplaceItemDetail; tr: Tr }) {
  return (
    <section className="mb-5 rounded-lg border border-surface-border bg-surface px-3.5 py-3">
      <SectionTitle>
        <ShieldCheck className="h-3.5 w-3.5 text-success" />
        {tr("marketplace.modalSecurityReport")}
      </SectionTitle>
      <div className="text-[12.5px] leading-relaxed text-ink-body">
        {view.security?.summary || tr("marketplace.modalSecurityNone")}
      </div>
      {view.security?.reports?.length ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {view.security.reports.map((r) => (
            <a
              key={r.provider}
              href={r.url ?? undefined}
              target={r.url ? "_blank" : undefined}
              rel={r.url ? "noreferrer" : undefined}
              className="inline-flex items-center gap-1 rounded-md border border-surface-border bg-surface-soft px-2 py-1 text-xs text-ink-heading transition-colors hover:border-brand/40 hover:text-brand"
            >
              {r.url ? <Link2 className="h-3 w-3" /> : <ShieldCheck className="h-3 w-3" />}
              {r.provider}
              <span className="text-ink-muted">{r.status}</span>
            </a>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function FileStructureCard({
  rows,
  total,
  tr,
}: {
  rows: FileSummaryRow[];
  total: number;
  tr: Tr;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <SectionTitle>
          <Folder className="h-3.5 w-3.5 text-ink-meta" />
          {tr("marketplace.modalFilesPreview")}
        </SectionTitle>
        <span className="text-2xs tabular-nums text-ink-muted">
          {tr("marketplace.modalFilesCount", { count: total })}
        </span>
      </div>
      <div className="overflow-hidden rounded-lg border border-surface-border bg-surface">
        {rows.length ? (
          rows.map((row) => (
            <div
              key={row.label}
              className="flex items-start gap-2.5 border-b border-surface-border px-3 py-2.5 last:border-b-0"
            >
              {row.dir ? (
                <Folder className="mt-0.5 h-3.5 w-3.5 flex-none text-ink-meta" />
              ) : (
                <FileText className="mt-0.5 h-3.5 w-3.5 flex-none text-ink-meta" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 truncate font-mono text-xs text-ink-heading">
                    {row.label}
                  </span>
                  <span className="text-2xs tabular-nums text-ink-muted">
                    {tr("marketplace.modalFilesCount", { count: row.count })}
                  </span>
                  {row.size != null ? (
                    <span className="text-2xs tabular-nums text-ink-muted">
                      {formatSize(row.size)}
                    </span>
                  ) : null}
                </div>
                {row.examples.length ? (
                  <div className="mt-0.5 truncate font-mono text-2xs text-ink-muted">
                    {row.examples.join(" · ")}
                  </div>
                ) : null}
              </div>
            </div>
          ))
        ) : (
          <div className="px-3 py-3 text-xs text-ink-meta">
            {tr("marketplace.modalFilesCount", { count: 0 })}
          </div>
        )}
      </div>
    </section>
  );
}

function EvaluationReportCard({ view, tr }: { view: MarketplaceItemDetail; tr: Tr }) {
  const report = view.evaluation;
  if (!report) return null;
  const scoreLabel =
    report.score != null ? report.score.toFixed(1) : tr("marketplace.modalEvaluationUnknown");
  return (
    <section className="mb-5 rounded-lg border border-brand/20 bg-brand-light/40 px-3.5 py-3">
      <div className="mb-2.5 flex items-start justify-between gap-3">
        <SectionTitle>
          <Activity className="h-3.5 w-3.5 text-brand" />
          {tr("marketplace.modalEvaluationReport")}
        </SectionTitle>
        <div className="flex-none text-right">
          <div className="text-[20px] font-semibold leading-none text-ink-heading">
            {scoreLabel}
            <span className="ml-1 text-xs font-normal text-ink-meta">/ 5</span>
          </div>
          {report.rating ? (
            <div className="mt-1 text-2xs font-medium text-brand">{report.rating}</div>
          ) : null}
        </div>
      </div>
      {report.summary ? (
        <div className="mb-3 line-clamp-3 text-[12.5px] leading-relaxed text-ink-body">
          {report.summary}
        </div>
      ) : null}
      {report.dimensions.length ? (
        <div className="grid gap-2 sm:grid-cols-2">
          {report.dimensions.map((dimension) => {
            const value = dimension.score ?? 0;
            const pct = Math.max(0, Math.min(100, (value / 5) * 100));
            return (
              <div key={dimension.key} className="min-w-0">
                <div className="mb-1 flex items-center gap-1.5">
                  <span className="flex h-4 w-4 items-center justify-center rounded-full bg-brand text-[9px] font-semibold text-white">
                    {dimension.code}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-heading">
                    {dimension.label}
                  </span>
                  {dimension.score != null ? (
                    <span className="text-2xs tabular-nums text-ink-meta">
                      {dimension.score.toFixed(1)}
                    </span>
                  ) : null}
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-surface-border">
                  <div className="h-full rounded-full bg-brand" style={{ width: `${pct}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

interface FileSummaryRow {
  label: string;
  size: number | null;
  /** Directory row (aggregated) vs root summary. */
  dir: boolean;
  count: number;
  examples: string[];
}

/** Collapse the flat file list into a compact root/top-level directory
 * structure. The preview should explain package shape, not dump every file. */
function summarizeFileStructure(
  files: { path: string; size?: number | null }[],
): FileSummaryRow[] {
  const root = { count: 0, size: 0, sized: false, examples: [] as string[] };
  const dirs = new Map<
    string,
    { count: number; size: number; sized: boolean; examples: string[] }
  >();
  for (const f of files) {
    const slash = f.path.indexOf("/");
    if (slash === -1) {
      root.count += 1;
      if (f.size != null) {
        root.size += f.size;
        root.sized = true;
      }
      root.examples.push(f.path);
      continue;
    }
    const dir = f.path.slice(0, slash);
    const entry = dirs.get(dir) ?? { count: 0, size: 0, sized: false, examples: [] };
    entry.count += 1;
    if (f.size != null) {
      entry.size += f.size;
      entry.sized = true;
    }
    entry.examples.push(f.path.slice(slash + 1));
    dirs.set(dir, entry);
  }

  const sortExamples = (items: string[]) =>
    items
      .sort((a, b) =>
        a === "SKILL.md" ? -1 : b === "SKILL.md" ? 1 : a.localeCompare(b),
      )
      .slice(0, 4);

  const rows: FileSummaryRow[] = [];
  if (root.count > 0) {
    rows.push({
      label: "/",
      size: root.sized ? root.size : null,
      dir: false,
      count: root.count,
      examples: sortExamples(root.examples),
    });
  }

  const dirRows: FileSummaryRow[] = [...dirs.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([dir, v]) => ({
      label: `${dir}/`,
      size: v.sized ? v.size : null,
      dir: true,
      count: v.count,
      examples: sortExamples(v.examples),
    }));
  return [...rows, ...dirRows];
}

function AgentBody({
  view,
  meta,
  tr,
}: {
  view: MarketplaceItemDetail;
  meta: { k: string; v: string }[];
  tr: Tr;
}) {
  const connectorTagKey = {
    required: "marketplace.connectorRequired",
    optional: "marketplace.connectorOptional",
    api_key: "marketplace.connectorApiKey",
    cost: "marketplace.connectorCost",
  } as const;
  return (
    <div>
      <MetaRow meta={meta} />
      <SectionTitle>{tr("marketplace.modalRoleInstructions")}</SectionTitle>
      <div className="mb-5 whitespace-pre-wrap rounded-lg border border-surface-border bg-surface-soft px-3.5 py-3 text-[12.5px] leading-relaxed text-ink-body">
        {view.instructions}
      </div>
      {view.bound_skills?.length ? (
        <>
          <SectionTitle>
            {tr("marketplace.modalBoundSkills")} · {view.bound_skills.length}
          </SectionTitle>
          <div className="mb-5 flex flex-wrap gap-1.5">
            {view.bound_skills.map((s) => (
              <span
                key={s}
                className="inline-flex items-center gap-1.5 rounded-md border border-surface-border bg-surface-soft px-2 py-1 text-xs text-ink-heading"
              >
                {s}
              </span>
            ))}
          </div>
        </>
      ) : null}
      {view.connectors?.length ? (
        <>
          <SectionTitle>{tr("marketplace.modalConnectors")}</SectionTitle>
          <div className="flex flex-col gap-1.5">
            {view.connectors.map((c) => (
              <div
                key={c.name}
                className="flex items-center gap-2.5 rounded-lg border border-surface-border px-3 py-2"
              >
                <Link2 className="h-3.5 w-3.5 flex-none text-ink-meta" />
                <span className="flex-1 text-[12.5px] text-ink-heading">{c.name}</span>
                <span className="rounded bg-surface-soft px-1.5 py-px text-micro font-medium text-ink-meta">
                  {tr(connectorTagKey[c.requirement])}
                </span>
              </div>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

function TeamBody({ view, tr }: { view: MarketplaceItemDetail; tr: Tr }) {
  return (
    <div>
      {view.instructions && view.instructions !== view.description ? (
        <section className="mb-5 rounded-lg border border-surface-border bg-surface px-3.5 py-3">
          <SectionTitle>{tr("marketplace.modalCollaboration")}</SectionTitle>
          <div className="text-[12.5px] leading-relaxed text-ink-body">{view.instructions}</div>
        </section>
      ) : null}
      {view.workflow?.length ? (
        <section className="mb-5 rounded-lg border border-surface-border bg-surface px-3.5 py-3">
          <SectionTitle>{tr("marketplace.modalWorkflow")}</SectionTitle>
          <div className="flex flex-col gap-2">
            {view.workflow.map((step, index) => (
              <div key={step} className="flex gap-2.5">
                <span className="flex h-5 w-5 flex-none items-center justify-center rounded-full bg-brand-light text-2xs font-semibold text-brand-700">
                  {index + 1}
                </span>
                <span className="text-[12.5px] leading-relaxed text-ink-body">{step}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
      <SectionTitle>
        {tr("marketplace.modalMembers")} ·{" "}
        {tr("marketplace.modalMembersCount", { count: view.members?.length ?? 0 })}
      </SectionTitle>
      <div className="flex flex-col gap-2">
        {(view.members ?? []).map((m) => {
          const tint = tintFor(m.name);
          return (
            <div
              key={m.name}
              className="flex items-center gap-3 rounded-lg border border-surface-border px-3 py-2.5"
            >
              <div
                className="flex h-8 w-8 flex-none items-center justify-center rounded-full text-xs font-semibold"
                style={{ background: tint.bg, color: tint.fg }}
              >
                {m.name.slice(0, 1)}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-semibold text-ink-heading">{m.name}</span>
                  <span
                    className="rounded px-1.5 py-px text-micro font-medium"
                    style={
                      m.lead
                        ? { background: "var(--brand-light)", color: "var(--brand-700)" }
                        : { background: "var(--surface-soft)", color: "var(--ink-meta)" }
                    }
                  >
                    {m.lead ? tr("marketplace.roleLead") : tr("marketplace.roleMember")}
                  </span>
                </div>
                <div className="mt-0.5 truncate text-[11.5px] text-ink-body">{m.role}</div>
              </div>
              {m.skill_count != null ? (
                <span className="text-2xs tabular-nums text-ink-muted">
                  {tr("marketplace.memberSkills", { count: m.skill_count })}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TeamActivationBody({
  view,
  tr,
  projects,
  projectMode,
  selectedProjectId,
  newProjectName,
  newProjectRootPath,
  onProjectModeChange,
  onProjectChange,
  onNewProjectNameChange,
  onSelectProjectRoot,
  onRefreshProjects,
}: {
  view: MarketplaceItemDetail;
  tr: Tr;
  projects: ProjectListItem[];
  projectMode: "new" | "existing";
  selectedProjectId: string;
  newProjectName: string;
  newProjectRootPath: string;
  onProjectModeChange: (mode: "new" | "existing") => void;
  onProjectChange: (projectId: string) => void;
  onNewProjectNameChange: (name: string) => void;
  onSelectProjectRoot: () => void;
  onRefreshProjects: () => void;
}) {
  const members = view.members ?? [];
  const lead = members.find((m) => m.lead) ?? members[0];
  const createNew = projectMode === "new";
  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-brand/20 bg-brand-light/40 px-3.5 py-3">
        <div className="mb-2 flex items-center gap-1.5 text-[12.5px] font-semibold text-ink-heading">
          <MessageSquare className="h-3.5 w-3.5 text-brand" />
          {tr("marketplace.teamLaunchTitle")}
        </div>
        <div className="text-[12.5px] leading-relaxed text-ink-body">
          {tr("marketplace.teamLaunchHint", {
            count: members.length,
            lead: lead?.name ?? tr("marketplace.roleLead"),
          })}
        </div>
      </section>

      <section>
        <SectionTitle>{tr("marketplace.teamLaunchDestination")}</SectionTitle>
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr]">
          <button
            type="button"
            onClick={() => {
              onProjectModeChange("new");
              onProjectChange("");
            }}
            className={`rounded-lg border px-3 py-3 text-left transition-colors ${
              createNew
                ? "border-brand bg-brand-light/50 text-ink-heading"
                : "border-surface-border bg-surface text-ink-body hover:border-brand/40"
            }`}
          >
            <div className="text-sm font-semibold">
              {tr("marketplace.teamLaunchNewProject")}
            </div>
            <div className="mt-1 text-[11.5px] text-ink-meta">
              {tr("marketplace.teamLaunchNewProjectHint")}
            </div>
          </button>
          <button
            type="button"
            onClick={() => {
              onProjectModeChange("existing");
              onRefreshProjects();
              onProjectChange(projects[0]?.id ?? "");
            }}
            className={`rounded-lg border px-3 py-3 text-left transition-colors ${
              !createNew
                ? "border-brand bg-brand-light/50 text-ink-heading"
                : "border-surface-border bg-surface text-ink-body hover:border-brand/40"
            }`}
          >
            <div className="text-sm font-semibold">
              {tr("marketplace.teamLaunchExistingProject")}
            </div>
            <div className="mt-1 text-[11.5px] text-ink-meta">
              {tr("marketplace.teamLaunchExistingProjectHint")}
            </div>
          </button>
        </div>
      </section>

      {createNew ? (
        <div className="grid gap-3">
          <section>
            <SectionTitle>{tr("marketplace.teamLaunchProjectName")}</SectionTitle>
            <input
              value={newProjectName}
              onChange={(event) => onNewProjectNameChange(event.target.value)}
              placeholder={tr("marketplace.teamLaunchProjectName")}
              className="h-9 w-full rounded-md border border-surface-border bg-surface px-2.5 text-sm text-ink-heading outline-none transition-colors placeholder:text-ink-muted focus:border-brand"
            />
          </section>
          <section>
            <SectionTitle>{tr("marketplace.teamLaunchProjectDirectory")}</SectionTitle>
            <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
              <div
                title={newProjectRootPath}
                className="flex h-9 min-w-0 items-center truncate rounded-md border border-surface-border bg-surface px-2.5 text-sm text-ink-heading"
              >
                {newProjectRootPath || tr("marketplace.teamLaunchProjectDirectoryEmpty")}
              </div>
              <Button type="button" variant="outline" size="sm" onClick={onSelectProjectRoot}>
                <Folder className="mr-1 h-3.5 w-3.5" />
                {tr("marketplace.teamLaunchChooseDirectory")}
              </Button>
            </div>
          </section>
        </div>
      ) : (
        <section>
          <SectionTitle>{tr("marketplace.teamLaunchSelectProject")}</SectionTitle>
          <select
            value={selectedProjectId}
            onChange={(event) => onProjectChange(event.target.value)}
            onFocus={onRefreshProjects}
            className="h-9 w-full rounded-md border border-surface-border bg-surface px-2.5 text-sm text-ink-heading outline-none transition-colors focus:border-brand"
          >
            {projects.length === 0 ? (
              <option value="">{tr("marketplace.teamLaunchNoProjects")}</option>
            ) : null}
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </section>
      )}

      <section>
        <SectionTitle>
          {tr("marketplace.modalMembers")} ·{" "}
          {tr("marketplace.modalMembersCount", { count: members.length })}
        </SectionTitle>
        <div className="grid gap-2 sm:grid-cols-2">
          {members.map((member) => (
            <div
              key={member.slug ?? member.name}
              className="rounded-lg border border-surface-border bg-surface px-3 py-2"
            >
              <div className="flex items-center gap-1.5">
                <span className="truncate text-[12.5px] font-semibold text-ink-heading">
                  {member.name}
                </span>
                {member.lead ? (
                  <span className="rounded bg-brand-light px-1.5 py-px text-micro font-medium text-brand-700">
                    {tr("marketplace.roleLead")}
                  </span>
                ) : null}
              </div>
              <div className="mt-0.5 truncate text-[11.5px] text-ink-body">{member.role}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
