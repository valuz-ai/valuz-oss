import { useEffect, useState } from "react";
import { AlertCircle, ArrowLeft } from "lucide-react";
import { settingsApi, useTranslation } from "@valuz/core";
import type { OnboardingTeamId } from "@valuz/core";
import type { I18nKey } from "@valuz/shared";
import { TEAMS, type TeamMember, type TeamPreset } from "./mock";
import { OnboardingStep } from "./OnboardingShell";
import { StepFooter } from "./StepFooter";

/**
 * Step 4 · Assemble a team. Four blocks: three opinionated presets (general /
 * investment / product) and a "no team for now" block. Picking a preset shows
 * its 4 role briefs (no secondary pack selection) and the primary action
 * deploys those agents into an auto-created example project and lands the
 * user there.
 *
 * The fourth block is a deliberate skip path: clicking it just creates the
 * Valuz Helper and drops the user into a quick chat. We don't surface the
 * helper concept here — the choice reads as "I don't need a team yet".
 */
export const TeamStep = ({
  onEnter,
  onAssistant,
  onSkip,
  onBackToConnect,
}: {
  onEnter: (teamId: OnboardingTeamId) => Promise<void>;
  /** No-team path: create just the Valuz Helper and enter a quick chat with it. */
  onAssistant: () => Promise<void>;
  onSkip: () => void;
  /** Return to ConnectStep so the user can configure a model channel before
   *  deploying a team — without this, the team's agents land with no provider
   *  and the first project chat hits a 422 SessionNotRunnable. */
  onBackToConnect: () => void;
}) => {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<TeamPreset | null>(null);
  // null = still loading; once resolved, drives the "no default model" guard
  // banner on PresetDetail. We refresh on every TeamStep mount so going back
  // to ConnectStep + setting a default reflects immediately on return.
  const [hasDefaultModel, setHasDefaultModel] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const md = await settingsApi.getModelDefaults();
        if (!cancelled) {
          setHasDefaultModel(
            Boolean(md.default_provider_id && md.default_model),
          );
        }
      } catch {
        if (!cancelled) setHasDefaultModel(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [picked]);

  if (picked) {
    return (
      <PresetDetail
        team={picked}
        onBack={() => setPicked(null)}
        onEnter={onEnter}
        hasDefaultModel={hasDefaultModel}
        onBackToConnect={onBackToConnect}
      />
    );
  }

  return (
    <OnboardingStep
      title={t("onboarding.teamTitle" as Parameters<typeof t>[0])}
      subtitle={t("onboarding.teamSubtitle" as Parameters<typeof t>[0])}
      width="lg"
      footer={
        <div className="flex items-center justify-end">
          <button
            type="button"
            onClick={onSkip}
            className="px-2 text-xs text-ink-meta transition-colors hover:text-ink-heading"
          >
            {t("onboarding.skipForNow" as Parameters<typeof t>[0])}
          </button>
        </div>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        {TEAMS.map((team) =>
          team.id === "custom" ? (
            <SkipTeamBlock
              key={team.id}
              team={team}
              onClick={() => void onAssistant()}
            />
          ) : (
            <TeamBlock
              key={team.id}
              team={team}
              onClick={() => setPicked(team)}
            />
          ),
        )}
      </div>
    </OnboardingStep>
  );
};

/* ---------- Grid blocks ---------- */

const TeamBlock = ({
  team,
  onClick,
}: {
  team: TeamPreset;
  onClick: () => void;
}) => {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      className="card-interactive flex h-full flex-col items-start rounded-xl border border-surface-border bg-surface p-4 text-left hover:border-surface-border-hover"
    >
      <span className="text-[26px] leading-none">{team.emoji}</span>
      <span className="mt-3 text-sm font-semibold text-ink-heading">
        {t(team.nameKey as Parameters<typeof t>[0])}
      </span>
      <span className="mt-1 text-xs leading-5 text-ink-body">
        {t(team.taglineKey as Parameters<typeof t>[0])}
      </span>
      <span className="mt-3 flex items-center gap-1.5">
        <span className="flex -space-x-1.5">
          {team.members.map((m) => (
            <span
              key={m.nameKey}
              className="flex h-5 w-5 items-center justify-center rounded-full border border-surface bg-surface-soft text-[11px]"
            >
              {m.avatar}
            </span>
          ))}
        </span>
        <span className="text-2xs text-ink-muted">
          {t("onboarding.memberCount" as Parameters<typeof t>[0], {
            count: team.members.length,
          })}
        </span>
      </span>
    </button>
  );
};

/** No-team path — clicking creates just the Valuz Helper and enters a quick
 *  chat with it. Replaces the old "coming soon" custom block. We don't surface
 *  the helper concept here; the choice reads as "I don't need a team yet" and
 *  the user meets the helper in the chat it drops them into. */
const SkipTeamBlock = ({
  team,
  onClick,
}: {
  team: TeamPreset;
  onClick: () => void;
}) => {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      className="card-interactive flex h-full flex-col items-start rounded-xl border border-dashed border-surface-border bg-surface p-4 text-left hover:border-surface-border-hover"
    >
      <span className="text-[26px] leading-none">{team.emoji}</span>
      <span className="mt-3 text-sm font-semibold text-ink-heading">
        {t(team.nameKey as Parameters<typeof t>[0])}
      </span>
      <span className="mt-1 text-xs leading-5 text-ink-body">
        {t(team.taglineKey as Parameters<typeof t>[0])}
      </span>
    </button>
  );
};

/* ---------- Preset detail ---------- */

const PresetDetail = ({
  team,
  onBack,
  onEnter,
  hasDefaultModel,
  onBackToConnect,
}: {
  team: TeamPreset;
  onBack: () => void;
  onEnter: (teamId: OnboardingTeamId) => Promise<void>;
  /** null = still loading; false = blocks deploy; true = OK. */
  hasDefaultModel: boolean | null;
  onBackToConnect: () => void;
}) => {
  const { t } = useTranslation();
  const [entering, setEntering] = useState(false);
  // PresetDetail never renders for the custom block, so team.id is one of the
  // three real rosters.
  const teamId = team.id as OnboardingTeamId;
  // Block deploy when defaults check has resolved AND it's false. Loading
  // (null) doesn't block — most users have a default and shouldn't see a
  // flash of disabled state.
  const missingDefault = hasDefaultModel === false;

  const handleEnter = async () => {
    setEntering(true);
    try {
      await onEnter(teamId);
      // success navigates away; nothing to reset
    } catch {
      setEntering(false); // surfaced via toast upstream — let the user retry
    }
  };

  return (
    <OnboardingStep
      title={t(team.nameKey as Parameters<typeof t>[0])}
      subtitle={t(team.taglineKey as Parameters<typeof t>[0])}
      footer={
        <div className="flex items-center justify-between">
          <BackLink
            onClick={onBack}
            label={t("onboarding.switchTeam" as Parameters<typeof t>[0])}
          />
          <StepFooter
            primaryLabel={
              entering
                ? t("onboarding.enteringProject" as Parameters<typeof t>[0])
                : t("onboarding.enterExampleProject" as Parameters<typeof t>[0])
            }
            onPrimary={handleEnter}
            primaryDisabled={entering || missingDefault}
          />
        </div>
      }
    >
      {/* Guard banner: without a default model, every deployed agent lands
          without a provider; the first project chat hits 422
          SessionNotRunnable. Tell the user up-front and let them jump
          straight back. */}
      {missingDefault && (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-amber-300/60 bg-amber-50/70 p-3.5 text-amber-900">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">
              {t("onboarding.missingDefaultTitle" as Parameters<typeof t>[0])}
            </div>
            <p className="mt-1 text-xs leading-5">
              {t("onboarding.missingDefaultHint" as Parameters<typeof t>[0])}
            </p>
            <button
              type="button"
              onClick={onBackToConnect}
              className="mt-2 inline-flex items-center gap-1 rounded-md text-xs font-medium text-amber-900 underline-offset-4 hover:underline"
            >
              <ArrowLeft className="h-3 w-3" />
              {t("onboarding.backToConnect" as Parameters<typeof t>[0])}
            </button>
          </div>
        </div>
      )}
      <div className="space-y-2.5">
        {team.members.map((m) => (
          <MemberCard key={m.nameKey} member={m} />
        ))}
      </div>
      {team.collabKey && <CollabNote textKey={team.collabKey} />}
      <p className="mt-4 text-xs leading-5 text-ink-meta">
        {t("onboarding.deployNote" as Parameters<typeof t>[0], {
          count: team.members.length,
        })}
      </p>
    </OnboardingStep>
  );
};

/* ---------- shared bits ---------- */

const MemberCard = ({ member }: { member: TeamMember }) => {
  const { t } = useTranslation();
  return (
    <div className="flex items-start gap-3 rounded-xl border border-surface-border bg-surface p-3.5">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-[18px]">
        {member.avatar}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="text-sm font-semibold text-ink-heading">
            {t(member.nameKey as Parameters<typeof t>[0])}
          </span>
          {member.tag && (
            <span className="rounded bg-brand-light px-1.5 text-2xs font-bold uppercase text-brand-700">
              {member.tag}
            </span>
          )}
        </div>
        <div className="mt-0.5 text-xs text-ink-meta">
          {t(member.dutyKey as Parameters<typeof t>[0])}
        </div>
        {member.skills && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {member.skills.map((s) => (
              <span key={s} className="font-mono text-2xs text-ink-body">
                ✦ {s}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

const CollabNote = ({ textKey }: { textKey: I18nKey }) => {
  const { t } = useTranslation();
  return (
    <div className="mt-4 rounded-xl border border-surface-border bg-surface-soft/50 p-3.5">
      <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-section">
        {t("onboarding.collabSection" as Parameters<typeof t>[0])}
      </div>
      <div className="font-mono text-xs leading-relaxed text-ink-body">
        {t(textKey as Parameters<typeof t>[0])}
      </div>
    </div>
  );
};

const BackLink = ({
  onClick,
  label,
}: {
  onClick: () => void;
  label: string;
}) => (
  <button
    type="button"
    onClick={onClick}
    className="flex items-center gap-1 text-xs text-ink-meta transition-colors hover:text-ink-heading"
  >
    <ArrowLeft className="h-3 w-3" />
    {label}
  </button>
);
