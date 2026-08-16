import type { ReactNode } from "react";
import { ExternalLink, Link2, Zap } from "lucide-react";
import { Badge, cn } from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import type { AgentPluginMemberKind } from "@valuz/core";

/** Minimal member row shape shared by market detail, install preview and
 * the installed-plugin panel (each carries a superset of these fields). */
export interface PluginMemberRow {
  kind: AgentPluginMemberKind;
  slug: string;
  name: string;
  description?: string | null;
  meta_version?: string | null;
  installed?: boolean;
  content_differs?: boolean;
}

interface PluginMembersListProps {
  members: PluginMemberRow[];
  /** When set, each row gets a trailing "查看" affordance. */
  onOpenMember?: (member: PluginMemberRow) => void;
  /** Rendered when both groups are empty. */
  emptyState?: ReactNode;
  className?: string;
}

/**
 * Members grouped as skills → connectors, each row: name, slug, optional
 * frontmatter version, description, and (installed plugins only) the
 * not-installed / content-differs meta chips.
 */
export function PluginMembersList({
  members,
  onOpenMember,
  emptyState,
  className,
}: PluginMembersListProps) {
  const { t } = useTranslation();
  const skills = members.filter((m) => m.kind === "skill");
  const connectors = members.filter((m) => m.kind === "connector");
  if (skills.length === 0 && connectors.length === 0) {
    return emptyState ? <>{emptyState}</> : null;
  }
  return (
    <div className={cn("space-y-4", className)}>
      {skills.length > 0 ? (
        <MemberGroup
          icon={<Zap className="h-3.5 w-3.5 text-ink-meta" />}
          title={t("plugin.skills")}
          count={skills.length}
          members={skills}
          onOpenMember={onOpenMember}
        />
      ) : null}
      {connectors.length > 0 ? (
        <MemberGroup
          icon={<Link2 className="h-3.5 w-3.5 text-ink-meta" />}
          title={t("plugin.connectors")}
          count={connectors.length}
          members={connectors}
          onOpenMember={onOpenMember}
        />
      ) : null}
    </div>
  );
}

function MemberGroup({
  icon,
  title,
  count,
  members,
  onOpenMember,
}: {
  icon: ReactNode;
  title: string;
  count: number;
  members: PluginMemberRow[];
  onOpenMember?: (member: PluginMemberRow) => void;
}) {
  const { t } = useTranslation();
  return (
    <section>
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-ink-heading">
        {icon}
        {title}
        <span className="text-2xs font-normal tabular-nums text-ink-muted">
          · {count}
        </span>
      </div>
      <div className="overflow-hidden rounded-lg border border-surface-border bg-surface">
        {members.map((member) => (
          <div
            key={`${member.kind}:${member.slug}`}
            className="flex items-start gap-2.5 border-b border-surface-border px-3 py-2 last:border-b-0"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="min-w-0 truncate text-sm font-medium text-ink-heading">
                  {member.name}
                </span>
                {member.slug && member.slug !== member.name ? (
                  <span className="truncate font-mono text-2xs text-ink-meta">
                    {member.slug}
                  </span>
                ) : null}
                {member.meta_version ? (
                  <Badge
                    variant="metaNeutral"
                    className="h-4 px-1 font-mono text-2xs text-ink-meta"
                  >
                    v{member.meta_version}
                  </Badge>
                ) : null}
                {member.installed === false ? (
                  <Badge variant="metaOutline" className="h-4 px-1 text-2xs">
                    {t("plugin.notInstalledMember")}
                  </Badge>
                ) : null}
                {member.content_differs ? (
                  <Badge variant="warning" className="h-4 px-1 text-2xs">
                    {t("plugin.contentDiffers")}
                  </Badge>
                ) : null}
              </div>
              {member.description ? (
                <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-ink-body">
                  {member.description}
                </p>
              ) : null}
            </div>
            {onOpenMember ? (
              <button
                type="button"
                onClick={() => onOpenMember(member)}
                className="inline-flex h-6 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
                aria-label={t("plugin.openMember")}
              >
                <ExternalLink className="h-3 w-3" />
                {t("plugin.openMember")}
              </button>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}
