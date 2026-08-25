/**
 * Task-mode hint — why ``send`` spawns a background run instead of a reply.
 *
 * It rides the far-right slot of the execution-location strip rather than a
 * block of its own: the strip is already the composer's footer line, and a
 * second block pushed the input further from the conversation. The sentence
 * is long, so a narrow composer gets a short form instead of wrapping it into
 * a paragraph.
 */

import { Zap } from "lucide-react";
import { useTranslation } from "@valuz/core";

export function TaskLeadHint() {
  const { t } = useTranslation();
  type TK = Parameters<typeof t>[0];
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-2xs text-ink-meta">
      <Zap className="h-3 w-3 shrink-0 text-ink-muted" strokeWidth={2} />
      <span className="hidden truncate @[620px]/composerpane:inline">
        {t("composer.taskHint" as TK)}
      </span>
      <span className="truncate @[620px]/composerpane:hidden">
        {t("composer.taskHintShort" as TK)}
      </span>
    </span>
  );
}
