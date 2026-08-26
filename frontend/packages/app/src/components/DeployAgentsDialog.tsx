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
  resolveApiBase,
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
 * agent via the member's ``source_agent_slug``.
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
  // deploy/undeploy so the已派驻 state stays honest against concurrent edits.
  const [liveAgents, setLiveAgents] = useState<Agent[]>(agents);
  useEffect(() => {
    setLiveAgents(agents);
  }, [agents]);

  const refresh = async () => {
    // Re-source from the project's owning backend (same reason as the parent
    // page's library load) so post-deploy refresh stays on the right target.
    const baseUrl = resolveApiBase({ projectId }, "") || undefined;
    const [agentsRes] = await Promise.all([
      agentsApi.listAgents(undefined, baseUrl ? { baseUrl } : undefined),
      onChanged(),
    ]);
    setLiveAgents(agentsRes.agents);
  };

  const deployedSourceSlugs = useMemo(
    () =>
      new Set(members.map((m) => m.member.source_agent_slug).filter(Boolean)),
    [members],
  );
  const memberBySourceSlug = useMemo(
    () =>
      new Map(
        members
          .filter((m) => m.member.source_agent_slug)
          .map((m) => [m.member.source_agent_slug as string, m]),
      ),
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
        const m = memberBySourceSlug.get(agent.slug);
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
                const isDeployed = deployedSourceSlugs.has(agent.slug);
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
                            variant="outline"
                            className="shrink-0 px-1.5 py-0 text-micro font-normal"
                          >
                            {t("agent.deployed")}
                          </Badge>
                        )}
                        <Badge
                          variant="outline"
                          className="shrink-0 px-1.5 py-0 text-micro font-normal text-ink-meta"
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
        </div>
        <DialogFooter
          className={onCreateNew ? "sm:items-center sm:justify-between" : ""}
        >
          {onCreateNew && (
            <button
              type="button"
              className="text-xs text-primary hover:text-primary/80"
              onClick={onCreateNew}
            >
              {t("agent.deployCreateLink")}
            </button>
          )}
          <Button onClick={() => onOpenChange(false)}>
            {t("common.done")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
