import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, BookOpen, Copy, Plug, Plus, Trash2 } from "lucide-react";
import {
  Button,
  DeleteConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogField,
  Input,
  PageLoader,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
} from "@valuz/ui";
import {
  agentsApi,
  connectorsApi,
  skillsApi,
  useResourceGuard,
  projectsApi,
  useTranslation,
  type Agent,
  type AgentDeployment,
  type ConnectorItem,
  type EffortLevel,
  type SkillView,
  type UpdateAgentPayload,
  type ProjectListItem,
} from "@valuz/core";
import { modelLabel } from "@valuz/shared";
import { AgentModelPicker, type AgentModelSelection } from "./AgentModelPicker";
import { CatalogPickerDialog } from "./CatalogPickerDialog";
import {
  AVATAR_PRESETS,
  AgentIconGlyph,
  getAvatarIcon,
  pickAgentIcon,
} from "./agent-icons";

export interface AgentDetailViewProps {
  /** Library agent slug to render. */
  slug: string;
  /** Called after any mutation (edit / install / deploy / delete) so the
   *  hosting list/page can refresh. */
  onChanged?: () => void | Promise<void>;
  /** When provided, renders a "← back" affordance (e.g. 返回项目 / 返回智能体库). */
  onBack?: () => void;
  /** Custom label for the back affordance. Defaults to a generic "返回". */
  backLabel?: string;
}

export const AgentDetailView = ({
  slug,
  onChanged,
  onBack,
  backLabel,
}: AgentDetailViewProps) => {
  const navigate = useNavigate();
  const { t } = useTranslation();

  const [agent, setAgent] = useState<Agent | null>(null);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [deployments, setDeployments] = useState<AgentDeployment[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [targetProject, setTargetProject] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [copyConfirmOpen, setCopyConfirmOpen] = useState(false);
  const [copyBusy, setCopyBusy] = useState(false);
  // Deferred save awaiting multi-project confirmation (see saveFields).
  const [pendingSave, setPendingSave] = useState<{
    tab: string;
    fields: UpdateAgentPayload;
  } | null>(null);

  // Per-tab inline edit drafts (independent save per tab, 08-agents-module §2).
  const [instrDraft, setInstrDraft] = useState("");
  const [nameDraft, setNameDraft] = useState("");
  const [descDraft, setDescDraft] = useState("");
  const [avatarDraft, setAvatarDraft] = useState<string | null>(null);
  const [brainDraft, setBrainDraft] = useState<AgentModelSelection>({
    runtime: "claude_agent",
    providerId: null,
    model: "claude-sonnet-4-6",
  });
  const [effortDraft, setEffortDraft] = useState<string>("high");
  const [savingTab, setSavingTab] = useState<string | null>(null);
  // Identity card uses click-to-edit: only one field is active at a time.
  const [editingField, setEditingField] = useState<
    "avatar" | "name" | "description" | null
  >(null);
  // Catalog picker dialogs for skills / connectors. The picker owns the draft
  // + search; this view only toggles open and commits the result.
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const [connectorPickerOpen, setConnectorPickerOpen] = useState(false);

  // Skill + connector catalogs for the 技能 / 装备 browse sub-tabs.
  const [skillCatalog, setSkillCatalog] = useState<SkillView[]>([]);
  const [connectorCatalog, setConnectorCatalog] = useState<ConnectorItem[]>([]);

  const { canDelete } = useResourceGuard(agent ?? {});

  const loadData = useCallback(async () => {
    try {
      const [tpl, wsRes, depRes] = await Promise.all([
        agentsApi.getAgent(slug),
        projectsApi.list(),
        agentsApi.listDeployments(slug),
      ]);
      setAgent(tpl);
      setProjects(wsRes.projects.filter((w) => w.kind === "project"));
      setDeployments(depRes.deployments);
    } catch {
      toast.error(t("common.error"));
    } finally {
      setLoading(false);
    }
  }, [slug, t]);

  useEffect(() => {
    void Promise.resolve().then(loadData);
  }, [loadData]);

  // Re-seed the per-tab drafts whenever the loaded agent changes — a deliberate
  // data → draft-state sync, not derived-during-render state.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!agent) return;
    setInstrDraft(agent.instructions);
    setNameDraft(agent.name);
    setDescDraft(agent.description);
    setAvatarDraft(agent.avatar);
    setBrainDraft({
      runtime: agent.runtime,
      providerId: agent.provider_id,
      model: agent.model,
    });
    setEffortDraft(agent.effort ?? "high");
  }, [agent]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const doSave = async (tab: string, fields: UpdateAgentPayload) => {
    if (!agent) return;
    setSavingTab(tab);
    try {
      await agentsApi.updateAgent(agent.slug, fields);
      toast.success(t("agent.agentSaved" as Parameters<typeof t>[0]));
      await loadData();
      await onChanged?.();
    } catch (err) {
      // surface the underlying message so 保存失败 isn't a dead end
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(
        `${t("agent.saveFailed" as Parameters<typeof t>[0])}: ${msg}`,
      );
    } finally {
      setSavingTab(null);
    }
  };

  // Live-reference: edits hit the shared AgentConfig, so saving while the agent
  // is deployed to 2+ projects changes all of them. Gate those saves behind an
  // explicit "this affects N projects" confirmation (08-agents-module §派驻).
  const saveFields = async (tab: string, fields: UpdateAgentPayload) => {
    if (!agent) return;
    if (deployments.length >= 2) {
      setPendingSave({ tab, fields });
      return;
    }
    await doSave(tab, fields);
  };

  // Load the skill + connector catalogs once for the browse sub-tabs.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [skillRes, connRes] = await Promise.all([
        skillsApi
          .list()
          .catch(() => ({ project_id: "", skills: [] as SkillView[] })),
        connectorsApi
          .list()
          .catch(() => ({ connectors: [] as ConnectorItem[] })),
      ]);
      if (cancelled) return;
      setSkillCatalog(skillRes.skills);
      setConnectorCatalog(
        connRes.connectors.filter((c) => c.enabled && c.status === "connected"),
      );
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Immediate install/remove (writes AgentRow.skills / connector_types).
  const toggleSkill = (path: string) => {
    if (!agent) return;
    const next = agent.skills.includes(path)
      ? agent.skills.filter((s) => s !== path)
      : [...agent.skills, path];
    void saveFields("skills", { skills: next });
  };
  const toggleConnector = (slug: string) => {
    if (!agent) return;
    const next = agent.connector_types.includes(slug)
      ? agent.connector_types.filter((c) => c !== slug)
      : [...agent.connector_types, slug];
    void saveFields("connectors", { connector_types: next });
  };

  // Member slug is backend-derived from the source agent's name, unique
  // within the target project (VALUZ-AGENT-SLUG) — no client computation.

  const openInstantiate = useCallback(() => {
    setTargetProject(projects[0]?.id ?? "");
    setDialogOpen(true);
  }, [projects]);

  const submitInstantiate = async () => {
    if (!agent || !targetProject) return;
    setSubmitting(true);
    try {
      const res = await agentsApi.deploy(targetProject, {
        source_agent_slug: agent.slug,
      });
      toast.success(t("agent.instantiated", { slug: res.member.agent_slug }));
      setDialogOpen(false);
      navigate(`/projects/${encodeURIComponent(targetProject)}`);
    } catch {
      toast.error(t("agent.instantiateFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  const doCopyAgent = async () => {
    if (!agent) return;
    setCopyBusy(true);
    try {
      const copy = await agentsApi.createAgent({
        name: `${agent.name} (copy)`,
        description: agent.description,
        instructions: agent.instructions,
        runtime: agent.runtime,
        model: agent.model,
        skills: agent.skills,
        connector_types: agent.connector_types,
        provider_id: agent.provider_id,
        effort: agent.effort,
        avatar: agent.avatar,
      });
      setCopyConfirmOpen(false);
      toast.success(t("agent.agentCreated" as Parameters<typeof t>[0]));
      await onChanged?.();
      navigate(`/agents/${encodeURIComponent(copy.slug)}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(
        `${t("agent.saveFailed" as Parameters<typeof t>[0])}: ${msg}`,
      );
    } finally {
      setCopyBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!agent) return;
    try {
      await agentsApi.deleteAgent(agent.slug);
      // Close the confirm dialog before the host swaps in another agent — in
      // the master-detail panel (no onBack) the component is reused, so a
      // lingering open=true would re-surface the dialog on the next agent.
      setDeleteOpen(false);
      toast.success(t("agent.agentDeleted" as Parameters<typeof t>[0]));
      await onChanged?.();
      onBack?.();
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("common.deleteFailed" as Parameters<typeof t>[0]),
      );
    }
  };

  // Skill identifiers are stored as paths; show the friendly basename.
  const skillName = (p: string) => p.split("/").filter(Boolean).pop() ?? p;

  // Picker open/commit handlers. The CatalogPickerDialog owns the draft; commit
  // batches the save in a single PATCH (skipped when nothing changed).
  const openSkillPicker = () => setSkillPickerOpen(true);
  const commitSkillPicker = (next: string[]) => {
    if (!agent) return;
    if (
      next.length === agent.skills.length &&
      next.every((s) => agent.skills.includes(s))
    ) {
      return; // no change
    }
    void saveFields("skills", { skills: next });
  };
  const openConnectorPicker = () => setConnectorPickerOpen(true);
  const commitConnectorPicker = (next: string[]) => {
    if (!agent) return;
    if (
      next.length === agent.connector_types.length &&
      next.every((c) => agent.connector_types.includes(c))
    ) {
      return; // no change
    }
    void saveFields("connectors", { connector_types: next });
  };

  // Click-to-edit handlers for the identity card.
  const cancelIdentityEdit = () => {
    if (!agent) return;
    setNameDraft(agent.name);
    setDescDraft(agent.description);
    setAvatarDraft(agent.avatar);
    setEditingField(null);
  };
  const commitIdentityField = async (
    field: "avatar" | "name" | "description",
  ) => {
    if (!agent) return;
    const fields: UpdateAgentPayload =
      field === "avatar"
        ? { avatar: avatarDraft }
        : field === "name"
          ? { name: nameDraft.trim() || null }
          : { description: descDraft };
    setEditingField(null);
    await saveFields(field, fields);
  };

  // Live preview: honour the in-progress avatar draft before it's saved.
  const agentIcon =
    getAvatarIcon(avatarDraft) ?? (agent ? pickAgentIcon(agent) : null);

  if (loading) {
    return <PageLoader />;
  }

  if (!agent) {
    return (
      <div className="px-5 pt-6">
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1 text-xs text-ink-meta hover:text-ink-heading"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {backLabel ?? t("agent.back")}
          </button>
        )}
        <p className="mt-6 text-sm text-ink-body">{t("common.error")}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl pb-12">
      {/* Back breadcrumb — only when navigated from a project / the
          full-page route. The library master-detail (AgentsPage) passes
          no onBack, so this row is absent there. No name here: the
          identity header below owns the title (matches Skills detail). */}
      {onBack && (
        <div className="border-b border-surface-border px-5 py-3 text-sm">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex shrink-0 items-center gap-1 text-ink-meta transition-colors hover:text-ink-heading"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span>{backLabel ?? t("agent.back")}</span>
          </button>
        </div>
      )}
      {/* ── Identity — flat section, editable in place. Icon + name +
          subtitle on the left, action buttons right-aligned on the same
          row (Skills detail layout). */}
      <div className="border-b border-surface-border px-5 py-4">
        {/* Avatar picker — full-width when editing, otherwise the
                avatar sits inline in the compact header row below. */}
        {editingField === "avatar" ? (
          <div className="mb-3 w-full">
            <div className="mb-2 flex flex-wrap gap-1.5">
              {AVATAR_PRESETS.map(({ key, icon: Icon }) => {
                const isActive = avatarDraft === key;
                return (
                  <button
                    key={key}
                    type="button"
                    aria-label={key}
                    aria-pressed={isActive}
                    onClick={() => setAvatarDraft(isActive ? null : key)}
                    className={`flex h-8 w-8 items-center justify-center rounded-lg border transition-colors ${
                      isActive
                        ? "border-brand bg-brand/10 text-brand"
                        : "border-surface-border bg-surface-soft text-ink-meta hover:border-ink-meta hover:text-ink-body"
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                  </button>
                );
              })}
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={cancelIdentityEdit}>
                {t("common.cancel")}
              </Button>
              <Button
                size="sm"
                disabled={savingTab === "avatar"}
                onClick={() => commitIdentityField("avatar")}
              >
                {t("agent.save")}
              </Button>
            </div>
          </div>
        ) : null}

        {/* Compact header row (Skills-panel style): small left icon +
                name + ``来源 · 运行时 · 模型 · 推理强度`` subtitle. Icon
                size (h-9) and row alignment (items-center) match the
                Skills detail panel exactly. */}
        <div className="flex items-center gap-3">
          {editingField !== "avatar" && (
            <button
              type="button"
              onClick={() => setEditingField("avatar")}
              aria-label={t("agent.avatarLabel" as Parameters<typeof t>[0])}
              className="group relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-body transition-colors hover:bg-brand/10"
            >
              {agentIcon ? (
                <AgentIconGlyph icon={agentIcon} className="h-4 w-4" />
              ) : null}
              <span className="pointer-events-none absolute -bottom-1 -right-1 rounded-full border border-surface-border bg-card px-1 py-px text-[9px] text-ink-meta opacity-0 transition-opacity group-hover:opacity-100">
                {t("common.edit" as Parameters<typeof t>[0])}
              </span>
            </button>
          )}
          <div className="min-w-0 flex-1">
            {/* Name — click to swap with an Input. */}
            {editingField === "name" ? (
              <div className="flex flex-col gap-2">
                <Input
                  autoFocus
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void commitIdentityField("name");
                    else if (e.key === "Escape") cancelIdentityEdit();
                  }}
                  className="h-8 text-sm"
                />
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={cancelIdentityEdit}
                  >
                    {t("common.cancel")}
                  </Button>
                  <Button
                    size="sm"
                    disabled={
                      savingTab === "name" ||
                      nameDraft.trim() === agent.name ||
                      !nameDraft.trim()
                    }
                    onClick={() => commitIdentityField("name")}
                  >
                    {t("agent.save")}
                  </Button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setEditingField("name")}
                className="group block max-w-full rounded px-1 py-0.5 text-left text-base font-medium text-ink-heading transition-colors hover:bg-surface-soft"
              >
                <span className="truncate">{agent.name}</span>
              </button>
            )}
            {/* Subtitle (Skills-panel style): plain ``来源 · 模型 ·
                    推理强度`` text — no badge pill. */}
            {editingField !== "name" && (
              <div className="mt-0.5 truncate px-1 text-xs text-ink-body">
                {[
                  agent.source === "official"
                    ? t("agent.groupOfficial" as Parameters<typeof t>[0])
                    : t("agent.groupCustom" as Parameters<typeof t>[0]),
                  modelLabel(agent.model),
                  agent.effort ?? "—",
                ].join(" · ")}
              </div>
            )}
          </div>

          {/* Actions — right-aligned on the identity row. Copy / delete
              use the same plain ``h-7 w-7`` icon buttons as the Skills
              detail panel; 派驻到项目 is the agent-specific primary CTA. */}
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              onClick={() => setCopyConfirmOpen(true)}
              title={t("agent.copyAgent" as Parameters<typeof t>[0])}
              aria-label={t("agent.copyAgent" as Parameters<typeof t>[0])}
              className="flex h-7 w-7 cursor-default items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
            >
              <Copy className="h-3.5 w-3.5" />
            </button>
            {canDelete && (
              <button
                type="button"
                onClick={() => setDeleteOpen(true)}
                title={t("common.delete")}
                aria-label={t("common.delete")}
                className="flex h-7 w-7 cursor-default items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-[#f54b4b]/10 hover:text-[#f54b4b]"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            )}
            <Button
              size="sm"
              onClick={openInstantiate}
              disabled={projects.length === 0}
              className="ml-1"
            >
              {t("agent.instantiate")}
            </Button>
          </div>
        </div>

        {/* Description — p by default, click to swap with a Textarea. */}
        <div className="mt-3">
          {editingField === "description" ? (
            <div className="flex flex-col gap-2">
              <Textarea
                autoFocus
                value={descDraft}
                onChange={(e) => setDescDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") cancelIdentityEdit();
                }}
                rows={4}
                className="text-sm leading-relaxed"
              />
              <div className="flex justify-end gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={cancelIdentityEdit}
                >
                  {t("common.cancel")}
                </Button>
                <Button
                  size="sm"
                  disabled={
                    savingTab === "description" ||
                    descDraft === agent.description
                  }
                  onClick={() => commitIdentityField("description")}
                >
                  {t("agent.save")}
                </Button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setEditingField("description")}
              className="block w-full rounded px-1 py-0.5 text-left text-sm leading-relaxed text-ink-body transition-colors hover:bg-surface-soft"
            >
              {agent.description || (
                <span className="italic text-ink-muted">
                  {t("agent.descriptionPlaceholder" as Parameters<typeof t>[0])}
                </span>
              )}
            </button>
          )}
        </div>

        {/* Counts + slug + 派驻 — single meta line (no divider above; a
            vertical rule separates the deployment status from the slug). */}
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-meta">
          <span>
            {t("agent.skillsLabel")} {agent.skills.length}
          </span>
          <span className="text-ink-muted">·</span>
          <span>
            {t("agent.tabConnectors")} {agent.connector_types.length}
          </span>
          <span className="text-ink-muted">·</span>
          <span className="font-mono text-2xs">{agent.slug}</span>
          {/* vertical separator before the deployment status */}
          <span className="h-3 w-px bg-surface-border" aria-hidden />
          {deployments.length > 0 ? (
            <span className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
              {deployments.map((d, i) => {
                const name =
                  projects.find((p) => p.id === d.project_id)?.name ??
                  d.project_id;
                return (
                  <span
                    key={`${d.project_id}:${d.agent_slug}`}
                    className="flex items-center gap-1.5"
                  >
                    {i > 0 && <span className="text-ink-muted">·</span>}
                    <button
                      type="button"
                      onClick={() =>
                        navigate(
                          `/projects/${encodeURIComponent(d.project_id)}`,
                        )
                      }
                      className="text-ink-body transition-colors hover:text-ink-heading"
                    >
                      {name}
                    </button>
                  </span>
                );
              })}
            </span>
          ) : (
            <span>{t("agent.notDeployedYet")}</span>
          )}
        </div>
      </div>

      {/* ── Tabs — flat section. System line-style tabs (gray baseline +
          black active underline), same as the Activity page. */}
      <div className="px-5 py-4">
        <Tabs defaultValue="model">
          <div className="border-b border-surface-border">
            <TabsList
              variant="line"
              className="h-9 justify-start gap-4 border-0 p-0"
            >
              <TabsTrigger value="model">{t("agent.tabModel")}</TabsTrigger>
              <TabsTrigger value="instructions">
                {t("agent.tabInstructions")}
              </TabsTrigger>
              <TabsTrigger value="skills">{t("agent.tabSkills")}</TabsTrigger>
              <TabsTrigger value="connectors">
                {t("agent.tabConnectors")}
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="instructions" className="mt-4">
            <div className="flex flex-col gap-2">
              <Textarea
                value={instrDraft}
                onChange={(e) => setInstrDraft(e.target.value)}
                rows={24}
                placeholder={t(
                  "agent.instructionsPlaceholder" as Parameters<typeof t>[0],
                )}
                className="min-h-[480px] text-xs leading-6"
              />
              <div className="flex justify-end">
                <Button
                  size="sm"
                  disabled={
                    savingTab === "instructions" ||
                    instrDraft === agent.instructions
                  }
                  onClick={() =>
                    saveFields("instructions", {
                      instructions: instrDraft,
                    })
                  }
                >
                  {t("agent.save")}
                </Button>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="skills" className="mt-4">
            <div className="flex items-center justify-between">
              <p className="text-xs text-ink-meta">
                {t("agent.skillsSectionHint" as Parameters<typeof t>[0])}
              </p>
              <Button size="sm" variant="outline" onClick={openSkillPicker}>
                <Plus className="mr-1 h-3.5 w-3.5" />
                {t("agent.addSkill" as Parameters<typeof t>[0])}
              </Button>
            </div>
            <div className="mt-3 flex flex-col gap-2">
              {agent.skills.length === 0 ? (
                <div className="rounded-[14px] border border-dashed border-surface-border bg-card px-4 py-6 text-center text-xs text-ink-meta">
                  {t("agent.noSkills")}
                </div>
              ) : (
                agent.skills.map((s) => {
                  const meta = skillCatalog.find((x) => x.path === s);
                  return (
                    <div
                      key={s}
                      className="flex items-start gap-3 rounded-[14px] border border-surface-border bg-card p-3 shadow-sm transition-colors hover:border-surface-border-hover"
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-meta">
                        <BookOpen className="h-4 w-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium text-ink-heading">
                          {meta?.name ?? skillName(s)}
                        </div>
                        {meta?.description && (
                          <div className="mt-0.5 line-clamp-1 text-xs text-ink-meta">
                            {meta.description}
                          </div>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => toggleSkill(s)}
                        aria-label={t("common.delete")}
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-surface-soft hover:text-[#f54b4b]"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </TabsContent>

          <TabsContent value="connectors" className="mt-4">
            <div className="flex items-center justify-between">
              <p className="text-xs text-ink-meta">
                {t("agent.connectorsSectionHint" as Parameters<typeof t>[0])}
              </p>
              <Button size="sm" variant="outline" onClick={openConnectorPicker}>
                <Plus className="mr-1 h-3.5 w-3.5" />
                {t("agent.addConnector" as Parameters<typeof t>[0])}
              </Button>
            </div>
            <div className="mt-3 flex flex-col gap-2">
              {agent.connector_types.length === 0 ? (
                <div className="rounded-[14px] border border-dashed border-surface-border bg-card px-4 py-6 text-center text-xs text-ink-meta">
                  {t("agent.noConnectors")}
                </div>
              ) : (
                agent.connector_types.map((c) => {
                  const meta = connectorCatalog.find((x) => x.slug === c);
                  return (
                    <div
                      key={c}
                      className="flex items-start gap-3 rounded-[14px] border border-surface-border bg-card p-3 shadow-sm transition-colors hover:border-surface-border-hover"
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-meta">
                        <Plug className="h-4 w-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium text-ink-heading">
                            {meta?.display_name ?? c}
                          </span>
                          <span
                            aria-hidden
                            className="h-1.5 w-1.5 rounded-full bg-emerald-500"
                          />
                        </div>
                        {meta?.description && (
                          <div className="mt-0.5 line-clamp-1 text-xs text-ink-meta">
                            {meta.description}
                          </div>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => toggleConnector(c)}
                        aria-label={t("common.delete")}
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-surface-soft hover:text-[#f54b4b]"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </TabsContent>

          <TabsContent value="model" className="mt-4">
            {
              <div className="flex flex-col gap-3">
                {/* 运行时 · 模型 · 推理强度 as settings-style rows (label +
                  description left, fixed select right, divider between) —
                  matches the global Settings → Model section. */}
                <div>
                  <AgentModelPicker
                    value={brainDraft}
                    onChange={setBrainDraft}
                    layout="rows"
                  />
                  {/* Reasoning effort — third row, same style. */}
                  <div className="flex items-center gap-4 border-b border-[#f7f8fa] py-3 dark:border-surface-border">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-ink-heading">
                        {t("agent.effortLabel")}
                      </div>
                      <div className="mt-0.5 text-xs text-ink-body">
                        {t("agent.effortDesc" as Parameters<typeof t>[0])}
                      </div>
                    </div>
                    <Select value={effortDraft} onValueChange={setEffortDraft}>
                      <SelectTrigger
                        size="sm"
                        className="h-8 w-[200px] text-xs"
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {["low", "medium", "high", "xhigh", "max"].map((e) => (
                          <SelectItem key={e} value={e}>
                            {t(`effort.${e}` as Parameters<typeof t>[0])}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="flex justify-end">
                  <Button
                    size="sm"
                    disabled={
                      savingTab === "model" ||
                      (brainDraft.runtime === agent.runtime &&
                        brainDraft.providerId === agent.provider_id &&
                        brainDraft.model === agent.model &&
                        effortDraft === (agent.effort ?? "high"))
                    }
                    onClick={() =>
                      saveFields("model", {
                        runtime: brainDraft.runtime,
                        model: brainDraft.model,
                        provider_id: brainDraft.providerId,
                        effort: effortDraft as EffortLevel,
                      })
                    }
                  >
                    {t("agent.save")}
                  </Button>
                </div>
              </div>
            }
          </TabsContent>
        </Tabs>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("agent.instantiateTitle")}</DialogTitle>
            <DialogDescription>{t("agent.instantiateDesc")}</DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-3">
            <DialogField label={t("agent.selectProject")} required>
              <Select value={targetProject} onValueChange={setTargetProject}>
                <SelectTrigger className="min-w-24">
                  <SelectValue
                    placeholder={t("agent.selectProjectPlaceholder")}
                  />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </DialogField>
            {/* Member slug is derived on the backend from the source
                agent's name, unique within the target project
                (VALUZ-AGENT-SLUG). Users don't see or edit it. */}
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={submitting}
            >
              {t("common.cancel")}
            </Button>
            <Button
              onClick={submitInstantiate}
              disabled={submitting || !targetProject}
            >
              {t("agent.instantiateSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {canDelete && (
        <DeleteConfirmDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          title={t("agent.confirmDeleteAgent" as Parameters<typeof t>[0])}
          confirmLabel={t("common.delete")}
          onConfirm={confirmDelete}
        />
      )}

      {/* Copy agent confirmation — non-destructive, so don't reuse the red
          DeleteConfirmDialog. Just a plain Dialog with two buttons. */}
      <Dialog open={copyConfirmOpen} onOpenChange={setCopyConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t("agent.copyAgent" as Parameters<typeof t>[0])}
            </DialogTitle>
            <DialogDescription>
              {t("agent.copyConfirmDesc" as Parameters<typeof t>[0], {
                name: agent.name,
              })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setCopyConfirmOpen(false)}
              disabled={copyBusy}
            >
              {t("common.cancel")}
            </Button>
            <Button onClick={() => void doCopyAgent()} disabled={copyBusy}>
              {t("common.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Skill catalog picker — shared multi-select with search. */}
      <CatalogPickerDialog
        open={skillPickerOpen}
        onOpenChange={setSkillPickerOpen}
        title={t("agent.addSkill" as Parameters<typeof t>[0])}
        description={t("agent.skillsSectionHint" as Parameters<typeof t>[0])}
        searchPlaceholder={t("agent.skillsSearch" as Parameters<typeof t>[0])}
        emptyCatalogText={t("agent.noSkillsLib" as Parameters<typeof t>[0])}
        items={skillCatalog.map((s) => ({
          id: s.path,
          name: s.name,
          description: s.description,
        }))}
        selected={agent.skills}
        onCommit={commitSkillPicker}
      />

      {/* Connector (MCP) catalog picker — shared multi-select with search. */}
      <CatalogPickerDialog
        open={connectorPickerOpen}
        onOpenChange={setConnectorPickerOpen}
        title={t("agent.addConnector" as Parameters<typeof t>[0])}
        description={t(
          "agent.connectorsSectionHint" as Parameters<typeof t>[0],
        )}
        searchPlaceholder={t(
          "agent.connectorsSearch" as Parameters<typeof t>[0],
        )}
        emptyCatalogText={t("agent.noConnectors")}
        items={connectorCatalog.map((c) => ({
          id: c.slug,
          name: c.display_name,
          description: c.description,
        }))}
        selected={agent.connector_types}
        onCommit={commitConnectorPicker}
      />

      {/* Multi-project save confirmation — live-reference blast radius. */}
      <DeleteConfirmDialog
        open={pendingSave !== null}
        onOpenChange={(v) => !v && setPendingSave(null)}
        title={t("agent.multiDeploySaveTitle" as Parameters<typeof t>[0])}
        description={t("agent.multiDeploySaveDesc" as Parameters<typeof t>[0], {
          count: deployments.length,
        })}
        confirmLabel={t("agent.save")}
        loading={pendingSave !== null && savingTab === pendingSave.tab}
        onConfirm={() => {
          if (!pendingSave) return;
          const { tab, fields } = pendingSave;
          setPendingSave(null);
          void doSave(tab, fields);
        }}
      />
    </div>
  );
};
