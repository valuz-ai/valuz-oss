import { type ReactNode } from "react";
import { Lock } from "lucide-react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";
import { Badge } from "../ui/badge";
import { Card } from "../ui/card";
import { CardContent } from "../ui/card";
import { getSkillIconStyle } from "./skill-icon-style";

/**
 * Right-hand origin badge surfaced under the new "official → .agents
 * → .claude" grouping (kernel-upgrade-cozy-rose plan). Drives one
 * small chip next to the skill name so the user knows at a glance
 * which sub-bucket the skill belongs to:
 *
 * - In the **official** group: "内置" (builtin).
 * - In the **.agents** group: "创建" vs "同步" depending on
 *   ``creation_origin``.
 * - In the **.claude** group: "claude" / "codex" depending on
 *   ``source``.
 *
 * Pages compute this object and pass it in; the card itself stays
 * dumb about the grouping policy.
 */
export interface SkillOriginBadge {
  label: string;
  tone?: "default" | "valuz" | "claude" | "codex";
}

export interface SkillCardProps {
  skill: {
    name: string;
    description: string;
    tags: string[];
    source: "official" | "custom";
    locked?: boolean;
    version: string;
    originLabel?: string;
    path?: string;
    /** Optional integer version pulled from SKILL.md frontmatter `version: N`. */
    versionNumber?: number | null;
  };
  /**
   * Optional new-style origin badge that supersedes the implicit
   * "official / builtin / custom" badge derived from ``skill.source``
   * + ``skill.originLabel``. When provided, only this badge renders;
   * when omitted, the legacy fallback kicks in so existing callers
   * keep their current visuals.
   */
  originBadge?: SkillOriginBadge;
  active?: boolean;
  onClick?: () => void;
  /** Optional action slot rendered at the trailing edge of the card row. */
  actions?: ReactNode;
}

const ORIGIN_BADGE_TONE: Record<
  NonNullable<SkillOriginBadge["tone"]>,
  string
> = {
  default: "bg-surface-soft text-ink-body",
  // "Valuz-managed" — same brand-y neutral the legacy "official" chip
  // used so the visual weight matches existing UI.
  valuz: "bg-[#eef0ff] text-[#5a63b6]",
  // Anthropic dust — gentle warm tint for ~/.claude/skills/.
  claude: "bg-[#fde8d4] text-[#b45309]",
  // OpenAI-ish slate for ~/.codex/skills/.
  codex: "bg-[#dbeafe] text-[#1d4ed8]",
};

export const SkillCard = ({
  skill,
  originBadge,
  active,
  onClick,
  actions,
}: SkillCardProps) => {
  const { t } = useI18n();
  const iconStyle = getSkillIconStyle(skill.name);
  // Fallback ``sourceLabel`` only kicks in when the caller hasn't
  // supplied a new-style ``originBadge`` — preserves backward
  // compatibility with any consumer that hasn't migrated yet.
  const sourceLabel = originBadge
    ? null
    : skill.originLabel === "Built-in"
      ? t("skill.builtin")
      : skill.source === "official"
        ? t("skill.official")
        : null;

  return (
    <Card
      onClick={onClick}
      className={cn(
        "cursor-default border-surface-border transition-all duration-150 select-none",
        active
          ? "border-brand/60"
          : "hover:border-surface-border-hover hover:shadow-sm",
        skill.locked && "opacity-60",
      )}
    >
      <CardContent className="flex items-start gap-2 px-4 py-1">
        <div
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-transparent text-lg font-semibold",
          )}
          style={{ backgroundColor: iconStyle.bg, color: iconStyle.fg }}
        >
          {iconStyle.letter}
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-0.5 flex flex-wrap items-center gap-2">
            <span className="min-w-0 truncate text-sm font-medium text-ink-heading">
              {skill.name}
            </span>
            {originBadge || sourceLabel || skill.locked ? (
              <span className="inline-flex items-center gap-1">
                {originBadge ? (
                  <Badge
                    variant="brand"
                    className={cn(
                      "rounded-[4px] px-1 py-0 text-[10px] leading-4",
                      ORIGIN_BADGE_TONE[originBadge.tone ?? "default"],
                    )}
                  >
                    {originBadge.label}
                  </Badge>
                ) : sourceLabel ? (
                  <Badge
                    variant="brand"
                    className="rounded-[4px] bg-surface-soft px-1 py-0 text-[10px] leading-4 text-ink-body"
                  >
                    {sourceLabel}
                  </Badge>
                ) : null}
                {skill.locked ? (
                  <span className="inline-flex items-center gap-0.5 rounded-[4px] border border-surface-border px-1 py-0 text-[10px] leading-4 text-ink-meta">
                    <Lock className="h-2.5 w-2.5" /> {t("skill.needsLogin")}
                  </span>
                ) : null}
              </span>
            ) : null}
            {skill.versionNumber != null ? (
              <span className="rounded bg-surface-soft px-1.5 py-0.5 font-mono text-2xs text-ink-meta">
                v{skill.versionNumber}
              </span>
            ) : null}
          </div>
          <div className="mb-2 truncate font-mono text-2xs text-ink-meta">
            {skill.originLabel === "Built-in"
              ? t("skill.builtinHint")
              : skill.source === "official"
                ? t("skill.officialHint")
                : skill.originLabel || skill.path || "~/.claude/skills/"}
          </div>
          <p className="line-clamp-2 text-xs leading-relaxed text-ink-body">
            {skill.description}
          </p>
          {skill.tags.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-1">
              {skill.tags.map((tag) => (
                <Badge
                  key={tag}
                  variant="outline"
                  className="rounded-[4px] px-1 py-0 text-[10px] font-normal leading-4 text-[#6e7481]"
                >
                  {tag}
                </Badge>
              ))}
            </div>
          )}
        </div>
        {actions ? (
          <div className="flex shrink-0 items-center">{actions}</div>
        ) : null}
      </CardContent>
    </Card>
  );
};
