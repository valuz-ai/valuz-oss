import { useEffect, useState } from "react";
import {
  connectorsApi,
  skillsApi,
  useTranslation,
  type ConnectorItem,
  type EffortLevel,
  type SkillView,
} from "@valuz/core";
import {
  DialogField,
  DialogInput,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from "@valuz/ui";
import { AgentModelPicker } from "./AgentModelPicker";
import { MultiSelect } from "./MultiSelect";

const EFFORT_ORDER: readonly EffortLevel[] = [
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
];

export interface AgentEditValue {
  name: string;
  runtime: string;
  providerId: string | null;
  model: string;
  instructions: string;
  /**
   * Reasoning-effort budget for sessions this agent runs. Configured at the
   * agent level (project conversations no longer pick effort per-message).
   */
  effort: EffortLevel;
  /** Skill identifiers (paths) bound from the skill library. */
  skills: string[];
  /** Connector slugs bound from the connected MCP connectors. */
  connectors: string[];
}

export interface AgentEditFormProps {
  value: AgentEditValue;
  onChange: (next: AgentEditValue) => void;
  /** Show the editable name field (hidden when the parent owns the name). */
  showName?: boolean;
}

/**
 * One shared agent-editing form — used for both Agent Templates and project
 * member agents. Composes the runtime/provider/model picker, instructions,
 * skill-library binding, and connected-MCP binding so the field set stays
 * identical everywhere.
 */
export const AgentEditForm = ({
  value,
  onChange,
  showName = true,
}: AgentEditFormProps) => {
  const { t } = useTranslation();
  const [connectors, setConnectors] = useState<ConnectorItem[]>([]);
  const [skills, setSkills] = useState<SkillView[]>([]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [connRes, skillRes] = await Promise.all([
        connectorsApi
          .list()
          .catch(() => ({ connectors: [] as ConnectorItem[] })),
        skillsApi.list().catch(() => ({ project_id: "", skills: [] })),
      ]);
      if (cancelled) return;
      setConnectors(
        connRes.connectors.filter((c) => c.enabled && c.status === "connected"),
      );
      setSkills(skillRes.skills);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-3">
      {showName && (
        <DialogField label={t("agent.name")}>
          <DialogInput
            value={value.name}
            onChange={(e) => onChange({ ...value, name: e.target.value })}
          />
        </DialogField>
      )}

      <AgentModelPicker
        value={{
          runtime: value.runtime,
          providerId: value.providerId,
          model: value.model,
        }}
        onChange={(m) =>
          onChange({
            ...value,
            runtime: m.runtime,
            providerId: m.providerId,
            model: m.model,
          })
        }
      />

      <DialogField
        label={t("agent.effortLabel" as Parameters<typeof t>[0])}
        help={t("agent.effortHint" as Parameters<typeof t>[0])}
        className="w-[160px]"
      >
        <Select
          value={value.effort}
          onValueChange={(next) =>
            onChange({ ...value, effort: next as EffortLevel })
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {EFFORT_ORDER.map((level) => (
              <SelectItem key={level} value={level}>
                {t(`effort.${level}` as Parameters<typeof t>[0])}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </DialogField>

      <DialogField label={t("agent.instructions")}>
        <Textarea
          value={value.instructions}
          onChange={(e) => onChange({ ...value, instructions: e.target.value })}
          rows={5}
        />
      </DialogField>

      <DialogField
        label={t("agent.skillsLabel" as Parameters<typeof t>[0])}
        help={t("agent.skillsHint" as Parameters<typeof t>[0])}
      >
        <MultiSelect
          options={skills.map((s) => ({ value: s.path, label: s.name }))}
          selected={value.skills}
          onChange={(next) => onChange({ ...value, skills: next })}
          placeholder={t("agent.skillsPlaceholder" as Parameters<typeof t>[0])}
          searchPlaceholder={t("agent.skillsSearch" as Parameters<typeof t>[0])}
          emptyText={t("agent.noSkillsLib" as Parameters<typeof t>[0])}
          triggerClassName="font-normal"
        />
      </DialogField>

      <DialogField
        label={t("agent.connectorsLabel")}
        help={t("agent.connectorsHint")}
      >
        <MultiSelect
          options={connectors.map((c) => ({
            value: c.slug,
            label: c.display_name,
            dot: true,
          }))}
          selected={value.connectors}
          onChange={(next) => onChange({ ...value, connectors: next })}
          placeholder={t(
            "agent.connectorsPlaceholder" as Parameters<typeof t>[0],
          )}
          searchPlaceholder={t(
            "agent.connectorsSearch" as Parameters<typeof t>[0],
          )}
          emptyText={t("agent.noConnectors")}
          triggerClassName="font-normal"
        />
      </DialogField>
    </div>
  );
};
