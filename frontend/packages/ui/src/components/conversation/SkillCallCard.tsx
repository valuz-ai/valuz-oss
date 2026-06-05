import { Bot, ChevronDown } from "lucide-react";
import { useState } from "react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface SkillCallCardProps {
  skillName: string;
  skillDescription?: string;
  status: "running" | "completed" | "failed";
  output?: string;
}

export const SkillCallCard = ({
  skillName,
  skillDescription,
  status,
  output,
}: SkillCallCardProps) => {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(status !== "completed");

  return (
    <div className="rounded-lg border border-surface-border bg-surface-soft">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <Bot
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            status === "running"
              ? "text-brand animate-pulse"
              : status === "completed"
                ? "text-success"
                : "text-red-500",
          )}
        />
        <span className="flex-1 text-xs font-medium text-ink-label">
          Skill: {skillName}
        </span>
        <span
          className={cn(
            "text-2xs",
            status === "running"
              ? "text-brand"
              : status === "completed"
                ? "text-success"
                : "text-red-500",
          )}
        >
          {status === "running"
            ? t("conversation.executing")
            : status === "completed"
              ? t("toolCall.complete")
              : t("common.failed")}
        </span>
        <ChevronDown
          className={cn(
            "h-3 w-3 text-ink-muted transition",
            expanded && "rotate-180",
          )}
        />
      </button>
      {expanded && (
        <div className="border-t border-surface-border px-3 py-2">
          {skillDescription && (
            <div className="text-2xs text-ink-meta">{skillDescription}</div>
          )}
          {output && (
            <pre className="mt-1 whitespace-pre-wrap font-mono text-xs text-ink-label">
              {output}
            </pre>
          )}
        </div>
      )}
    </div>
  );
};
