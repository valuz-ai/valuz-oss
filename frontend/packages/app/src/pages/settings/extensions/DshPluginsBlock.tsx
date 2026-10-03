import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import { ExternalLink, PlugZap } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  EmptyState,
  LoadingState,
  SettingsRow,
} from "@valuz/ui";
import {
  describeDshApiError,
  dshPluginsApi,
  useTranslation,
} from "@valuz/core";
import type { DshManagerStatus } from "@valuz/core";
import { t as translate } from "@valuz/shared/i18n";
import { DshBundleList } from "./DshBundleList";
import { DshInstallForm } from "./DshInstallForm";
import { useDshManager } from "./use-dsh-manager";

const SubHeading = ({
  id,
  title,
  desc,
}: {
  id: string;
  title: string;
  desc?: string;
}) => (
  <div className="mb-3">
    <h4 id={id} className="text-sm font-semibold text-ink-heading">
      {title}
    </h4>
    {desc ? <p className="text-xs text-ink-body">{desc}</p> : null}
  </div>
);

/** Profile + home of the managed dsh, so the user knows where plugins live. */
const ProfileLine = ({ status }: { status: DshManagerStatus }) => {
  const { t } = useTranslation();
  return (
    <p className="mb-3 break-all text-xs text-ink-meta">
      {t("extensions.dsh.profile")}{" "}
      <span className="font-mono text-ink-body">{status.profile}</span>
      {" · "}
      {t("extensions.dsh.home")}{" "}
      <span className="font-mono text-ink-body">{status.home}</span>
    </p>
  );
};

/** Starts the resident manager host, then opens dsh's own plugin UI. */
const OpenManagerRow = () => {
  const { t } = useTranslation();
  const [opening, setOpening] = useState(false);

  const open = async () => {
    setOpening(true);
    try {
      const { ui_url } = await dshPluginsApi.startManager();
      window.open(ui_url, "_blank", "noopener");
    } catch (error) {
      toast.error(
        translate("extensions.dsh.openFailed", {
          error: describeDshApiError(error).message,
        }),
      );
    } finally {
      setOpening(false);
    }
  };

  return (
    <Card className="mb-6 rounded-xl shadow-xs">
      <CardContent className="py-1">
        <SettingsRow
          className="px-0"
          label={t("extensions.dsh.openManagerLabel")}
          desc={t("extensions.dsh.openManagerDesc")}
        >
          <Button
            variant="outline"
            size="sm"
            disabled={opening}
            loading={opening}
            onClick={() => void open()}
          >
            {!opening ? <ExternalLink /> : null}
            {t("extensions.dsh.openManager")}
          </Button>
        </SettingsRow>
      </CardContent>
    </Card>
  );
};

/**
 * The DSH half of the Extensions page. Cloud builds disable the manager, so an
 * unavailable manager is an explanation, never an error.
 */
export const DshPluginsBlock = () => {
  const { t } = useTranslation();
  const { state, reload, refreshBundles } = useDshManager();

  let body: ReactNode;
  switch (state.phase) {
    case "checking":
      body = <LoadingState variant="section" />;
      break;
    case "connecting":
      // The first ``listBundles`` starts the resident host: up to ~30s.
      body = (
        <LoadingState
          variant="section"
          label={t("extensions.dsh.connecting")}
        />
      );
      break;
    case "unavailable":
      body = (
        <EmptyState
          variant="plain"
          icon={<PlugZap />}
          title={t("extensions.dsh.unavailable.title")}
          description={
            state.status && !state.status.enabled
              ? t("extensions.dsh.unavailable.disabled")
              : t("extensions.dsh.unavailable.generic")
          }
          action={
            state.reason ? (
              <p className="max-w-[460px] break-words text-xs text-ink-meta">
                {t("extensions.dsh.unavailable.reason", {
                  reason: state.reason,
                })}
              </p>
            ) : undefined
          }
        />
      );
      break;
    case "error":
      body = (
        <EmptyState
          variant="plain"
          icon={<PlugZap />}
          title={t("extensions.dsh.loadFailed")}
          description={state.message}
          action={
            <Button variant="outline" size="sm" onClick={() => void reload()}>
              {t("common.retry")}
            </Button>
          }
        />
      );
      break;
    case "ready":
      body = (
        <>
          <ProfileLine status={state.status} />
          <OpenManagerRow />
          <SubHeading
            id="extensions-dsh-bundles"
            title={t("extensions.dsh.bundles.title")}
            desc={t("extensions.dsh.bundles.desc")}
          />
          <div className="mb-6">
            <DshBundleList bundles={state.bundles} onChanged={refreshBundles} />
          </div>
          <SubHeading
            id="extensions-dsh-install"
            title={t("extensions.dsh.install.title")}
            desc={t("extensions.dsh.install.desc")}
          />
          <DshInstallForm onInstalled={refreshBundles} />
          <p className="mt-6 text-xs text-ink-meta">
            {t("extensions.dsh.toolsNote")}
          </p>
        </>
      );
      break;
  }

  return (
    <section aria-labelledby="extensions-dsh-heading" className="mb-8">
      <div className="mb-3">
        <h3
          id="extensions-dsh-heading"
          className="text-sm font-semibold text-ink-heading"
        >
          {t("extensions.dsh.title")}
        </h3>
        <p className="text-xs text-ink-body">{t("extensions.dsh.desc")}</p>
      </div>
      {body}
    </section>
  );
};
