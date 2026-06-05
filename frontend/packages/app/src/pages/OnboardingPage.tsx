import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { MessageSquare, FolderOpen } from "lucide-react";
import { Button } from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { markOnboarded } from "../lib/onboarding";

export const OnboardingPage = () => {
  const { t } = useTranslation();
  const [step, setStep] = useState<1 | 2>(1);
  const navigate = useNavigate();

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="w-full max-w-[460px] px-6">
        {/* Step 1: File parsing info */}
        {step === 1 && (
          <>
            <div className="mb-2 text-right text-xs text-ink-meta">1 / 2</div>
            <h1 className="mb-1 text-lg font-semibold text-ink-heading">
              {t("knowledge.indexing" as Parameters<typeof t>[0])}
            </h1>
            <p className="mb-6 text-sm leading-6 text-ink-body">
              {t("onboarding.stepProgress" as Parameters<typeof t>[0], {
                current: String(step),
                total: "2",
              })}
              <strong className="text-ink-heading">
                {t("onboarding.localFirst" as Parameters<typeof t>[0])}
              </strong>{" "}
              {t("onboarding.localFirstDesc" as Parameters<typeof t>[0])}
            </p>

            <div className="mb-6 rounded-xl border border-surface-border bg-surface-soft/50 p-4">
              <p className="text-xs font-medium text-ink-label">
                {t("onboarding.recommended" as Parameters<typeof t>[0])}
              </p>
              <p className="mt-1 text-xs leading-5 text-ink-body">
                {`${t("settings.title" as Parameters<typeof t>[0])} → ${t("onboarding.cloudCollabDesc" as Parameters<typeof t>[0])}`}
                <br />
                {t("onboarding.cloudCollab" as Parameters<typeof t>[0])}
              </p>
            </div>

            {/* Page dots */}
            <div className="mb-6 flex items-center justify-center gap-2">
              <div className="h-1.5 w-6 rounded-full bg-brand" />
              <div className="h-1.5 w-6 rounded-full bg-surface-border" />
            </div>

            <Button className="w-full" onClick={() => setStep(2)}>
              {`${t("common.nextStep" as Parameters<typeof t>[0])} →`}
            </Button>
          </>
        )}

        {/* Step 2: Chat vs Project intro */}
        {step === 2 && (
          <>
            <div className="mb-2 text-right text-xs text-ink-meta">2 / 2</div>
            <h1 className="mb-6 text-lg font-semibold text-ink-heading">
              {t("onboarding.selectConnection" as Parameters<typeof t>[0])}
            </h1>

            <div className="space-y-4">
              {/* Chat card */}
              <div className="rounded-xl border border-surface-border bg-surface p-5">
                <div className="mb-2 flex items-center gap-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand/10 text-brand">
                    <MessageSquare className="h-4 w-4" />
                  </div>
                  <span className="text-sm font-medium text-ink-heading">
                    {t("conversation.newChat" as Parameters<typeof t>[0])}
                  </span>
                </div>
                <p className="text-xs leading-5 text-ink-body">
                  {t("conversation.startHere" as Parameters<typeof t>[0])}
                </p>
              </div>

              {/* Project card */}
              <div className="rounded-xl border border-surface-border bg-surface p-5">
                <div className="mb-2 flex items-center gap-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-soft text-ink-body">
                    <FolderOpen className="h-4 w-4" />
                  </div>
                  <span className="text-sm font-medium text-ink-heading">
                    {t("project.instruction" as Parameters<typeof t>[0])}
                  </span>
                </div>
                <p className="text-xs leading-5 text-ink-body">
                  {t("project.askAgent" as Parameters<typeof t>[0])}
                </p>
              </div>
            </div>

            {/* Page dots */}
            <div className="my-6 flex items-center justify-center gap-2">
              <div className="h-1.5 w-6 rounded-full bg-surface-border" />
              <div className="h-1.5 w-6 rounded-full bg-brand" />
            </div>

            <Button
              className="w-full"
              onClick={() => {
                // The user has finished the connection wizard — persist
                // the marker so the setup gate stops bouncing them back
                // to /welcome on every relaunch. (Subscription provider
                // creds live in the CLI keychain and are invisible to
                // the providers API, so we need this localStorage flag
                // to know the user has gotten past welcome.)
                markOnboarded();
                navigate("/");
              }}
            >
              {`${t("conversation.newChat" as Parameters<typeof t>[0])} →`}
            </Button>
          </>
        )}
      </div>
    </div>
  );
};
