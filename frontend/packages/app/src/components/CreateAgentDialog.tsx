import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  ChevronDown,
  ChevronRight,
  MessageSquare,
  Plus,
  Send,
} from "lucide-react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogField,
  DialogFooter,
  DialogHeader,
  DialogInput,
  DialogTitle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from "@valuz/ui";
import {
  agentsApi,
  connectorsApi,
  skillsApi,
  projectsApi,
  useModelDefaults,
  useTranslation,
  type Agent,
  type ConnectorItem,
  type EffortLevel,
  type SkillView,
  type ProjectListItem,
} from "@valuz/core";
import { AgentModelPicker, type AgentModelSelection } from "./AgentModelPicker";
import { CatalogPickerDialog } from "./CatalogPickerDialog";
import { AVATAR_PRESETS, AgentIconGlyph, getAvatarIcon } from "./agent-icons";

const DEFAULT_MODEL: AgentModelSelection = {
  runtime: "claude_agent",
  providerId: null,
  model: "claude-sonnet-4-6",
};
const DEFAULT_EFFORT: EffortLevel = "high";
const EFFORT_LEVELS: EffortLevel[] = ["low", "medium", "high", "xhigh", "max"];

type Step = "form" | "next";

export interface CreateAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * When set, pre-fills the form from an existing agent (列表「复制」). The new
   * agent is an independent copy — nothing is linked back to the source.
   */
  seed?: Agent | null;
  /**
   * Called when the user finishes via 「完成」 (or dismisses the next-step
   * card). Receives the new agent's slug so the caller can refresh / select it.
   * NOT called when the user navigates away via 派驻 / 去临时对话.
   */
  onCreated?: (slug: string) => void | Promise<void>;
}

/**
 * Create a brand-new agent into the "My agents" library via a single-page
 * guided form (基本信息 → 指令 → 增强能力). On success it switches to a
 * 「下一步」card (派驻到项目 / 去临时对话 / 完成) instead of silently closing.
 * Shared by AgentsPage and ConversationPage.
 */
export const CreateAgentDialog = ({
  open,
  onOpenChange,
  seed,
  onCreated,
}: CreateAgentDialogProps) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { defaults } = useModelDefaults();

  const [step, setStep] = useState<Step>("form");

  // ── Form fields ──
  const [name, setName] = useState("");
  const [tagline, setTagline] = useState("");
  const [avatar, setAvatar] = useState<string | null>(null);
  const [model, setModel] = useState<AgentModelSelection>(DEFAULT_MODEL);
  const [effort, setEffort] = useState<EffortLevel>(DEFAULT_EFFORT);
  const [instructions, setInstructions] = useState("");
  const [skills, setSkills] = useState<string[]>([]);
  const [connectors, setConnectors] = useState<string[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  // ── Catalogs for the 增强能力 pickers ──
  const [skillCatalog, setSkillCatalog] = useState<SkillView[]>([]);
  const [connectorCatalog, setConnectorCatalog] = useState<ConnectorItem[]>([]);
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const [connectorPickerOpen, setConnectorPickerOpen] = useState(false);

  // ── Next-step state ──
  const [createdSlug, setCreatedSlug] = useState<string | null>(null);
  const [createdName, setCreatedName] = useState("");
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [targetProject, setTargetProject] = useState("");
  const [deploying, setDeploying] = useState(false);
  // Suppresses the onCreated callback when we close after navigating away.
  const navigatedRef = useRef(false);

  const reset = useCallback(() => {
    setStep("form");
    setName("");
    setTagline("");
    setAvatar(null);
    setModel(DEFAULT_MODEL);
    setEffort(DEFAULT_EFFORT);
    setInstructions("");
    setSkills([]);
    setConnectors([]);
    setAdvancedOpen(false);
    setCreatedSlug(null);
    setCreatedName("");
    setTargetProject("");
  }, []);

  // Seed the form from a copy source each time the dialog opens with a seed.
  // Props → form-state sync on open is a deliberate effect; re-seeding on every
  // ``seed`` identity change would clobber the user's in-progress edits.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!open) return;
    if (seed) {
      setName(`${seed.name} (copy)`);
      setTagline(seed.description);
      setAvatar(seed.avatar);
      setModel({
        runtime: seed.runtime,
        providerId: seed.provider_id,
        model: seed.model,
      });
      setEffort(seed.effort ?? DEFAULT_EFFORT);
      setInstructions(seed.instructions);
      setSkills(seed.skills);
      setConnectors(seed.connector_types);
      setAdvancedOpen(
        seed.skills.length > 0 || seed.connector_types.length > 0,
      );
    } else if (defaults) {
      // Blank create defaults to the user's global model / effort settings —
      // not a hardcoded model. Keep every entry point pointing at one source.
      setModel({
        runtime: defaults.default_runtime,
        providerId: defaults.default_provider_id,
        model: defaults.default_model ?? "",
      });
      setEffort(defaults.default_effort);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, defaults]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Load skill + connector catalogs once the dialog opens.
  useEffect(() => {
    if (!open) return;
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
  }, [open]);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) {
        if (step === "next" && createdSlug && !navigatedRef.current) {
          void onCreated?.(createdSlug);
        }
        navigatedRef.current = false;
        reset();
      }
      onOpenChange(next);
    },
    [step, createdSlug, onCreated, reset, onOpenChange],
  );

  const submitCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      const created = await agentsApi.createAgent({
        name: trimmed,
        description: tagline.trim(),
        instructions: instructions.trim(),
        runtime: model.runtime,
        model: model.model,
        provider_id: model.providerId,
        skills,
        connector_types: connectors,
        effort,
        avatar,
      });
      setCreatedSlug(created.slug);
      setCreatedName(created.name);
      // Load deploy targets for the next-step card.
      const wsRes = await projectsApi
        .list()
        .catch(() => ({ projects: [] }));
      const projs = wsRes.projects.filter((w) => w.kind === "project");
      setProjects(projs);
      setTargetProject(projs[0]?.id ?? "");
      setStep("next");
    } catch {
      toast.error(t("agent.saveFailed" as Parameters<typeof t>[0]));
    } finally {
      setBusy(false);
    }
  };

  const handleDeploy = async () => {
    if (!createdSlug || !targetProject) return;
    setDeploying(true);
    try {
      await agentsApi.deploy(targetProject, { source_agent_slug: createdSlug });
      const proj = projects.find((p) => p.id === targetProject);
      toast.success(
        t("agent.deployedToProject" as Parameters<typeof t>[0], {
          project: proj?.name ?? "",
        }),
      );
      // Don't navigate away — just confirm and close, leaving the user where
      // they were (library / chat). handleOpenChange fires onCreated to refresh.
      handleOpenChange(false);
    } catch {
      toast.error(t("agent.instantiateFailed"));
    } finally {
      setDeploying(false);
    }
  };

  const handleGoChat = () => {
    if (!createdSlug) return;
    navigatedRef.current = true;
    navigate(`/?agent=${encodeURIComponent(createdSlug)}`);
    onOpenChange(false);
    reset();
  };

  const handleDone = () => handleOpenChange(false);

  const previewIcon = getAvatarIcon(avatar);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto border-0 sm:max-w-2xl [&_label]:mb-1.5 [&_label]:font-medium [&_label]:text-[#131313]">
        {step === "form" ? (
          <>
            <DialogHeader>
              <DialogTitle>
                {t("agent.createAgent" as Parameters<typeof t>[0])}
              </DialogTitle>
              <p className="mt-1 text-xs leading-5 text-ink-meta">
                {t("agent.createSubtitle" as Parameters<typeof t>[0])}
              </p>
            </DialogHeader>

            {/* ① 基本信息 */}
            <div className="flex flex-col gap-3">
              <div className="text-xs font-semibold text-ink-meta">
                {t("agent.sectionBasic" as Parameters<typeof t>[0])}
              </div>
              <div className="flex items-start gap-3">
                {/* Avatar preset trigger + inline preset row */}
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs">
                    {t("agent.avatarLabel" as Parameters<typeof t>[0])}
                  </label>
                  <div className="flex flex-wrap gap-1.5">
                    {AVATAR_PRESETS.map(({ key, icon: Icon }) => {
                      const isActive = avatar === key;
                      return (
                        <button
                          key={key}
                          type="button"
                          aria-label={key}
                          aria-pressed={isActive}
                          onClick={() => setAvatar(isActive ? null : key)}
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
                </div>
              </div>

              <DialogField label={t("agent.name")} required>
                <DialogInput
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoFocus
                />
              </DialogField>

              <DialogField
                label={t("agent.tagline" as Parameters<typeof t>[0])}
              >
                <DialogInput
                  value={tagline}
                  onChange={(e) => setTagline(e.target.value)}
                  placeholder={t(
                    "agent.taglinePlaceholder" as Parameters<typeof t>[0],
                  )}
                />
              </DialogField>

              <DialogField
                label={t("agent.modelChannel" as Parameters<typeof t>[0])}
              >
                <AgentModelPicker value={model} onChange={setModel} />
              </DialogField>

              <DialogField label={t("agent.effortLabel")}>
                <Select
                  value={effort}
                  onValueChange={(v) => setEffort(v as EffortLevel)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {EFFORT_LEVELS.map((e) => (
                      <SelectItem key={e} value={e}>
                        {t(`effort.${e}` as Parameters<typeof t>[0])}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </DialogField>
            </div>

            {/* ② 指令 */}
            <DialogField label={t("agent.tabInstructions")}>
              <Textarea
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                rows={8}
                placeholder={t(
                  "agent.instructionsPlaceholder" as Parameters<typeof t>[0],
                )}
                className="text-xs leading-6"
              />
            </DialogField>

            {/* ③ 增强能力 (可选, 可折叠) */}
            <div className="rounded-lg border border-surface-border">
              <button
                type="button"
                onClick={() => setAdvancedOpen((v) => !v)}
                className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
              >
                {advancedOpen ? (
                  <ChevronDown className="h-4 w-4 text-ink-meta" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-ink-meta" />
                )}
                <span className="text-sm font-medium text-ink-heading">
                  {t("agent.sectionEnhance" as Parameters<typeof t>[0])}
                </span>
                <span className="text-xs text-ink-meta">
                  {t("agent.sectionEnhanceHint" as Parameters<typeof t>[0])}
                </span>
              </button>
              {advancedOpen && (
                <div className="flex flex-col gap-2 border-t border-surface-border px-3 py-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-ink-body">
                      {t("agent.tabSkills")}
                      <span className="ml-1.5 text-xs text-ink-meta">
                        {t("agent.selectedCount" as Parameters<typeof t>[0], {
                          count: skills.length,
                        })}
                      </span>
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setSkillPickerOpen(true)}
                    >
                      <Plus className="mr-1 h-3.5 w-3.5" />
                      {t("agent.addSkill" as Parameters<typeof t>[0])}
                    </Button>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-ink-body">
                      {t("agent.tabConnectors")}
                      <span className="ml-1.5 text-xs text-ink-meta">
                        {t("agent.selectedCount" as Parameters<typeof t>[0], {
                          count: connectors.length,
                        })}
                      </span>
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setConnectorPickerOpen(true)}
                    >
                      <Plus className="mr-1 h-3.5 w-3.5" />
                      {t("agent.addConnector" as Parameters<typeof t>[0])}
                    </Button>
                  </div>
                </div>
              )}
            </div>

            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => handleOpenChange(false)}
                disabled={busy}
              >
                {t("common.cancel")}
              </Button>
              <Button onClick={submitCreate} disabled={busy || !name.trim()}>
                {t("agent.createAgent" as Parameters<typeof t>[0])}
              </Button>
            </DialogFooter>
          </>
        ) : (
          /* ── 创建成功后的「下一步」引导 ── */
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                {previewIcon && (
                  <AgentIconGlyph icon={previewIcon} className="h-5 w-5" />
                )}
                {t("agent.createdTitle" as Parameters<typeof t>[0], {
                  name: createdName,
                })}
              </DialogTitle>
              <p className="mt-1 text-xs text-ink-meta">
                {t("agent.nextStepQuestion" as Parameters<typeof t>[0])}
              </p>
            </DialogHeader>

            <div className="flex flex-col gap-2">
              {/* 派驻到项目 */}
              <div className="rounded-lg border border-surface-border p-3">
                <div className="flex items-center gap-2">
                  <Send className="h-4 w-4 text-ink-meta" />
                  <span className="text-sm font-medium text-ink-heading">
                    {t("agent.instantiate")}
                  </span>
                </div>
                <p className="mt-0.5 pl-6 text-xs text-ink-meta">
                  {t("agent.nextDeployDesc" as Parameters<typeof t>[0])}
                </p>
                <div className="mt-2 flex items-center gap-2 pl-6">
                  {projects.length === 0 ? (
                    <span className="text-xs text-ink-meta">
                      {t("agent.noProjects")}
                    </span>
                  ) : (
                    <>
                      <Select
                        value={targetProject}
                        onValueChange={setTargetProject}
                      >
                        <SelectTrigger size="sm" className="h-8 flex-1 text-xs">
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
                      <Button
                        size="sm"
                        onClick={handleDeploy}
                        disabled={deploying || !targetProject}
                      >
                        {t("agent.instantiateSubmit")}
                      </Button>
                    </>
                  )}
                </div>
              </div>

              {/* 去临时对话 */}
              <button
                type="button"
                onClick={handleGoChat}
                className="flex items-center justify-between rounded-lg border border-surface-border p-3 text-left transition-colors hover:border-surface-border-hover hover:bg-surface-soft"
              >
                <div>
                  <div className="flex items-center gap-2">
                    <MessageSquare className="h-4 w-4 text-ink-meta" />
                    <span className="text-sm font-medium text-ink-heading">
                      {t("agent.nextChatTitle" as Parameters<typeof t>[0])}
                    </span>
                  </div>
                  <p className="mt-0.5 pl-6 text-xs text-ink-meta">
                    {t("agent.nextChatDesc" as Parameters<typeof t>[0])}
                  </p>
                </div>
                <ChevronRight className="h-4 w-4 shrink-0 text-ink-meta" />
              </button>
            </div>

            <DialogFooter>
              <Button variant="outline" onClick={handleDone}>
                {t("common.done")}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>

      {/* 增强能力 pickers — in-memory selection (no API write until 创建). */}
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
        selected={skills}
        onCommit={setSkills}
      />
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
        selected={connectors}
        onCommit={setConnectors}
      />
    </Dialog>
  );
};
