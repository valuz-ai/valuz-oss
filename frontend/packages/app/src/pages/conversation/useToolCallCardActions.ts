import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  agentsApi,
  automationsApi,
  skillsApi,
  useTranslation,
  type Trigger,
  type useIncrementalTurns,
} from "@valuz/core";
import { type SkillSubmissionState } from "@valuz/ui";
import { isToolNamed, parseAutomationCreateInput } from "./tool-card-helpers";

type ToolCallCardActionsParams = {
  turns: ReturnType<typeof useIncrementalTurns>;
  isBusy: boolean;
  selectedSessionId: string | null;
  selectedSessionIdRef: { current: string | null };
  /** ``selectedSession?.name`` — labels the "bound to project" chip on
   *  confirmed submission/proposal cards. */
  selectedSessionName: string | null;
};

/**
 * Confirm/dismiss state machines behind the special-cased tool-call cards:
 * per-tool_use entries for skill submissions, ``propose_agent`` cards, and
 * ``automation create`` proposals, plus the session re-entry seeding and the
 * staging-scan poll. ``useToolCallCards`` renders from what this returns.
 */
export function useToolCallCardActions({
  turns,
  isBusy,
  selectedSessionId,
  selectedSessionIdRef,
  selectedSessionName,
}: ToolCallCardActionsParams) {
  const { t } = useTranslation();

  // Per-``submit_skill`` tool_use state — keyed by tool_use id so multiple
  // submissions in the same conversation render independently. Persists
  // for the lifetime of the page; on refresh the cards re-render in
  // their initial "pending" state, which is acceptable for v1 (the
  // backend has the staged content + library state of record).
  type SubmissionEntry = {
    state: SkillSubmissionState;
    errorMessage?: string;
    boundToProjectLabel?: string | null;
    // Live snapshot of the staged slug's contents — populated by the
    // page's scan poll. Drives both the "save" gate (we only enable the
    // save button when files are actually present) and the file tree
    // the card surfaces inline.
    stagedFiles?: {
      path: string;
      type: "file" | "directory";
      size?: number | null;
    }[];
    stagingPath?: string;
  };
  const [submissionStates, setSubmissionStates] = useState<
    Record<string, SubmissionEntry>
  >({});
  // Per-``tool.id`` state for ``propose_agent`` cards (natural-language agent
  // creation). Unlike skills there's no server-side staging — the full spec
  // rides the tool input — so the card starts ``pending`` and dismiss is
  // purely client-side.
  type ProposalEntry = {
    state:
      | "pending"
      | "confirming"
      | "confirmed"
      | "dismissing"
      | "dismissed"
      | "error";
    errorMessage?: string;
    deployedProjectLabel?: string | null;
  };
  const [proposalStates, setProposalStates] = useState<
    Record<string, ProposalEntry>
  >({});
  // Per-``tool.id`` state for ``automation create`` proposal cards. Same
  // propose→confirm model as agents; ``automationId`` is filled on confirm /
  // re-entry so a confirmed card can deep-link into the automation page.
  type AutomationProposalEntry = {
    state:
      | "pending"
      | "confirming"
      | "confirmed"
      | "dismissing"
      | "dismissed"
      | "error";
    errorMessage?: string;
    automationId?: string | null;
  };
  const [automationProposalStates, setAutomationProposalStates] = useState<
    Record<string, AutomationProposalEntry>
  >({});

  const submissionProjectLabel = useMemo(() => {
    if (selectedSessionName) return selectedSessionName;
    return null;
  }, [selectedSessionName]);

  const handleConfirmSubmission = useCallback(
    async (
      toolId: string,
      slug: string,
      summary: string | undefined,
      changeKind: "create" | "update",
      filesTouched: string[],
    ) => {
      const sid = selectedSessionIdRef.current;
      if (!sid) return;
      setSubmissionStates((prev) => ({
        ...prev,
        [toolId]: {
          ...(prev[toolId] || { state: "pending" }),
          state: "confirming",
        },
      }));
      try {
        const res = await skillsApi.confirmSubmission(sid, slug, {
          summary: summary ?? null,
          change_kind: changeKind,
          files_touched: filesTouched,
        });
        const boundLabel =
          res.bound_to_project_id && res.creation_context.kind === "project"
            ? submissionProjectLabel
            : null;
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: {
            state: "confirmed",
            boundToProjectLabel: boundLabel,
          },
        }));
        toast.success(t("skill.savedToLib" as Parameters<typeof t>[0]));
      } catch (cause) {
        const msg =
          cause instanceof Error
            ? cause.message
            : t("common.saveFailed" as Parameters<typeof t>[0]);
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: { state: "error", errorMessage: msg },
        }));
        toast.error(msg);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [submissionProjectLabel],
  );

  const handleDismissSubmission = useCallback(
    async (toolId: string, slug: string) => {
      const sid = selectedSessionIdRef.current;
      if (!sid) return;
      setSubmissionStates((prev) => ({
        ...prev,
        [toolId]: {
          ...(prev[toolId] || { state: "pending" }),
          state: "dismissing",
        },
      }));
      try {
        await skillsApi.dismissSubmission(sid, slug);
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: { state: "dismissed" },
        }));
      } catch (cause) {
        const msg =
          cause instanceof Error
            ? cause.message
            : t("conversation.cancelFailed" as Parameters<typeof t>[0]);
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: { state: "error", errorMessage: msg },
        }));
        toast.error(msg);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // Create + deploy the agent the assistant proposed via ``propose_agent``.
  // The spec is replayed from the tool input (no server staging, unlike
  // skills); the backend derives the slug and deploys into the session's
  // project when there is one.
  const handleConfirmProposal = useCallback(
    async (
      toolId: string,
      spec: {
        name: string;
        instructions: string;
        description?: string;
        runtime?: string;
        model?: string;
        skills?: string[];
        connectors?: string[];
      },
    ) => {
      const sid = selectedSessionIdRef.current;
      if (!sid) return;
      setProposalStates((prev) => ({
        ...prev,
        [toolId]: {
          ...(prev[toolId] || { state: "pending" }),
          state: "confirming",
        },
      }));
      try {
        const res = await agentsApi.confirmProposal(sid, spec);
        setProposalStates((prev) => ({
          ...prev,
          [toolId]: {
            state: "confirmed",
            deployedProjectLabel:
              res.deployed && res.project_id ? submissionProjectLabel : null,
          },
        }));
        toast.success(t("agent.proposalCreated" as Parameters<typeof t>[0]));
      } catch (cause) {
        const msg =
          cause instanceof Error
            ? cause.message
            : t("common.saveFailed" as Parameters<typeof t>[0]);
        setProposalStates((prev) => ({
          ...prev,
          [toolId]: { state: "error", errorMessage: msg },
        }));
        toast.error(msg);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [submissionProjectLabel],
  );

  const handleDismissProposal = useCallback((toolId: string) => {
    // Client-side only — nothing was written, so there's nothing to clean up.
    setProposalStates((prev) => ({
      ...prev,
      [toolId]: { state: "dismissed" },
    }));
  }, []);

  // Create the automation the assistant proposed via ``automation create``.
  // The confirmable spec is replayed from the parsed tool output; the backend
  // re-resolves project / bound-agent context from the session and stamps the
  // proposing ``tool_call_id`` so a reload can detect the row already exists.
  const handleConfirmAutomation = useCallback(
    async (
      toolId: string,
      spec: {
        name: string;
        prompt_template: string;
        trigger: Trigger;
        agent_slug?: string | null;
        action_kind?: "chat" | "task";
        worktree?: boolean;
      },
    ) => {
      const sid = selectedSessionIdRef.current;
      if (!sid) return;
      setAutomationProposalStates((prev) => ({
        ...prev,
        [toolId]: {
          ...(prev[toolId] || { state: "pending" }),
          state: "confirming",
        },
      }));
      try {
        const res = await automationsApi.confirmProposal(sid, {
          tool_call_id: toolId,
          name: spec.name,
          prompt_template: spec.prompt_template,
          trigger: spec.trigger,
          agent_slug: spec.agent_slug ?? null,
          action_kind: spec.action_kind,
          worktree: spec.worktree ?? false,
        });
        setAutomationProposalStates((prev) => ({
          ...prev,
          [toolId]: { state: "confirmed", automationId: res.automation_id },
        }));
        toast.success(
          t("automation.proposalCreated" as Parameters<typeof t>[0]),
        );
      } catch (cause) {
        const msg =
          cause instanceof Error
            ? cause.message
            : t("common.saveFailed" as Parameters<typeof t>[0]);
        setAutomationProposalStates((prev) => ({
          ...prev,
          [toolId]: { state: "error", errorMessage: msg },
        }));
        toast.error(msg);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t],
  );

  const handleDismissAutomation = useCallback((toolId: string) => {
    // Client-side only — nothing was written, so there's nothing to clean up.
    setAutomationProposalStates((prev) => ({
      ...prev,
      [toolId]: { state: "dismissed" },
    }));
  }, []);

  // Stable signature of the propose_agent tool_use ids in this session, so the
  // re-entry detection below fetches only when the set of proposal cards
  // changes (not on every streamed token).
  const proposeAgentToolSig = useMemo(() => {
    const ids: string[] = [];
    for (const turn of turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "tool") continue;
        const tname = block.tool.title || "";
        if (isToolNamed(tname, "propose_agent")) {
          ids.push(block.tool.id);
        }
      }
    }
    return ids.join(",");
  }, [turns]);

  // Reflect agents already created from a propose_agent card when the user
  // RE-ENTERS the session. In-memory ``proposalStates`` is lost on reload, so a
  // confirmed card would otherwise show "pending" again (and a second click
  // would create a duplicate). ``propose_agent`` always creates a
  // ``source=custom`` library agent named exactly as proposed, so a library
  // match means the proposal was confirmed. Best-effort + name-based; never
  // overwrites a live user transition (confirming/dismissing/terminal).
  useEffect(() => {
    if (!selectedSessionId || !proposeAgentToolSig) return;
    const proposeTools: { id: string; name: string }[] = [];
    for (const turn of turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "tool") continue;
        const tname = block.tool.title || "";
        if (!isToolNamed(tname, "propose_agent")) {
          continue;
        }
        let nm = "";
        if (block.tool.input) {
          try {
            const parsed =
              typeof block.tool.input === "string"
                ? JSON.parse(block.tool.input)
                : block.tool.input;
            nm = String(parsed?.name || "");
          } catch {
            /* malformed/streaming input — skip */
          }
        }
        if (nm) proposeTools.push({ id: block.tool.id, name: nm });
      }
    }
    if (proposeTools.length === 0) return;

    let cancelled = false;
    void (async () => {
      try {
        const res = await agentsApi.listAgents("custom");
        if (cancelled) return;
        const names = new Set(res.agents.map((a) => a.name));
        setProposalStates((prev) => {
          let changed = false;
          const next = { ...prev };
          for (const { id, name } of proposeTools) {
            const cur = next[id];
            // Keep live (confirming/dismissing) and terminal (confirmed/
            // dismissed/error) states — only seed an untracked/pending card.
            if (cur && cur.state !== "pending") continue;
            if (names.has(name)) {
              next[id] = { state: "confirmed" };
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      } catch {
        /* non-fatal — list endpoint can fail transiently */
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSessionId, proposeAgentToolSig]);

  // Stable signature of the ``automation create`` proposal tool_use ids in this
  // session — only create-action calls render a proposal card.
  const automationCreateToolSig = useMemo(() => {
    const ids: string[] = [];
    for (const turn of turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "tool") continue;
        const tname = block.tool.title || "";
        if (!isToolNamed(tname, "automation")) continue;
        if (parseAutomationCreateInput(block.tool.input))
          ids.push(block.tool.id);
      }
    }
    return ids.join(",");
  }, [turns]);

  // Reflect automations already created from a proposal card on session
  // RE-ENTRY (in-memory state is lost on reload). Unlike agents (matched by
  // name), automations are matched by ID: the confirm endpoint stamped the
  // proposing ``tool_call_id`` onto the row, so the status endpoint maps each
  // tool id → its created automation. Only seeds untracked/pending cards.
  useEffect(() => {
    if (!selectedSessionId || !automationCreateToolSig) return;
    const ids = automationCreateToolSig.split(",").filter(Boolean);
    if (ids.length === 0) return;

    let cancelled = false;
    void (async () => {
      try {
        const res = await automationsApi.proposalStatus(selectedSessionId, ids);
        if (cancelled) return;
        setAutomationProposalStates((prev) => {
          let changed = false;
          const next = { ...prev };
          for (const id of ids) {
            const cur = next[id];
            if (cur && cur.state !== "pending") continue;
            const hit = res.confirmed[id];
            if (hit) {
              next[id] = {
                state: "confirmed",
                automationId: hit.automation_id,
              };
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      } catch {
        /* non-fatal — status endpoint can fail transiently */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedSessionId, automationCreateToolSig]);

  // Scan staging for every ``submit_skill`` tool_use we've seen, so the
  // card renders the actual file tree (not just the agent's
  // ``files_touched`` claim) and gates its save button on real file
  // presence. While the agent is still streaming, the staging dir
  // might be empty — we poll every 1.5s for up to ~30s and the card
  // shows "Waiting for AI". Once SKILL.md appears, the card flips
  // to "pending" with the file tree visible and save enabled.
  useEffect(() => {
    if (!selectedSessionId) return;
    // Find all submit_skill tool_use blocks in the current turn list.
    const submitTools: { id: string; slug: string }[] = [];
    for (const turn of turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "tool") continue;
        const t = block.tool;
        const name = t.title || "";
        if (!isToolNamed(name, "submit_skill")) {
          continue;
        }
        let slug = "";
        if (t.input) {
          try {
            const parsed =
              typeof t.input === "string" ? JSON.parse(t.input) : t.input;
            slug = String(parsed?.slug || "");
          } catch {
            /* malformed input — skip; card will render with placeholder slug */
          }
        }
        if (slug) submitTools.push({ id: t.id, slug });
      }
    }
    if (submitTools.length === 0) return;

    let cancelled = false;
    const sid = selectedSessionId;

    const tick = async () => {
      if (cancelled) return;
      try {
        const res = await skillsApi.scanStaging(sid);
        const slugViewMap = new Map(res.slugs.map((s) => [s.slug, s]));
        setSubmissionStates((prev) => {
          let changed = false;
          const next: typeof prev = { ...prev };
          for (const { id, slug } of submitTools) {
            const view = slugViewMap.get(slug);
            const current = next[id];
            // Don't overwrite terminal states (confirmed / dismissed)
            // or in-flight states (confirming / dismissing) — those are
            // user-driven transitions that the scan must not stomp.
            if (
              current?.state === "confirmed" ||
              current?.state === "dismissed" ||
              current?.state === "confirming" ||
              current?.state === "dismissing"
            ) {
              continue;
            }
            const stagingPath = `${res.staging_path}/${slug}`;
            if (view && view.files.length > 0) {
              const stagedFiles = view.files.map((f) => ({
                path: f.path,
                type: f.type,
                size: f.size ?? null,
              }));
              const target: SubmissionEntry = {
                state: "pending",
                stagedFiles,
                stagingPath,
              };
              if (
                !current ||
                current.state !== "pending" ||
                current.stagedFiles?.length !== stagedFiles.length
              ) {
                next[id] = target;
                changed = true;
              }
            } else {
              const target: SubmissionEntry = {
                state: "awaiting_files",
                stagedFiles: [],
                stagingPath,
              };
              if (current?.state !== "awaiting_files") {
                next[id] = target;
                changed = true;
              }
            }
          }
          return changed ? next : prev;
        });
      } catch {
        // Non-fatal — scan endpoint can 404 transiently if the staging
        // dir hasn't been initialised yet. Next tick will retry.
      }
    };

    void tick();
    // Poll faster while a turn is active (agent actively writing), slower
    // once the turn is done. Derived ``isBusy``, not the raw pending flag —
    // the flag only bridges the click → turn-start window now.
    const intervalMs = isBusy ? 1500 : 5000;
    const interval = window.setInterval(() => void tick(), intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [selectedSessionId, turns, isBusy]);


  return {
    submissionStates,
    proposalStates,
    automationProposalStates,
    handleConfirmSubmission,
    handleDismissSubmission,
    handleConfirmProposal,
    handleDismissProposal,
    handleConfirmAutomation,
    handleDismissAutomation,
  };
}
