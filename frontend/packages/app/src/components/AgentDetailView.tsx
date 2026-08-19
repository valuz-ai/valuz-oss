import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft,
  BookOpen,
  Bot,
  ChevronRight,
  KeyRound,
  Plug,
  Plus,
  Trash2,
} from "lucide-react";
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
  StatusPill,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
  statusDotClass,
} from "@valuz/ui";
import {
  agentsApi,
  channelsApi,
  connectorsApi,
  skillsApi,
  useResourceGuard,
  projectsApi,
  useTranslation,
  type Agent,
  type AgentDeployment,
  type CatalogEntry,
  type ConnectorItem,
  type EffortLevel,
  type EffectiveAgentResource,
  type EffectiveAgentResources,
  type FeishuBinding,
  type SkillView,
  type UpdateAgentPayload,
  type ProjectListItem,
  type WeComAIBotBinding,
} from "@valuz/core";
import { modelLabel } from "@valuz/shared";
import { AgentModelPicker, type AgentModelSelection } from "./AgentModelPicker";
import { reconcileDraft, sameBrain } from "./agent-draft-sync";
import { AgentDetailCopyActions } from "./AgentDetailCopyActions";
import { CatalogPickerDialog } from "./CatalogPickerDialog";
import { ExportPackDialog } from "./ExportPackDialog";
import { ResourceDetailActionSlot } from "./ResourceActionSlot";
import { useOptionalProjectOutlet } from "../layout";
import {
  AVATAR_PRESETS,
  AgentIconGlyph,
  getAvatarIcon,
  pickAgentIcon,
} from "./agent-icons";

// Connector status → i18n key for the colored status pill — mirrors the
// Connectors page so the agent's connector list reads the same. The two
// "configured but not connected" states (pending_auth / unknown) read as
// "未连接"; a bound-but-not-installed connector is treated as pending_auth too.
const STATUS_LABEL_KEY: Record<string, string> = {
  connected: "connector.statusConnected",
  connecting: "connector.statusConnecting",
  error: "connector.statusError",
  pending_auth: "connector.statusNotConnected",
  unknown: "connector.statusNotConnected",
};

interface ConnectorMeta {
  display_name: string;
  description: string | null;
}

function ReadonlyResourceList({
  items,
  emptyText,
}: {
  items: EffectiveAgentResource[];
  emptyText: string;
}) {
  if (items.length === 0) {
    return (
      <div className="rounded-[14px] border border-dashed border-surface-border bg-card px-4 py-6 text-center text-xs text-ink-meta">
        {emptyText}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {items.map((item) => (
        <div
          key={`${item.source}:${item.id}`}
          className="flex items-center gap-3 rounded-[14px] bg-card p-3 shadow-[var(--shadow-1)]"
        >
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-heading">
            {item.name}
          </span>
          <span className="shrink-0 text-2xs text-ink-meta">{item.status}</span>
        </div>
      ))}
    </div>
  );
}

/** Flatten the connector directory (groups + standalone) into slug → meta, so
 *  a mounted connector_type resolves to its name + description even when the
 *  user hasn't configured it (e.g. template connectors like ``wind-mcp``). */
function buildConnectorDir(items: CatalogEntry[]): Map<string, ConnectorMeta> {
  const map = new Map<string, ConnectorMeta>();
  for (const item of items) {
    if (item.kind === "group") {
      for (const c of item.connectors) {
        map.set(c.slug, {
          display_name: c.display_name,
          description: c.description,
        });
      }
    } else {
      map.set(item.slug, {
        display_name: item.display_name,
        description: item.description,
      });
    }
  }
  return map;
}

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
  // Rendered both as a full page (inside the project outlet) and inside the
  // agent-library master-detail right panel, which the layout mounts in its
  // aside slot — OUTSIDE any ``<Outlet context>`` — so the outlet may be
  // absent. Guard the header wiring instead of destructuring ``undefined``.
  const outlet = useOptionalProjectOutlet();
  const setHeader = outlet?.setHeader;
  const setHeaderClassName = outlet?.setHeaderClassName;
  const setContentInnerClassName = outlet?.setContentInnerClassName;

  const [agent, setAgent] = useState<Agent | null>(null);
  const [effectiveResources, setEffectiveResources] =
    useState<EffectiveAgentResources | null>(null);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [deployments, setDeployments] = useState<AgentDeployment[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [targetProject, setTargetProject] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
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
  const [skillPickerCatalog, setSkillPickerCatalog] = useState<SkillView[]>([]);
  const [connectorCatalog, setConnectorCatalog] = useState<ConnectorItem[]>([]);
  // Full connector list (every status), so the connectors tab can show each
  // bound connector's status pill — not just the connected ones.
  const [allConnectors, setAllConnectors] = useState<ConnectorItem[]>([]);
  // True once the connector list has loaded, so the connectors-tab dot doesn't
  // flash a false positive before we know which connectors are connected.
  const [connectorsLoaded, setConnectorsLoaded] = useState(false);
  // Full connector directory (slug → name/description), so a mounted
  // connector_type the user hasn't configured (e.g. a template's wind-mcp)
  // still resolves to a readable name + description, not a bare slug.
  const [connectorDir, setConnectorDir] = useState<Map<string, ConnectorMeta>>(
    new Map(),
  );
  const [aibotBinding, setAibotBinding] = useState<WeComAIBotBinding | null>(
    null,
  );
  const [aibotEnabled, setAibotEnabled] = useState(true);
  const [aibotBotId, setAibotBotId] = useState("");
  const [aibotSecret, setAibotSecret] = useState("");
  const [savingChannel, setSavingChannel] = useState(false);
  const [feishuBinding, setFeishuBinding] = useState<FeishuBinding | null>(
    null,
  );
  const [feishuEnabled, setFeishuEnabled] = useState(true);
  const [feishuAppId, setFeishuAppId] = useState("");
  const [feishuAppSecret, setFeishuAppSecret] = useState("");
  const [savingFeishuChannel, setSavingFeishuChannel] = useState(false);
  const [testingFeishuChannel, setTestingFeishuChannel] = useState(false);

  const { canDelete } = useResourceGuard(agent ?? {});

  const pageHeader = useMemo(() => {
    if (!onBack) return null;
    return (
      <div className="flex min-w-0 items-center gap-2 text-sm leading-5">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex shrink-0 items-center gap-1 text-ink-meta transition-colors hover:text-ink-heading"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          <span>{backLabel ?? t("agent.back")}</span>
        </button>
        {agent ? (
          <>
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
            <span className="min-w-0 truncate font-medium text-ink-heading">
              {agent.name}
            </span>
          </>
        ) : null}
      </div>
    );
  }, [agent, backLabel, onBack, t]);

  useEffect(() => {
    // No project outlet (e.g. the agent-library right panel) → nothing to
    // drive; the panel owns its own chrome.
    if (
      !pageHeader ||
      !setHeader ||
      !setHeaderClassName ||
      !setContentInnerClassName
    ) {
      return;
    }
    setHeader(pageHeader);
    setHeaderClassName("h-auto px-5 py-5");
    setContentInnerClassName("p-0");
    return () => {
      setHeader(null);
      setHeaderClassName(undefined);
      setContentInnerClassName(undefined);
    };
  }, [pageHeader, setContentInnerClassName, setHeader, setHeaderClassName]);

  const loadData = useCallback(async () => {
    try {
      const [tpl, wsRes, depRes, channelRes, feishuChannelRes, resources] =
        await Promise.all([
          agentsApi.getAgent(slug),
          projectsApi.list(),
          agentsApi.listDeployments(slug),
          channelsApi.getWeComAIBotBinding(slug).catch(() => null),
          channelsApi.getFeishuBinding(slug).catch(() => null),
          agentsApi.getEffectiveResources(slug).catch(() => null),
        ]);
      setAgent(tpl);
      setEffectiveResources(resources);
      setProjects(wsRes.projects.filter((w) => w.kind === "project"));
      setDeployments(depRes.deployments);
      setAibotBinding(channelRes);
      if (channelRes) {
        setAibotEnabled(
          channelRes.agent_slug === slug ? channelRes.enabled : true,
        );
        setAibotBotId(channelRes.bot_id);
        setAibotSecret("");
      }
      setFeishuBinding(feishuChannelRes);
      if (feishuChannelRes) {
        setFeishuEnabled(
          feishuChannelRes.agent_slug === slug
            ? feishuChannelRes.enabled
            : true,
        );
        setFeishuAppId(feishuChannelRes.app_id);
        setFeishuAppSecret("");
      }
    } catch {
      toast.error(t("common.error"));
    } finally {
      setLoading(false);
    }
  }, [slug, t]);

  useEffect(() => {
    void Promise.resolve().then(loadData);
  }, [loadData]);

  // Values the drafts were last seeded from. A draft still equal to its seed
  // is pristine and may be refreshed; one that has diverged is an edit in
  // progress and belongs to the user.
  const seededRef = useRef<{
    slug: string;
    instructions: string;
    name: string;
    description: string;
    avatar: string | null;
    brain: AgentModelSelection;
    effort: string;
  } | null>(null);

  // Re-seed the per-tab drafts whenever the loaded agent changes — a deliberate
  // data → draft-state sync, not derived-during-render state.
  //
  // Every save re-fetches the agent so the other tabs see fresh data, and each
  // fetch is a new object, so this runs far more often than "the user opened a
  // different agent". Writing the response into every draft unconditionally
  // threw away whatever was half-typed elsewhere: save the inheritance switch
  // — or just keep typing while the instructions save is still in flight — and
  // the reply reverted the textarea to the stored version. So each field is
  // reconciled on its own, and only a pristine one is refreshed.
  useEffect(() => {
    if (!agent) return;
    const seeded = seededRef.current;
    const incoming = {
      slug: agent.slug,
      instructions: agent.instructions,
      name: agent.name,
      description: agent.description,
      avatar: agent.avatar,
      brain: {
        runtime: agent.runtime,
        providerId: agent.provider_id,
        model: agent.model,
      },
      effort: agent.effort ?? "high",
    };
    // A different agent in the same mounted view (the full-page route swaps
    // ``slug`` without remounting) — those drafts belong to the previous one.
    const agentChanged = seeded === null || seeded.slug !== incoming.slug;
    const opts = { agentChanged };

    setInstrDraft((cur) =>
      reconcileDraft(
        cur,
        seeded?.instructions ?? cur,
        incoming.instructions,
        opts,
      ),
    );
    setNameDraft((cur) =>
      reconcileDraft(cur, seeded?.name ?? cur, incoming.name, opts),
    );
    setDescDraft((cur) =>
      reconcileDraft(
        cur,
        seeded?.description ?? cur,
        incoming.description,
        opts,
      ),
    );
    setAvatarDraft((cur) =>
      reconcileDraft(cur, seeded?.avatar ?? cur, incoming.avatar, opts),
    );
    setBrainDraft((cur) =>
      reconcileDraft(cur, seeded?.brain ?? cur, incoming.brain, {
        ...opts,
        isEqual: sameBrain,
      }),
    );
    setEffortDraft((cur) =>
      reconcileDraft(cur, seeded?.effort ?? cur, incoming.effort, opts),
    );
    seededRef.current = incoming;
  }, [agent]);

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
      const [skillRes, enabledSkillRes, connRes, dirRes] = await Promise.all([
        skillsApi
          .list()
          .catch(() => ({ project_id: "", skills: [] as SkillView[] })),
        skillsApi
          .list(undefined, { libraryEnabled: true })
          .catch(() => ({ project_id: "", skills: [] as SkillView[] })),
        connectorsApi
          .list()
          .catch(() => ({ connectors: [] as ConnectorItem[] })),
        connectorsApi
          .listDirectory()
          .catch(() => ({ items: [] as CatalogEntry[] })),
      ]);
      if (cancelled) return;
      setSkillCatalog(skillRes.skills);
      setSkillPickerCatalog(enabledSkillRes.skills);
      setConnectorCatalog(
        connRes.connectors.filter((c) => c.enabled && c.status === "connected"),
      );
      setAllConnectors(connRes.connectors);
      setConnectorDir(buildConnectorDir(dirRes.items));
      setConnectorsLoaded(true);
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
      const copy = await agentsApi.copyAgent(agent.slug);
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
      // Confirmed delete cascades: 解除 every 派驻 first so the user doesn't have
      // to undeploy from each project by hand. The dialog already warns when the
      // agent is deployed (see deployments count below).
      await agentsApi.deleteAgent(agent.slug, { cascade: true });
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

  const saveAibotBinding = async () => {
    if (!agent) return;
    const botId = aibotBotId.trim();
    const secret = aibotSecret.trim();
    if (!botId) {
      toast.error(
        t("agent.wecomAibotBotIdRequired" as Parameters<typeof t>[0]),
      );
      return;
    }
    if (!aibotBinding?.has_secret && !secret) {
      toast.error(
        t("agent.wecomAibotSecretRequired" as Parameters<typeof t>[0]),
      );
      return;
    }
    setSavingChannel(true);
    try {
      const next = await channelsApi.updateWeComAIBotBinding({
        enabled: aibotEnabled,
        agent_slug: agent.slug,
        bot_id: botId,
        secret,
      });
      setAibotBinding(next);
      setAibotSecret("");
      toast.success(t("agent.wecomAibotSaved" as Parameters<typeof t>[0]));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(
        `${t("agent.saveFailed" as Parameters<typeof t>[0])}: ${msg}`,
      );
    } finally {
      setSavingChannel(false);
    }
  };

  const saveFeishuBinding = async (enabledOverride?: boolean) => {
    if (!agent) return;
    const enabled = enabledOverride ?? feishuEnabled;
    const appId = feishuAppId.trim();
    const appSecret = feishuAppSecret.trim();
    if (!appId) {
      toast.error(t("agent.feishuAppIdRequired" as Parameters<typeof t>[0]));
      return;
    }
    if (!feishuBinding?.has_app_secret && !appSecret) {
      toast.error(
        t("agent.feishuAppSecretRequired" as Parameters<typeof t>[0]),
      );
      return;
    }
    setSavingFeishuChannel(true);
    try {
      const next = await channelsApi.updateFeishuBinding({
        enabled,
        agent_slug: agent.slug,
        app_id: appId,
        app_secret: appSecret,
      });
      setFeishuBinding(next);
      setFeishuEnabled(next.enabled);
      setFeishuAppSecret("");
      toast.success(t("agent.feishuSaved" as Parameters<typeof t>[0]));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(
        `${t("agent.saveFailed" as Parameters<typeof t>[0])}: ${msg}`,
      );
    } finally {
      setSavingFeishuChannel(false);
    }
  };

  // The enable switch persists immediately — a toggle that only lives in
  // component state silently reverts on the next app restart (the stored
  // binding still carries the old flag). Only possible once a binding with a
  // stored secret exists; before that the switch just stages the initial save.
  const toggleFeishuEnabled = (checked: boolean) => {
    setFeishuEnabled(checked);
    if (!agent || savingFeishuChannel) return;
    const persistable =
      feishuBinding?.agent_slug === agent.slug &&
      feishuBinding.has_app_secret &&
      feishuAppId.trim().length > 0;
    if (!persistable) return;
    void (async () => {
      setSavingFeishuChannel(true);
      try {
        const next = await channelsApi.updateFeishuBinding({
          enabled: checked,
          agent_slug: agent.slug,
          app_id: feishuAppId.trim(),
          app_secret: feishuAppSecret.trim(),
        });
        setFeishuBinding(next);
        setFeishuEnabled(next.enabled);
      } catch (err) {
        setFeishuEnabled(!checked);
        const msg = err instanceof Error ? err.message : String(err);
        toast.error(
          `${t("agent.feishuEnableFailed" as Parameters<typeof t>[0])}: ${msg}`,
        );
      } finally {
        setSavingFeishuChannel(false);
      }
    })();
  };

  const testFeishuBinding = async () => {
    if (!agent) return;
    setTestingFeishuChannel(true);
    try {
      const result = await channelsApi.testFeishuBinding(agent.slug);
      if (result.credential_ok) {
        toast.success(
          t("agent.feishuTestCredentialOk" as Parameters<typeof t>[0]),
        );
      } else {
        toast.error(
          `${t("agent.feishuTestFailed" as Parameters<typeof t>[0])}: ${
            result.error ?? result.connection_error ?? ""
          }`,
        );
      }
      // Refresh the stored binding so the status line reflects the probe.
      const fresh = await channelsApi
        .getFeishuBinding(agent.slug)
        .catch(() => null);
      if (fresh) setFeishuBinding(fresh);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(
        `${t("agent.feishuTestFailed" as Parameters<typeof t>[0])}: ${msg}`,
      );
    } finally {
      setTestingFeishuChannel(false);
    }
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

  const isSystem = agent.kind === "system";
  const skillCount = isSystem
    ? (effectiveResources?.counts.skills ?? 0)
    : agent.skills.length;
  const connectorCount = isSystem
    ? (effectiveResources?.counts.connectors ?? 0)
    : agent.connector_types.length;
  const displayedSkills = isSystem
    ? (effectiveResources?.skills ?? []).map((resource) => {
        const reference = resource.slug || resource.id;
        const meta = skillCatalog.find(
          (item) =>
            item.path === reference ||
            item.slug === reference ||
            item.id === reference ||
            item.id === resource.id,
        );
        return {
          key: `${resource.source}:${resource.id}`,
          reference,
          name: resource.name,
          identifier: resource.slug || resource.id,
          description: meta?.description,
          catalogId: meta?.id,
          removable: false,
        };
      })
    : agent.skills.map((reference) => {
        // Mounted skills are stored as a path (picker) or a slug
        // (templates) — match either, plus the catalog id, so the
        // name + description always resolve.
        const meta = skillCatalog.find(
          (item) =>
            item.path === reference ||
            item.slug === reference ||
            item.id === reference,
        );
        return {
          key: reference,
          reference,
          name: meta?.name ?? skillName(reference),
          identifier: meta?.slug ?? skillName(reference),
          description: meta?.description,
          catalogId: meta?.id,
          removable: true,
        };
      });
  const displayedConnectors = isSystem
    ? (effectiveResources?.connectors ?? []).map((resource) => {
        const connectorSlug = resource.slug || resource.id;
        const installed = allConnectors.find(
          (item) => item.slug === connectorSlug,
        );
        const meta: ConnectorMeta | undefined = installed
          ? {
              display_name: installed.display_name,
              description: installed.description,
            }
          : connectorDir.get(connectorSlug);
        return {
          key: `${resource.source}:${resource.id}`,
          slug: connectorSlug,
          name: resource.name,
          description: meta?.description,
          status: resource.status,
          navigable: Boolean(meta),
          removable: false,
        };
      })
    : agent.connector_types.map((connectorSlug) => {
        // An installed connector resolves its name + status from the full
        // list; an uninstalled bound slug (a template's unconfigured
        // wind-mcp, etc.) still resolves its name from the directory and
        // counts as "not connected".
        const installed = allConnectors.find(
          (item) => item.slug === connectorSlug,
        );
        const meta: ConnectorMeta | undefined = installed
          ? {
              display_name: installed.display_name,
              description: installed.description,
            }
          : connectorDir.get(connectorSlug);
        return {
          key: connectorSlug,
          slug: connectorSlug,
          name: meta?.display_name ?? connectorSlug,
          description: meta?.description,
          status: installed?.status ?? "pending_auth",
          navigable: Boolean(meta),
          removable: true,
        };
      });

  // Red dot on the 连接器 tab when a bound connector isn't connected — the same
  // "needs attention" idea as the Connectors nav dot, scoped to this agent's
  // own connector_types. ``connectorCatalog`` holds the connected connectors,
  // so any bound slug missing from it (pending_auth / error / not installed) is
  // "没有链接好的".
  const connectedSlugs = new Set(connectorCatalog.map((c) => c.slug));
  const hasUnconnectedConnector =
    !isSystem &&
    connectorsLoaded &&
    agent.connector_types.some((slug) => !connectedSlugs.has(slug));
  const aibotBoundToThisAgent =
    aibotBinding?.enabled === true && aibotBinding.agent_slug === agent.slug;
  const feishuRuntimeStatus = feishuBinding?.connected
    ? {
        status: "connected",
        label: t("agent.feishuConnected" as Parameters<typeof t>[0]),
      }
    : feishuBinding?.connection_status === "connecting"
      ? {
          status: "running",
          label: t("agent.feishuConnecting" as Parameters<typeof t>[0]),
        }
      : {
          status: "disconnected",
          label: t("agent.feishuDisconnected" as Parameters<typeof t>[0]),
        };
  const aibotRuntimeStatus = aibotBinding?.connected
    ? {
        status: "connected",
        label: t("agent.wecomAibotConnected" as Parameters<typeof t>[0]),
      }
    : aibotBinding?.connection_status === "connecting"
      ? {
          status: "running",
          label: t("agent.wecomAibotConnecting" as Parameters<typeof t>[0]),
        }
      : {
          status: "disconnected",
          label: t("agent.wecomAibotDisconnected" as Parameters<typeof t>[0]),
        };

  const fullPage = !!onBack;

  return (
    <div
      className={
        fullPage
          ? "mx-auto h-full min-h-0 w-[760px] max-w-full pb-12"
          : "mx-auto max-w-4xl pb-12"
      }
    >
      {/* ── Identity — flat section, editable in place. Icon + name +
          subtitle on the left, action buttons right-aligned on the same
          row (Skills detail layout). */}
      <div
        className={
          fullPage
            ? "border-b border-surface-border px-5 pt-0 pb-4"
            : "border-b border-surface-border px-5 py-4"
        }
      >
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
              onClick={() => {
                if (!isSystem) setEditingField("avatar");
              }}
              disabled={isSystem}
              aria-label={t("agent.avatarLabel" as Parameters<typeof t>[0])}
              className="group relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-body transition-colors enabled:hover:bg-brand/10"
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
                onClick={() => {
                  if (!isSystem) setEditingField("name");
                }}
                disabled={isSystem}
                className="group block max-w-full rounded px-1 py-0.5 text-left text-base font-medium text-ink-heading transition-colors enabled:hover:bg-surface-soft"
              >
                <span className="flex items-center gap-2 truncate">
                  {agent.name}
                  {isSystem ? (
                    <span className="inline-flex h-5 shrink-0 items-center rounded-sm bg-brand-light px-1.5 text-micro font-normal text-brand">
                      {t("agent.systemBadge" as Parameters<typeof t>[0])}
                    </span>
                  ) : null}
                </span>
              </button>
            )}
            {/* Subtitle (Skills-panel style): plain ``来源 · 模型 ·
                    推理强度`` text — no badge pill. */}
            {editingField !== "name" && (
              <div className="mt-0.5 truncate px-1 text-xs text-ink-body">
                {[
                  isSystem
                    ? t("agent.groupSystem" as Parameters<typeof t>[0])
                    : agent.source === "official"
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
            <AgentDetailCopyActions
              resource={agent as unknown as Record<string, unknown>}
              isSystem={isSystem}
              onExport={() => setExportOpen(true)}
              onCopy={() => setCopyConfirmOpen(true)}
            />
            {canDelete && agent.deletable && (
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
            <ResourceDetailActionSlot
              resourceType="agent"
              resource={agent as unknown as Record<string, unknown>}
            />
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
              onClick={() => {
                if (!isSystem) setEditingField("description");
              }}
              disabled={isSystem}
              className="block w-full rounded px-1 py-0.5 text-left text-sm leading-relaxed text-ink-body transition-colors enabled:hover:bg-surface-soft"
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
            {t("agent.skillsLabel")} {skillCount}
          </span>
          <span className="text-ink-muted">·</span>
          <span>
            {t("agent.tabConnectors")} {connectorCount}
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
                <span className="relative inline-block">
                  {t("agent.tabConnectors")}
                  {hasUnconnectedConnector ? (
                    <span className="absolute -right-2 -top-0.5 h-1.5 w-1.5 rounded-full bg-[#f54b4b]" />
                  ) : null}
                </span>
              </TabsTrigger>
              {isSystem ? (
                <TabsTrigger value="knowledge">
                  {t("agent.tabKnowledge" as Parameters<typeof t>[0])}
                </TabsTrigger>
              ) : null}
              <TabsTrigger value="channels">
                {t("agent.tabChannels" as Parameters<typeof t>[0])}
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="instructions" className="mt-4">
            {isSystem ? (
              <div className="rounded-[14px] border border-surface-border bg-card p-4 text-sm leading-6 text-ink-body">
                <div className="font-medium text-ink-heading">
                  {t(
                    "agent.valurionInstructionsTitle" as Parameters<
                      typeof t
                    >[0],
                  )}
                </div>
                <p className="mt-1 text-xs text-ink-meta">
                  {t(
                    "agent.valurionInstructionsHint" as Parameters<typeof t>[0],
                  )}
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="flex items-start justify-between gap-4 rounded-[14px] border border-surface-border bg-card p-4">
                  <div>
                    <div className="text-sm font-medium text-ink-heading">
                      {t(
                        "agent.inheritValurionInstructions" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </div>
                    <p className="mt-1 text-xs leading-5 text-ink-meta">
                      {t(
                        "agent.inheritValurionInstructionsHint" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </p>
                  </div>
                  <Switch
                    checked={agent.inherit_global_instructions}
                    disabled={savingTab === "inheritance"}
                    onCheckedChange={(checked) =>
                      void saveFields("inheritance", {
                        inherit_global_instructions: checked,
                      })
                    }
                  />
                </div>
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
            )}
          </TabsContent>

          <TabsContent value="skills" className="mt-4">
            <div className="flex items-center justify-between">
              <p className="text-xs leading-5 text-ink-meta">
                {isSystem
                  ? t(
                      "agent.allAvailableResourcesHint" as Parameters<
                        typeof t
                      >[0],
                    )
                  : t("agent.skillsSectionHint" as Parameters<typeof t>[0])}
              </p>
              {!isSystem ? (
                <Button size="sm" variant="outline" onClick={openSkillPicker}>
                  <Plus className="mr-1 h-3.5 w-3.5" />
                  {t("agent.addSkill" as Parameters<typeof t>[0])}
                </Button>
              ) : null}
            </div>
            <div className="mt-3 flex flex-col gap-2">
              {displayedSkills.length === 0 ? (
                <div className="rounded-[14px] border border-dashed border-surface-border bg-card px-4 py-6 text-center text-xs text-ink-meta">
                  {isSystem
                    ? t("agent.noEffectiveResources" as Parameters<typeof t>[0])
                    : t("agent.noSkills")}
                </div>
              ) : (
                displayedSkills.map((skill) => {
                  const catalogId = skill.catalogId;
                  const body = (
                    <>
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-medium text-ink-heading">
                          {skill.name}
                        </span>
                      </div>
                      {skill.identifier !== skill.name && (
                        <div className="mt-0.5 truncate font-mono text-2xs text-ink-meta">
                          {skill.identifier}
                        </div>
                      )}
                      {skill.description && (
                        <div className="mt-0.5 line-clamp-2 text-xs text-ink-meta">
                          {skill.description}
                        </div>
                      )}
                    </>
                  );
                  return (
                    <div
                      key={skill.key}
                      className="flex items-start gap-3 rounded-[14px] bg-card p-3 shadow-[var(--shadow-1)] transition-colors"
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-meta">
                        <BookOpen className="h-4 w-4" />
                      </span>
                      {catalogId ? (
                        <button
                          type="button"
                          onClick={() =>
                            // Open the skill in the 技能库 (master-detail panel),
                            // not the standalone detail page.
                            navigate(
                              `/skills?skill=${encodeURIComponent(catalogId)}`,
                            )
                          }
                          className="min-w-0 flex-1 cursor-pointer text-left"
                        >
                          {body}
                        </button>
                      ) : (
                        <div className="min-w-0 flex-1">{body}</div>
                      )}
                      {skill.removable ? (
                        <button
                          type="button"
                          onClick={() => toggleSkill(skill.reference)}
                          aria-label={t("common.delete")}
                          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-surface-soft hover:text-[#f54b4b]"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      ) : null}
                    </div>
                  );
                })
              )}
            </div>
          </TabsContent>

          <TabsContent value="connectors" className="mt-4">
            <div className="flex items-center justify-between">
              <p className="text-xs leading-5 text-ink-meta">
                {isSystem
                  ? t(
                      "agent.allAvailableResourcesHint" as Parameters<
                        typeof t
                      >[0],
                    )
                  : t("agent.connectorsSectionHint" as Parameters<typeof t>[0])}
              </p>
              {!isSystem ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={openConnectorPicker}
                >
                  <Plus className="mr-1 h-3.5 w-3.5" />
                  {t("agent.addConnector" as Parameters<typeof t>[0])}
                </Button>
              ) : null}
            </div>
            <div className="mt-3 flex flex-col gap-2">
              {displayedConnectors.length === 0 ? (
                <div className="rounded-[14px] border border-dashed border-surface-border bg-card px-4 py-6 text-center text-xs text-ink-meta">
                  {isSystem
                    ? t("agent.noEffectiveResources" as Parameters<typeof t>[0])
                    : t("agent.noConnectors")}
                </div>
              ) : (
                displayedConnectors.map((connector) => {
                  const statusKey = STATUS_LABEL_KEY[connector.status];
                  const body = (
                    <>
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-medium text-ink-heading">
                          {connector.name}
                        </span>
                      </div>
                      {connector.name !== connector.slug && (
                        <div className="mt-0.5 truncate font-mono text-2xs text-ink-meta">
                          {connector.slug}
                        </div>
                      )}
                      {connector.description && (
                        <div className="mt-0.5 line-clamp-2 text-xs text-ink-meta">
                          {connector.description}
                        </div>
                      )}
                    </>
                  );
                  return (
                    <div
                      key={connector.key}
                      className="flex items-center gap-3 rounded-[14px] bg-card p-3 shadow-[var(--shadow-1)] transition-colors"
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-meta">
                        <Plug className="h-4 w-4" />
                      </span>
                      {connector.navigable ? (
                        <button
                          type="button"
                          onClick={() => navigate("/connectors")}
                          className="min-w-0 flex-1 cursor-pointer text-left"
                        >
                          {body}
                        </button>
                      ) : (
                        <div className="min-w-0 flex-1">{body}</div>
                      )}
                      {/* Pill + delete grouped tightly (gap-2.5) so they read
                          together, matching the Connectors page list rows. */}
                      <div className="flex shrink-0 items-center gap-2.5">
                        {statusKey ? (
                          <StatusPill
                            status={connector.status}
                            label={t(statusKey as Parameters<typeof t>[0])}
                            className="shrink-0 px-1.5 py-0 text-micro leading-4"
                          />
                        ) : null}
                        {connector.removable ? (
                          <button
                            type="button"
                            onClick={() => toggleConnector(connector.slug)}
                            aria-label={t("common.delete")}
                            className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-soft hover:text-[#f54b4b]"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        ) : null}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </TabsContent>

          {isSystem ? (
            <TabsContent value="knowledge" className="mt-4">
              <p className="mb-3 text-xs leading-5 text-ink-meta">
                {t(
                  "agent.allAvailableResourcesHint" as Parameters<typeof t>[0],
                )}
              </p>
              <ReadonlyResourceList
                items={effectiveResources?.knowledge_bases ?? []}
                emptyText={t(
                  "agent.noEffectiveResources" as Parameters<typeof t>[0],
                )}
              />
            </TabsContent>
          ) : null}

          <TabsContent value="channels" className="mt-4">
            <div className="flex flex-col gap-3">
              <div className="rounded-[14px] bg-card p-4 shadow-[var(--shadow-1)]">
                <div className="flex items-start gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-meta">
                    <Bot className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-medium text-ink-heading">
                        {t("agent.wecomAibotTitle" as Parameters<typeof t>[0])}
                      </h3>
                      {aibotBoundToThisAgent ? (
                        <StatusPill
                          status="ok"
                          label={t(
                            "agent.wecomAibotBound" as Parameters<typeof t>[0],
                          )}
                          className="px-1.5 py-0 text-micro leading-4"
                        />
                      ) : null}
                      {aibotBoundToThisAgent ? (
                        <StatusPill
                          status={aibotRuntimeStatus.status}
                          label={aibotRuntimeStatus.label}
                          className="px-1.5 py-0 text-micro leading-4"
                        />
                      ) : null}
                    </div>
                  </div>
                  <Switch
                    checked={aibotEnabled}
                    onCheckedChange={setAibotEnabled}
                    aria-label={t(
                      "agent.wecomAibotEnabled" as Parameters<typeof t>[0],
                    )}
                  />
                </div>

                <div className="mt-4 flex flex-col gap-3">
                  <label className="flex flex-col gap-1.5">
                    <span className="text-xs font-medium text-ink-heading">
                      {t("agent.wecomAibotBotId" as Parameters<typeof t>[0])}
                    </span>
                    <Input
                      value={aibotBotId}
                      onChange={(e) => setAibotBotId(e.target.value)}
                      placeholder="aib..."
                      className="font-mono text-xs"
                    />
                  </label>
                  <label className="flex flex-col gap-1.5">
                    <span className="text-xs font-medium text-ink-heading">
                      {t("agent.wecomAibotSecret" as Parameters<typeof t>[0])}
                    </span>
                    <div className="relative">
                      <KeyRound className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
                      <Input
                        type="password"
                        value={aibotSecret}
                        onChange={(e) => setAibotSecret(e.target.value)}
                        placeholder={
                          aibotBinding?.has_secret
                            ? t(
                                "agent.wecomAibotSecretSaved" as Parameters<
                                  typeof t
                                >[0],
                              )
                            : ""
                        }
                        className="pl-8 font-mono text-xs"
                      />
                    </div>
                  </label>
                  <div className="flex justify-end">
                    <Button
                      size="sm"
                      disabled={
                        savingChannel ||
                        !aibotBotId.trim() ||
                        (!aibotBinding?.has_secret && !aibotSecret.trim())
                      }
                      onClick={() => void saveAibotBinding()}
                    >
                      {t("agent.wecomAibotBind" as Parameters<typeof t>[0])}
                    </Button>
                  </div>
                </div>
              </div>
              <div className="rounded-[14px] bg-card p-4 shadow-[var(--shadow-1)]">
                <div className="flex items-start gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-meta">
                    <BookOpen className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-medium text-ink-heading">
                      {t("agent.feishuTitle" as Parameters<typeof t>[0])}
                    </h3>
                    {feishuBinding?.agent_slug === agent.slug ? (
                      // Dot + text: the enabled state is already implied by
                      // the status label, so no extra "bound" pill.
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-body">
                        <span
                          className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(
                            !feishuBinding.enabled
                              ? "stopped"
                              : feishuBinding.connected
                                ? "connected"
                                : feishuBinding.connection_status ===
                                    "connecting"
                                  ? "connecting"
                                  : "error",
                          )}`}
                        />
                        <span>
                          {feishuBinding.enabled
                            ? feishuRuntimeStatus.label
                            : t(
                                "agent.feishuNotEnabled" as Parameters<
                                  typeof t
                                >[0],
                              )}
                        </span>
                        {feishuBinding.enabled &&
                        feishuBinding.connection_error ? (
                          <span className="truncate text-ink-muted">
                            {feishuBinding.connection_error}
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  <Switch
                    checked={feishuEnabled}
                    onCheckedChange={toggleFeishuEnabled}
                    aria-label={t(
                      "agent.feishuEnabled" as Parameters<typeof t>[0],
                    )}
                  />
                </div>

                <div className="mt-4 flex flex-col gap-3">
                  <label className="flex flex-col gap-1.5">
                    <span className="text-xs font-medium text-ink-heading">
                      {t("agent.feishuAppId" as Parameters<typeof t>[0])}
                    </span>
                    <Input
                      value={feishuAppId}
                      onChange={(e) => setFeishuAppId(e.target.value)}
                      placeholder="cli_..."
                      className="font-mono text-xs"
                    />
                  </label>
                  <label className="flex flex-col gap-1.5">
                    <span className="text-xs font-medium text-ink-heading">
                      {t("agent.feishuAppSecret" as Parameters<typeof t>[0])}
                    </span>
                    <div className="relative">
                      <KeyRound className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
                      <Input
                        type="password"
                        value={feishuAppSecret}
                        onChange={(e) => setFeishuAppSecret(e.target.value)}
                        placeholder={
                          feishuBinding?.has_app_secret
                            ? t(
                                "agent.feishuSecretSaved" as Parameters<
                                  typeof t
                                >[0],
                              )
                            : ""
                        }
                        className="pl-8 font-mono text-xs"
                      />
                    </div>
                  </label>
                  <div className="flex justify-end gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={
                        testingFeishuChannel ||
                        savingFeishuChannel ||
                        feishuBinding?.agent_slug !== agent.slug ||
                        !feishuBinding?.has_app_secret
                      }
                      onClick={() => void testFeishuBinding()}
                    >
                      {t("agent.feishuTest" as Parameters<typeof t>[0])}
                    </Button>
                    <Button
                      size="sm"
                      disabled={
                        savingFeishuChannel ||
                        !feishuAppId.trim() ||
                        (!feishuBinding?.has_app_secret &&
                          !feishuAppSecret.trim())
                      }
                      onClick={() => void saveFeishuBinding()}
                    >
                      {t("agent.feishuBind" as Parameters<typeof t>[0])}
                    </Button>
                  </div>
                </div>
              </div>
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
          description={
            deployments.length > 0
              ? t("agent.deleteDeployedWarning" as Parameters<typeof t>[0], {
                  count: deployments.length,
                })
              : undefined
          }
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
        items={skillPickerCatalog.map((s) => ({
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

      {/* Export this agent — same naming dialog as the multi-select export, so
          single and group exports stay consistent (a "group of one"). */}
      {agent && (
        <ExportPackDialog
          open={exportOpen}
          onOpenChange={setExportOpen}
          agentSlugs={[agent.slug]}
          defaultName={agent.name}
        />
      )}
    </div>
  );
};
