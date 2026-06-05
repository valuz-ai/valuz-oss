import { useEffect, useState } from "react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogField,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  SegmentedControl,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@valuz/ui";
import { useTranslation, type Agent } from "@valuz/core";
import { toast } from "sonner";
import { AgentEditForm, type AgentEditValue } from "./AgentEditForm";

const BLANK_AGENT_VALUE: AgentEditValue = {
  name: "",
  runtime: "claude_agent",
  providerId: null,
  model: "claude-sonnet-4-6",
  instructions: "",
  effort: "high",
  skills: [],
  connectors: [],
};

// ``agent_slug`` is no longer carried here — the backend derives the
// member slug from the source/display name (VALUZ-AGENT-SLUG).
export type AddAgentSubmitData =
  | {
      mode: "agent";
      source_agent_slug: string;
      provider_id: string | null;
      model: string | null;
    }
  | {
      mode: "custom";
      name: string;
      runtime: string;
      model: string;
      provider_id: string | null;
      effort: string;
      instructions: string;
      skills: string[];
      connector_bindings: { type: string }[];
    };

export interface AddAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agents: Agent[];
  onSubmit: (data: AddAgentSubmitData) => Promise<void>;
  /** Called after a member is successfully added (for the caller to refresh). */
  onAdded: () => void | Promise<void>;
}

/**
 * Add a member agent to a project — either instantiated from a Library
 * agent or created blank/custom. Shared by the project home (config panel
 * "Agents" section) and the project tasks page so both surfaces open the
 * exact same dialog.
 */
export const AddAgentDialog = ({
  open,
  onOpenChange,
  agents,
  onSubmit,
  onAdded,
}: AddAgentDialogProps) => {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"agent" | "custom">("agent");
  const [sourceSlug, setSourceSlug] = useState("");
  const [value, setValue] = useState<AgentEditValue>(BLANK_AGENT_VALUE);
  const [busy, setBusy] = useState(false);

  // Reset the form whenever the dialog opens so a fresh add always starts
  // from the first agent / blank value.
  useEffect(() => {
    if (!open) return;
    const first = agents[0]?.slug ?? "";
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMode("agent");
    setSourceSlug(first);
    setValue(BLANK_AGENT_VALUE);
  }, [open, agents]);

  // Slug is backend-derived from the source/display name (VALUZ-AGENT-SLUG);
  // the dialog no longer computes it.

  // v2 派驻 is a live reference — config lives on the agent, so deploy needs
  // only a selected source agent (no per-instance provider pin). Official
  // agents whose provider_id is null must still be deployable.
  const valid =
    mode === "agent" ? sourceSlug.length > 0 : value.name.trim().length > 0;

  const submit = async () => {
    if (!valid) return;
    setBusy(true);
    try {
      if (mode === "agent") {
        // v2 派驻: reference-only. provider/model are no longer per-instance
        // overrides (the backend ignores them); config lives on the agent.
        await onSubmit({
          mode: "agent",
          source_agent_slug: sourceSlug,
          provider_id: null,
          model: null,
        });
      } else {
        await onSubmit({
          mode: "custom",
          name: value.name.trim(),
          runtime: value.runtime,
          model: value.model.trim() || "claude-sonnet-4-6",
          provider_id: value.providerId,
          effort: value.effort,
          instructions: value.instructions,
          skills: value.skills,
          connector_bindings: value.connectors.map((type) => ({ type })),
        });
      }
      onOpenChange(false);
      await onAdded();
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("common.saveFailed" as Parameters<typeof t>[0]),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {mode === "agent"
              ? t("agent.instantiateTitle")
              : t("agent.createBlankTitle")}
          </DialogTitle>
          <DialogDescription>
            {mode === "agent"
              ? t("agent.instantiateDesc")
              : t("agent.createBlankDesc")}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <SegmentedControl
            value={mode}
            onValueChange={(v) => setMode(v as "agent" | "custom")}
            options={[
              { value: "agent", label: t("agent.modeAgent") },
              { value: "custom", label: t("agent.modeCustom") },
            ]}
          />
          {mode === "agent" ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
              {t("agent.deployWarning")}
            </div>
          ) : null}
          {mode === "agent" ? (
            <DialogField label={t("agent.agentLabel")} required>
              <Select value={sourceSlug} onValueChange={setSourceSlug}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {agents.map((tpl) => (
                    <SelectItem key={tpl.slug} value={tpl.slug}>
                      {tpl.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </DialogField>
          ) : null}
          {mode === "custom" && (
            <AgentEditForm value={value} onChange={setValue} />
          )}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            {t("common.cancel")}
          </Button>
          <Button onClick={submit} disabled={busy || !valid}>
            {mode === "agent"
              ? t("agent.instantiateSubmit")
              : t("agent.createBlankSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
