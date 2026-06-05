import { Shield, Sparkles } from "lucide-react";
import { useTranslation } from "@valuz/core";
import { WELCOME_FEED, type FeedCard, type FeedStatus } from "./mock";

/**
 * Step 1 · Welcome — a split editorial screen. Left: the pitch
 * (Project-as-Agent-Team) in display serif. Right: a frozen slice of a real
 * project where an agent team is already collaborating, so the value lands
 * before any configuration. Adapted from the Multica welcome pattern to the
 * Valuz design language (violet brand, serif hero, no Valuz account).
 */
export const WelcomeStep = ({ onStart }: { onStart: () => void }) => {
  const { t } = useTranslation();
  return (
    <div className="relative grid min-h-screen grid-cols-1 overflow-hidden bg-white lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
      {/* ---------- Left: pitch ---------- */}
      <div className="relative flex flex-col justify-center overflow-hidden bg-[linear-gradient(135deg,#f8fbff_0%,#ffffff_38%,#f3f6ff_68%,#eef4ff_100%)] px-10 py-16 lg:px-16">
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(28deg,rgba(91,130,255,0.24)_0%,rgba(141,108,255,0.14)_24%,transparent_48%),linear-gradient(180deg,transparent_66%,rgba(225,233,255,0.56)_100%)]" />
        <div className="relative mx-auto w-full max-w-md">
          {/* Brand eyebrow */}
          <div className="mb-10 flex items-center gap-2.5">
            <img src="./logo.png" alt="Valuz" className="h-[34px] w-[34px] rounded-lg" />
            <span className="text-[17px] font-semibold tracking-tight text-ink-heading">
              Valuz
            </span>
          </div>

          <h1 className="font-display text-[42px] font-medium leading-[1.12] tracking-tight text-ink-heading">
            {t("onboarding.heroTitleLine1" as Parameters<typeof t>[0])}
            <br />
            {t("onboarding.heroTitleLine2" as Parameters<typeof t>[0])}
            <span className="text-brand">
              {t("onboarding.heroTitleAccent" as Parameters<typeof t>[0])}
            </span>
            {t("onboarding.heroTitleSuffix" as Parameters<typeof t>[0])}
          </h1>

          <p className="mt-6 max-w-sm text-[15px] leading-7 text-ink-body">
            {t("onboarding.heroSubtitlePrefix" as Parameters<typeof t>[0])}
            <span className="font-medium text-ink-body">
              {t("onboarding.heroSubtitleAccent" as Parameters<typeof t>[0])}
            </span>
            {t("onboarding.heroSubtitleSuffix" as Parameters<typeof t>[0])}
          </p>

          <button
            type="button"
            onClick={onStart}
            className="mt-9 inline-flex min-w-40 items-center justify-center gap-2 rounded-xl bg-brand px-9 py-3 text-[14px] font-medium text-white shadow-[0_14px_30px_rgba(76,70,229,0.24)] transition-all hover:bg-brand-hover hover:shadow-[0_18px_34px_rgba(76,70,229,0.3)] active:scale-[0.99]"
          >
            {t("onboarding.startButton" as Parameters<typeof t>[0])}
            <span aria-hidden className="text-[15px]">
              →
            </span>
          </button>

          <p className="mt-7 flex items-center gap-2 text-[12.5px] leading-5 text-ink-meta">
            <Shield className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
            {t("onboarding.heroFootnote" as Parameters<typeof t>[0])}
          </p>
        </div>
      </div>

      {/* ---------- Right: live team feed ---------- */}
      <div className="relative hidden flex-col justify-center overflow-hidden border-l border-surface-border/70 bg-white px-10 py-16 lg:flex lg:px-14">
        <p className="mb-8 max-w-sm font-display text-[14.5px] italic leading-6 text-ink-meta">
          {t("onboarding.heroQuote" as Parameters<typeof t>[0])}
        </p>

        <div className="relative space-y-3">
          {WELCOME_FEED.map((card, i) => (
            <div
              key={card.id}
              className="relative grid grid-cols-[24px_minmax(0,1fr)] items-center gap-4"
            >
              <div className="relative flex h-full items-center justify-center">
                {i < WELCOME_FEED.length - 1 && (
                  <span
                    className="absolute left-1/2 top-[calc(50%+10px)] bottom-[-12px] w-px -translate-x-1/2 bg-surface-border/90"
                    aria-hidden
                  />
                )}
                <span
                  className="relative z-[1] flex h-5 w-5 items-center justify-center rounded-full bg-white shadow-[0_0_0_1px_rgba(226,232,240,0.95),0_4px_12px_rgba(15,23,42,0.08)]"
                  aria-hidden
                >
                  <span
                    className={`h-2.5 w-2.5 rounded-full ${
                      card.status === "you"
                        ? "bg-ink-muted"
                        : STATUS_META[card.status].dot
                    }`}
                  />
                </span>
              </div>
              <FeedCardView card={card} index={i} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

const STATUS_META: Record<
  FeedStatus,
  { labelKey: string; pill: string; dot: string }
> = {
  running: {
    labelKey: "onboarding.statusRunning",
    pill: "bg-warning-light text-warning-text",
    dot: "bg-warning",
  },
  done: {
    labelKey: "onboarding.statusDone",
    pill: "bg-success-light text-success-text",
    dot: "bg-success",
  },
  review: {
    labelKey: "onboarding.statusReview",
    pill: "bg-brand-light text-brand-700",
    dot: "bg-brand",
  },
  you: { labelKey: "", pill: "", dot: "" },
};

const FeedCardView = ({ card, index }: { card: FeedCard; index: number }) => {
  const { t } = useTranslation();
  const meta = STATUS_META[card.status];
  const isYou = card.status === "you";
  const author = t(card.authorKey as Parameters<typeof t>[0]);
  const body = t(card.bodyKey as Parameters<typeof t>[0]);
  // The avatar glyph for the user mirrors the feed.you i18n value rather than
  // a hardcoded character, so zh-CN and en-US builds stay visually consistent.
  const youGlyph = t("onboarding.feed.you" as Parameters<typeof t>[0]);
  const when = card.whenKey
    ? t(card.whenKey as Parameters<typeof t>[0], card.whenParams)
    : null;

  return (
    <div
      className="rounded-xl border border-surface-border/80 bg-white/84 p-4 shadow-[0_12px_30px_rgba(15,23,42,0.08)] backdrop-blur"
      style={{
        // staggered indentation so the stack feels like an unfolding thread
        marginLeft: `${Math.min(index, 3) * 12}px`,
        maxWidth: "480px",
      }}
    >
      <div className="flex items-center gap-2.5">
        <div
          className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[14px] ${
            isYou ? "bg-ink-heading text-white" : "bg-surface-soft"
          }`}
        >
          {isYou ? youGlyph : card.avatar}
        </div>
        <span className="text-[13px] font-semibold text-ink-heading">
          {author}
        </span>
        <span className="ml-auto font-mono text-[10.5px] tracking-wide text-ink-muted">
          {card.ref}
        </span>
      </div>

      <p className="mt-2.5 text-[13px] leading-[1.55] text-ink-body">
        {renderMentions(body)}
      </p>

      {!isYou && (
        <div className="mt-3 flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[11px] font-medium ${meta.pill}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
            {meta.labelKey ? t(meta.labelKey as Parameters<typeof t>[0]) : ""}
          </span>
          {when && <span className="text-[11px] text-ink-muted">· {when}</span>}
        </div>
      )}

      {isYou && (
        <div className="mt-3 flex items-center gap-1.5 text-[11px] text-ink-muted">
          <Sparkles className="h-3 w-3" />
          {t("onboarding.assignedToTeam" as Parameters<typeof t>[0])}
        </div>
      )}
    </div>
  );
};

/** Render @mentions in an accent colour without a markdown dependency. */
const renderMentions = (text: string) => {
  const parts = text.split(/(@[^\s，。、]+)/g);
  return parts.map((part, i) =>
    part.startsWith("@") ? (
      <span key={i} className="font-medium text-brand">
        {part}
      </span>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
};
