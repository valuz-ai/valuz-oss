import { useEffect, useMemo, useState } from "react";
import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
} from "@valuz/ui";
import {
  agentsApi,
  useTranslation,
  type Agent,
  type MemberWithAgent,
} from "@valuz/core";
import { toast } from "sonner";
import { AgentIconGlyph, pickAgentIcon } from "./agent-icons";

export interface DeployAgentsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Target project project id. */
  projectId: string;
  /** Library agents available to派驻. */
  agents: Agent[];
  /** Current members of this project (to render the已派驻 state). */
  members: MemberWithAgent[];
  /** Called after every deploy/undeploy so the caller can refresh members. */
  onChanged: () => void | Promise<void>;
  /** Optional "go create a new agent in the library" affordance. */
  onCreateNew?: () => void;
}

/**
 * v2 派驻 picker — multi-select deploy/undeploy of library agents into a
 * project. Each toggle is a live reference (deploy) or its removal (undeploy);
 * NO copy. The已派驻 state is derived by mapping a member back to its library
 * agent via the shared ``kernel_agent_id``.
 */
export const DeployAgentsDialog = ({
  open,
  onOpenChange,
  projectId,
  agents,
  members,
  onChanged,
  onCreateNew,
}: DeployAgentsDialogProps) => {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [busySlug, setBusySlug] = useState<string | null>(null);

  // The library agents are kept as local state and refreshed after every
  // deploy/undeploy. A freshly-created-then-deployed agent only gets its
  // ``kernel_agent_id`` backfilled on first deploy, so the prop snapshot (taken
  // before the deploy) would still show it unchecked — letting the user select
  // it again and hit a duplicate error. Re-fetching keeps the已派驻 state honest.
  const [liveAgents, setLiveAgents] = useState<Agent[]>(agents);
  useEffect(() => {
    setLiveAgents(agents);
  }, [agents]);

  const refresh = async () => {
    const [agentsRes] = await Promise.all([
      agentsApi.listAgents(),
      onChanged(),
    ]);
    setLiveAgents(agentsRes.agents);
  };

  const deployedKernelIds = useMemo(
    () => new Set(members.map((m) => m.member.kernel_agent_id)),
    [members],
  );
  const memberByKernelId = useMemo(
    () => new Map(members.map((m) => [m.member.kernel_agent_id, m])),
    [members],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return liveAgents;
    return liveAgents.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q),
    );
  }, [liveAgents, query]);

  const toggle = async (agent: Agent, willDeploy: boolean) => {
    setBusySlug(agent.slug);
    try {
      if (willDeploy) {
        await agentsApi.deploy(projectId, {
          source_agent_slug: agent.slug,
        });
      } else {
        const m = agent.kernel_agent_id
          ? memberByKernelId.get(agent.kernel_agent_id)
          : undefined;
        if (m) await agentsApi.deleteMember(projectId, m.member.agent_slug);
      }
      await refresh();
    } catch (err) {
      // Re-sync so the checkbox reflects reality, then surface a friendly,
      // operation-specific message (never the raw backend detail). An
      // "already deployed" race is benign once the state re-syncs.
      await refresh().catch(() => {});
      const raw = err instanceof Error ? err.message : "";
      if (willDeploy && raw.includes("already deployed")) return;
      toast.error(
        willDeploy ? t("agent.deployFailed") : t("agent.deployHolderBlocked"),
      );
    } finally {
      setBusySlug(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>{t("agent.deployPickerTitle")}</DialogTitle>
          <DialogDescription>{t("agent.deployWarning")}</DialogDescription>
        </DialogHeader>
        <div className="flex min-h-0 flex-col gap-3">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("agent.deployPickerSearch")}
          />
          <div className="-mr-1 max-h-[48vh] min-h-0 flex-1 overflow-y-auto pr-1">
            <div className="flex flex-col gap-0.5">
              {filtered.map((agent) => {
                const isDeployed =
                  !!agent.kernel_agent_id &&
                  deployedKernelIds.has(agent.kernel_agent_id);
                const icon = pickAgentIcon(agent);
                return (
                  <label
                    key={agent.slug}
                    className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 hover:bg-surface-soft"
                  >
                    <Checkbox
                      checked={isDeployed}
                      disabled={busySlug === agent.slug}
                      onCheckedChange={(c) => void toggle(agent, c === true)}
                    />
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-body">
                      {icon ? (
                        <AgentIconGlyph icon={icon} className="h-4 w-4" />
                      ) : null}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-medium text-ink-heading">
                          {agent.name}
                        </span>
                        {isDeployed && (
                          <Badge
                            variant="secondary"
                            className="shrink-0 px-1.5 py-0 text-[10px] font-normal"
                          >
                            {t("agent.deployed")}
                          </Badge>
                        )}
                        <Badge
                          variant="outline"
                          className="shrink-0 px-1.5 py-0 text-[10px] font-normal text-ink-meta"
                        >
                          {agent.source === "official"
                            ? t("agent.groupOfficial")
                            : t("agent.sourceMine")}
                        </Badge>
                      </div>
                      {agent.description && (
                        <p className="truncate text-xs text-ink-meta">
                          {agent.description}
                        </p>
                      )}
                    </div>
                  </label>
                );
              })}
              {filtered.length === 0 && (
                <p className="px-2 py-6 text-center text-xs text-ink-meta">
                  {t("agent.noMembers")}
                </p>
              )}
            </div>
          </div>
          {onCreateNew && (
            <button
              type="button"
              className="self-start border-t border-surface-border pt-2 text-xs text-primary hover:underline"
              onClick={onCreateNew}
            >
              {t("agent.deployCreateLink")}
            </button>
          )}
        </div>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>
            {t("common.done")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
