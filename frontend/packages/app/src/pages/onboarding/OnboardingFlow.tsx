import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { onboardingApi, type OnboardingTeamId } from "@valuz/core";
import { useTranslation } from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
import { toast } from "sonner";
import { markOnboarded } from "../../lib/onboarding";
import { WelcomeStep } from "./WelcomeStep";
import { ConnectStep } from "./ConnectStep";
import { TeamStep } from "./TeamStep";

/**
 * Onboarding v2 first-run flow:
 *
 *   welcome → connect (channel + default model) → team → (enter example project)
 *
 * Step 1 (welcome) is the one editorial moment — a bespoke split screen with no
 * top chrome. Steps 2–3 use the compact, app-styled shell with a slim top
 * progress bar. Connecting a channel also sets the default model inline (the
 * old separate step is folded in). Assembling (or skipping) the team is
 * terminal: it deploys the chosen agents into an example project and routes in.
 */

type Step = "welcome" | "connect" | "team";

const STEP_ORDER: Step[] = ["welcome", "connect", "team"];

/** The 2 dots shown after the welcome screen map to steps 2–3. */
const PROGRESS_STEPS: Step[] = ["connect", "team"];

export const OnboardingFlow = () => {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("welcome");

  const go = (next: Step) => setStep(next);

  // Terminal (skip path): mark done and route into the app without deploying
  // a team.
  const finish = () => {
    markOnboarded();
    navigate("/");
  };

  // Terminal (team path): create the example project + deploy the chosen team's
  // agents server-side, then land the user in that project. Throws on failure
  // so the caller (the enter button) can surface a toast and let the user retry
  // — we do NOT mark onboarded on failure.
  const enterExampleProject = async (teamId: OnboardingTeamId) => {
    try {
      const { project_id } = await onboardingApi.createExampleProject(teamId);
      markOnboarded();
      navigate(`/projects/${project_id}`);
    } catch (err) {
      toast.error(
        _t("onboarding.createProjectFailed" as Parameters<typeof _t>[0]),
      );
      throw err;
    }
  };

  // Terminal (no-team path): create just the Valuz 小助手 and land in 临时对话
  // with it pre-selected (?agent=). No project deployed.
  const enterAssistant = async () => {
    try {
      const { agent_slug } = await onboardingApi.createAssistant();
      markOnboarded();
      navigate(`/?agent=${encodeURIComponent(agent_slug)}`);
    } catch (err) {
      toast.error(
        _t("onboarding.createAssistantFailed" as Parameters<typeof _t>[0]),
      );
      throw err;
    }
  };

  const stepIndex = STEP_ORDER.indexOf(step); // 0..3
  const advance = () => {
    const next = STEP_ORDER[stepIndex + 1];
    if (next) go(next);
    else finish();
  };

  if (step === "welcome") {
    return (
      <OnboardingChrome onSkip={finish} showProgress={false} stepIndex={0}>
        <div className="animate-page-enter">
          <WelcomeStep onStart={() => go("connect")} />
        </div>
      </OnboardingChrome>
    );
  }

  return (
    <OnboardingChrome
      onSkip={finish}
      showProgress
      stepIndex={stepIndex}
      onBack={() => go(STEP_ORDER[Math.max(0, stepIndex - 1)])}
    >
      {/* key by step so the fade re-triggers on every transition */}
      <div key={step} className="animate-page-enter">
        {step === "connect" && (
          <ConnectStep onContinue={advance} onSkip={advance} />
        )}
        {step === "team" && (
          <TeamStep
            onEnter={enterExampleProject}
            onAssistant={enterAssistant}
            onSkip={finish}
            onBackToConnect={() => go("connect")}
          />
        )}
      </div>
    </OnboardingChrome>
  );
};

/* -------------------------------------------------------------------------- */
/* Chrome: slim top bar (back · progress dots · step counter · skip)          */
/* -------------------------------------------------------------------------- */

interface ChromeProps {
  children: React.ReactNode;
  onSkip: () => void;
  showProgress: boolean;
  stepIndex: number; // 0-based over STEP_ORDER
  onBack?: () => void;
}

const OnboardingChrome = ({
  children,
  onSkip,
  showProgress,
  stepIndex,
  onBack,
}: ChromeProps) => {
  const { t } = useTranslation();
  return (
    <div className="relative min-h-screen bg-background">
      {/* pl-20 leaves room for the macOS traffic-light cluster (~78px); on
          web the extra space just reads as breathing room. pr-5 keeps the
          right edge from feeling crowded. */}
      <header
        className="absolute inset-x-0 top-0 z-10 flex items-center justify-between pl-20 pr-5 py-3"
        style={{ WebkitAppRegion: "drag" } as React.CSSProperties}
      >
        <div className="flex items-center gap-4">
          {onBack ? (
            <button
              type="button"
              onClick={onBack}
              className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-heading"
              style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
            >
              <ArrowLeft className="h-4 w-4" />
              {t("onboarding.back" as Parameters<typeof t>[0])}
            </button>
          ) : (
            <span className="h-9" />
          )}

          {showProgress && (
            <div className="flex items-center gap-1.5">
              {PROGRESS_STEPS.map((s) => {
                const active = STEP_ORDER.indexOf(s) === stepIndex;
                const done = STEP_ORDER.indexOf(s) < stepIndex;
                return (
                  <span
                    key={s}
                    className={`h-1.5 rounded-full transition-all ${
                      active
                        ? "w-6 bg-brand"
                        : done
                          ? "w-1.5 bg-brand/50"
                          : "w-1.5 bg-surface-border"
                    }`}
                  />
                );
              })}
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          {showProgress && (
            <span className="font-mono text-[11px] tracking-wide text-ink-muted">
              {t("onboarding.stepCounter" as Parameters<typeof t>[0], {
                current: stepIndex,
                total: 2,
              })}
            </span>
          )}
          <button
            type="button"
            onClick={onSkip}
            className="rounded-lg px-3 py-2 text-sm font-medium text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-heading"
            style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
          >
            {t("onboarding.skipGuide" as Parameters<typeof t>[0])}
          </button>
        </div>
      </header>

      {children}
    </div>
  );
};
