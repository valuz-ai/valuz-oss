import { useState, type FormEvent } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  Badge,
  Button,
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
  Input,
  Spinner,
} from "@valuz/ui";
import {
  describeDshApiError,
  dshPluginsApi,
  useTranslation,
} from "@valuz/core";
import type { DshChangeResult, DshSpecInspection } from "@valuz/core";
import { changeTone, describeManagementError } from "./dsh-helpers";
import { Notice } from "./Notice";

type Accepted = Extract<DshSpecInspection, { status: "accepted" }>;

type Step =
  | { kind: "idle" }
  | { kind: "inspecting" }
  | { kind: "inspected"; spec: string; inspection: DshSpecInspection }
  | { kind: "installing"; spec: string; inspection: Accepted }
  | { kind: "done"; result: DshChangeResult };

/** Collapsible, scrollable pnpm output — the evidence behind a failed install. */
const InstallOutput = ({
  output,
  truncated,
  logPath,
}: {
  output: string;
  truncated: boolean;
  logPath: string;
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const Icon = open ? ChevronDown : ChevronRight;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="xs" className="-ml-2">
          <Icon />
          {t("extensions.dsh.install.output")}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-surface-border bg-surface-soft px-3 py-2 font-mono text-2xs leading-4 text-ink-body">
          {output}
        </pre>
        {truncated ? (
          <p className="mt-1 break-all text-2xs text-ink-meta">
            {t("extensions.dsh.install.outputTruncated", { path: logPath })}
          </p>
        ) : null}
      </CollapsibleContent>
    </Collapsible>
  );
};

const InstallResult = ({ result }: { result: DshChangeResult }) => {
  const { t } = useTranslation();
  const tone = changeTone(result);
  const target = result.bundle
    ? `${result.bundle}${result.version ? `@${result.version}` : ""}`
    : result.target;

  if (tone !== "error") {
    const key =
      result.application === "restart-required"
        ? "extensions.dsh.install.result.restartRequired"
        : result.application === "overridden"
          ? "extensions.dsh.install.result.overridden"
          : "extensions.dsh.install.result.applied";
    return (
      <Notice tone={tone}>
        <p>{t(key, { target })}</p>
        {result.warnings?.length ? (
          <p>
            {t("extensions.dsh.install.warnings", {
              warnings: result.warnings.join("; "),
            })}
          </p>
        ) : null}
      </Notice>
    );
  }

  if (result.application === "cancelled") {
    return (
      <Notice tone="error">
        <p>{t("extensions.dsh.install.result.cancelled")}</p>
      </Notice>
    );
  }

  const { label, diagnostic } = describeManagementError(result.error);
  const incompatible =
    result.error?.incompatible ?? result.packageResult?.incompatible ?? [];
  const output = result.packageResult?.output;
  return (
    <Notice tone="error">
      <p className="font-medium">{t("extensions.dsh.install.result.failed")}</p>
      <p>{label}</p>
      {diagnostic ? (
        <p className="break-words text-ink-body">{diagnostic}</p>
      ) : null}
      {result.pendingBuilds?.length ? (
        <p>
          {t("extensions.dsh.install.pendingBuilds", {
            packages: result.pendingBuilds.join(", "),
          })}
        </p>
      ) : null}
      {incompatible.map((plugin) => (
        <p key={`${plugin.name}@${plugin.version}`}>
          {t("extensions.dsh.install.incompatible", {
            name: plugin.name,
            version: plugin.version,
            runtime: plugin.runtimeVersion,
          })}
        </p>
      ))}
      {output ? (
        <InstallOutput
          output={output}
          truncated={result.packageResult?.truncated ?? false}
          logPath={result.packageResult?.logPath ?? ""}
        />
      ) : null}
    </Notice>
  );
};

const InspectionPanel = ({
  inspection,
  installing,
  onInstall,
  onDismiss,
}: {
  inspection: DshSpecInspection;
  installing: boolean;
  onInstall: () => void;
  onDismiss: () => void;
}) => {
  const { t } = useTranslation();

  if (inspection.status === "refused") {
    return (
      <Notice tone="error">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="error">{t("extensions.dsh.install.refused")}</Badge>
          <span className="font-medium">
            {t(
              `extensions.dsh.install.problem.${inspection.problem}` as Parameters<
                typeof t
              >[0],
            )}
          </span>
        </div>
        {inspection.reason ? (
          <p className="break-words text-ink-body">{inspection.reason}</p>
        ) : null}
      </Notice>
    );
  }

  const name = [inspection.name, inspection.version && `@${inspection.version}`]
    .filter(Boolean)
    .join("");
  return (
    <div className="space-y-2 rounded-md border border-surface-border bg-surface-soft px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="success">{t("extensions.dsh.install.accepted")}</Badge>
        <Badge variant="metaOutline">
          {t(`extensions.dsh.install.kind.${inspection.kind}`)}
        </Badge>
        {name ? (
          <span className="font-mono text-sm font-medium text-ink-heading">
            {name}
          </span>
        ) : null}
      </div>
      {inspection.description ? (
        <p className="text-xs text-ink-body">{inspection.description}</p>
      ) : null}
      {inspection.host ? (
        <p className="text-xs text-ink-meta">
          {t("extensions.dsh.install.from", { host: inspection.host })}
        </p>
      ) : null}
      {inspection.bundle === null ? (
        <p className="text-xs text-ink-meta">
          {t("extensions.dsh.install.bundleUnknown")}
        </p>
      ) : null}
      <div className="flex items-center gap-2 pt-1">
        <Button size="sm" disabled={installing} onClick={onInstall}>
          {installing ? <Spinner /> : null}
          {t("extensions.dsh.install.install")}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={installing}
          onClick={onDismiss}
        >
          {t("extensions.dsh.install.cancel")}
        </Button>
        {installing ? (
          <span className="text-xs text-ink-meta">
            {t("extensions.dsh.install.installing")}
          </span>
        ) : null}
      </div>
    </div>
  );
};

/**
 * Install a dsh bundle the dsh way: ``inspect`` reads what the spec names,
 * the user confirms, ``installBundle`` runs dsh's own pnpm path. The result's
 * ``application`` (``applied`` / ``restart-required`` / ``overridden`` /
 * ``failed``) decides the notice; a failure keeps pnpm's output one click away.
 */
export const DshInstallForm = ({
  onInstalled,
}: {
  /** Called after an install that changed the profile, to re-read the list. */
  onInstalled: () => Promise<void> | void;
}) => {
  const { t } = useTranslation();
  const [spec, setSpec] = useState("");
  const [step, setStep] = useState<Step>({ kind: "idle" });
  const [inspectError, setInspectError] = useState<string | null>(null);

  const busy = step.kind === "inspecting" || step.kind === "installing";

  const edit = (value: string) => {
    setSpec(value);
    // What gets confirmed must be what was inspected.
    if (step.kind === "inspected" || step.kind === "done") {
      setStep({ kind: "idle" });
    }
    setInspectError(null);
  };

  const inspect = async (event: FormEvent) => {
    event.preventDefault();
    const value = spec.trim();
    if (!value || busy) return;
    setStep({ kind: "inspecting" });
    setInspectError(null);
    try {
      const inspection = await dshPluginsApi.inspect(value);
      setStep({ kind: "inspected", spec: value, inspection });
    } catch (error) {
      setStep({ kind: "idle" });
      setInspectError(describeDshApiError(error).message);
    }
  };

  const install = async () => {
    if (step.kind !== "inspected" || step.inspection.status !== "accepted") {
      return;
    }
    const { spec: confirmed, inspection } = step;
    setStep({ kind: "installing", spec: confirmed, inspection });
    let result: DshChangeResult;
    try {
      result = await dshPluginsApi.installBundle(confirmed, { enabled: true });
    } catch (error) {
      result = {
        changed: false,
        application: "failed",
        stage: "install",
        target: confirmed,
        error: {
          code: "operation-error",
          diagnostic: describeDshApiError(error).message,
        },
      };
    }
    setStep({ kind: "done", result });
    if (changeTone(result) !== "error") {
      setSpec("");
      await onInstalled();
    }
  };

  return (
    <div className="space-y-3">
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => void inspect(e)}
      >
        <Input
          value={spec}
          onChange={(e) => edit(e.target.value)}
          disabled={busy}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          aria-label={t("extensions.dsh.install.specLabel")}
          placeholder={t("extensions.dsh.install.placeholder")}
          className="font-mono"
        />
        <Button
          type="submit"
          variant="outline"
          size="sm"
          disabled={!spec.trim() || busy}
          loading={step.kind === "inspecting"}
        >
          {t("extensions.dsh.install.inspect")}
        </Button>
      </form>

      {inspectError ? (
        <p role="alert" className="break-words text-xs text-error-text">
          {t("extensions.dsh.install.inspectFailed", { error: inspectError })}
        </p>
      ) : null}

      {step.kind === "inspected" || step.kind === "installing" ? (
        <InspectionPanel
          inspection={step.inspection}
          installing={step.kind === "installing"}
          onInstall={() => void install()}
          onDismiss={() => setStep({ kind: "idle" })}
        />
      ) : null}

      {step.kind === "done" ? <InstallResult result={step.result} /> : null}
    </div>
  );
};
