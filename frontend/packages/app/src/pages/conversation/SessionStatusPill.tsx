import { useTranslation } from "@valuz/core";

/**
 * Small status pill shown next to the conversation title in the page
 * header. Mirrors the sidebar's per-row indicator: ``running`` pulses
 * (the agent is mid-turn), ``failed``/``cancelled`` show a muted state.
 * Idle / archived / undefined render nothing — no point in chrome for
 * the steady state.
 */
export const SessionStatusPill = ({
  status,
  cancelled,
  pending,
  background,
}: {
  status?: string;
  cancelled?: boolean;
  /** The transcript hasn't loaded yet, so ``cancelled`` isn't known. Suppresses
   *  the failure pill in the meantime so a stopped conversation doesn't flash a
   *  red 失败 for a beat before it resolves to the grey 已停止. */
  pending?: boolean;
  /** A ``run_in_background`` task is still executing. The launching turn ends
   *  normally, so ``session.status`` goes ``idle`` while real work continues —
   *  without this the header is the ONE surface that goes quiet, while the
   *  sidebar dot and the background-task strip both still say "running". */
  background?: boolean;
}) => {
  const { t } = useTranslation();
  // A user-interrupted turn can leave the PERSISTED session status on
  // ``failed`` / ``terminated``: the interrupt's ``idle`` finalize races the
  // turn's own finalize, and when the turn finalize wins it maps the cut-short
  // run to a failure. When the transcript itself says the last turn was
  // cancelled, that's an interrupt, not a failure — show the quiet 已中断 pill
  // instead of a red 失败.
  if (cancelled) {
    return (
      <span
        className="flex h-5 shrink-0 items-center gap-1 rounded-[4px] bg-surface-soft px-2 py-0 text-2xs text-ink-meta"
        title="session status: cancelled"
      >
        {/* One label for a stopped conversation everywhere: matches the
            activity feed / project lists (activity.statusStopped) and the
            "停止" button, rather than a second word (已中断) only here. */}
        {t("activity.statusStopped" as Parameters<typeof t>[0])}
      </span>
    );
  }
  // A stopped conversation persists as ``failed``/``terminated``; whether it was
  // a user stop (grey) or a real error (red) is only known once the transcript
  // loads and ``cancelled`` resolves. Until then, show no pill rather than a red
  // 失败 that flips to grey a beat later.
  if (pending && (status === "failed" || status === "terminated")) return null;
  // Background work outlives its launching turn, so ``status`` reads ``idle``
  // while a task is still executing. The server reports that as a separate
  // ``background`` flag (same source the sidebar and Activity read), and it
  // gets its own label rather than reusing 运行中: nothing is streaming, a
  // shell task is what's in flight — matching ``ActivityPage``'s
  // ``run.background`` branch. A real running turn still wins.
  const effective =
    background && (!status || status === "idle") ? "background" : status;
  if (!effective || effective === "idle" || effective === "archived")
    return null;
  const live = effective === "running" || effective === "background";
  const text =
    effective === "running"
      ? t("common.running" as Parameters<typeof t>[0])
      : effective === "background"
        ? t("activity.statusBackground" as Parameters<typeof t>[0])
        : effective === "created"
          ? t("common.waiting" as Parameters<typeof t>[0])
          : effective === "failed"
            ? t("common.failed" as Parameters<typeof t>[0])
            : effective === "cancelled"
              ? t("conversation.interrupted" as Parameters<typeof t>[0])
              : effective;
  const cls = live
    ? "bg-brand/10 text-brand"
    : effective === "created"
      ? "bg-brand/5 text-brand/80"
      : effective === "failed"
        ? "bg-error-light text-error-text"
        : "bg-surface-soft text-ink-meta";
  return (
    <span
      className={`flex h-5 shrink-0 items-center gap-1 rounded-[4px] px-2 py-0 text-2xs ${cls}`}
      title={`session status: ${status ?? "idle"}${
        effective === "background" ? " (background task running)" : ""
      }`}
    >
      {live ? (
        <span className="h-1.5 w-1.5 rounded-full bg-brand animate-pulse" />
      ) : null}
      {text}
    </span>
  );
};
