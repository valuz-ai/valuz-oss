import type { FC } from "react";
import { Card, CardContent } from "../ui/card";
import { Badge } from "../ui/badge";
import { useI18n } from "../../hooks/use-i18n";

export interface ConversationSummaryCardProps {
  title: string;
  topic: string;
  findings: string[];
  references: string[];
}

export const ConversationSummaryCard: FC<ConversationSummaryCardProps> = ({
  title,
  topic,
  findings,
  references,
}) => {
  const { t } = useI18n();
  return (
    <Card>
      <CardContent className="flex flex-col gap-4 pt-0">
        <span className="text-sm font-medium text-ink-title">{title}</span>
        <p className="text-xs text-ink-body">{topic}</p>

        {findings.length > 0 ? (
          <div className="flex flex-col gap-2">
            <span className="text-xs font-medium text-ink-muted">
              {t("project.keyFindings")}
            </span>
            <ul className="flex flex-col gap-1.5">
              {findings.map((finding, i) => (
                <li
                  key={i}
                  className="text-xs text-ink-body before:mr-2 before:content-['-']"
                >
                  {finding}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {references.length > 0 ? (
          <div className="flex flex-col gap-2">
            <span className="text-xs font-medium text-ink-muted">
              {t("project.referencedDocsLabel")}
            </span>
            <div className="flex flex-wrap gap-1.5">
              {references.map((ref, i) => (
                <Badge key={i} variant="outline" className="text-[11px]">
                  {ref}
                </Badge>
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
};
