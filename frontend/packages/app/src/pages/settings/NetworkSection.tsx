import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Copy } from "lucide-react";
import { toast } from "sonner";
import {
  Badge,
  Button,
  Card,
  CardContent,
  SettingsRow,
  SettingsSection,
} from "@valuz/ui";
import { useRunningRuns, useTranslation } from "@valuz/core";
import {
  DESKTOP_CAPABILITIES_CHANNEL,
  NETWORK_EGRESS_CHANNELS,
  NETWORK_EGRESS_EVENTS,
  type DesktopCapabilities,
  type EgressManagerStatus,
  type EgressSnapshot,
  type NetworkEgressCapability,
  type PublicEgressMode,
  type RuntimePhaseRecord,
} from "@valuz/desktop-network-egress/contracts";
import { buildEgressDiagnosticsExport } from "./network-diagnostics";
import {
  currentNetworkSnapshots,
  currentRuntimeActivities,
  isManagedNetworkMode,
  networkHealthDetailKey,
  networkRouteKey,
  networkRuntimeLabel,
  shouldShowNetworkDiagnosticsAction,
} from "./network-presentation";

type Health = EgressSnapshot["health"];

interface DesktopBridge {
  invoke<T>(channel: string, payload?: Record<string, unknown>): Promise<T>;
  on(event: string, handler: (payload: unknown) => void): void;
  off(event: string, handler: (payload: unknown) => void): void;
}

const bridge = (): DesktopBridge | null =>
  (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;

const overallHealth = (snapshots: EgressSnapshot[]): Health => {
  if (snapshots.some((item) => item.health === "failed")) return "failed";
  if (snapshots.some((item) => item.health === "degraded")) return "degraded";
  if (snapshots.some((item) => item.health === "healthy")) return "healthy";
  return "unknown";
};

const healthBadgeVariant: Record<
  Health,
  "metaNeutral" | "success" | "warning" | "error"
> = {
  unknown: "metaNeutral",
  healthy: "success",
  degraded: "warning",
  failed: "error",
};

export const NetworkSection = () => {
  const { t } = useTranslation();
  const { count: activeRunCount } = useRunningRuns();
  const [status, setStatus] = useState<EgressManagerStatus | null>(null);
  const [snapshots, setSnapshots] = useState<EgressSnapshot[]>([]);
  const [diagnostics, setDiagnostics] = useState<unknown[]>([]);
  const [runtimePhases, setRuntimePhases] = useState<RuntimePhaseRecord[]>([]);
  const [capability, setCapability] =
    useState<NetworkEgressCapability | null>(null);
  const [capabilityChecked, setCapabilityChecked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyMode, setBusyMode] = useState<PublicEgressMode | null>(null);

  const load = useCallback(async (silent = false) => {
    const desktop = bridge();
    if (!desktop) {
      setLoading(false);
      return;
    }
    try {
      const [nextStatus, nextSnapshots, nextDiagnostics, nextPhases] =
        await Promise.all([
          desktop.invoke<EgressManagerStatus>(NETWORK_EGRESS_CHANNELS.getStatus),
          desktop.invoke<EgressSnapshot[]>(NETWORK_EGRESS_CHANNELS.getSnapshots),
          desktop.invoke<unknown[]>(NETWORK_EGRESS_CHANNELS.getDiagnostics),
          desktop.invoke<RuntimePhaseRecord[]>(
            NETWORK_EGRESS_CHANNELS.getRuntimePhases,
          ),
        ]);
      setStatus(nextStatus);
      setSnapshots(nextSnapshots);
      setDiagnostics(nextDiagnostics);
      setRuntimePhases(nextPhases);
    } catch {
      if (!silent) toast.error(t("settings.network.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    const desktop = bridge();
    if (!desktop) {
      setCapabilityChecked(true);
      setLoading(false);
      return;
    }
    let disposed = false;
    let subscribed = false;
    let poll: number | undefined;
    const onChange = () => void load(true);
    const initialize = async () => {
      try {
        const capabilities = await desktop.invoke<DesktopCapabilities>(
          DESKTOP_CAPABILITIES_CHANNEL,
        );
        if (disposed) return;
        setCapability(capabilities.networkEgress);
        setCapabilityChecked(true);
        if (!capabilities.networkEgress.available) {
          setLoading(false);
          return;
        }
      } catch {
        if (!disposed) {
          setCapability(null);
          setCapabilityChecked(true);
          setLoading(false);
        }
        return;
      }
      await load();
      if (disposed) return;
      desktop.on(NETWORK_EGRESS_EVENTS.statusChanged, onChange);
      subscribed = true;
      poll = window.setInterval(() => void load(true), 3_000);
    };
    void initialize();
    return () => {
      disposed = true;
      if (poll !== undefined) window.clearInterval(poll);
      if (subscribed) {
        desktop.off(NETWORK_EGRESS_EVENTS.statusChanged, onChange);
      }
    };
  }, [load]);

  const activeSnapshots = useMemo(
    () => currentNetworkSnapshots(snapshots),
    [snapshots],
  );
  const health = useMemo(
    () =>
      status?.lastErrorCode ? "failed" : overallHealth(activeSnapshots),
    [activeSnapshots, status?.lastErrorCode],
  );
  const runtimeActivities = useMemo(
    () => currentRuntimeActivities(runtimePhases),
    [runtimePhases],
  );
  const healthLabel = t(`settings.network.health.${health}`);
  const managedMode = isManagedNetworkMode(status?.mode);
  const showDiagnosticsAction =
    !loading &&
    shouldShowNetworkDiagnosticsAction(
      health,
      snapshots.length > 0 ||
      diagnostics.length > 0 ||
      runtimePhases.length > 0 ||
        Boolean(status?.lastErrorCode),
    );

  const changeMode = async (mode: PublicEgressMode) => {
    const desktop = bridge();
    if (
      !desktop ||
      !status ||
      status.mode === mode ||
      !capability?.policy.userConfigurable ||
      !capability.policy.allowedModes.includes(mode)
    ) {
      return;
    }
    const interruptActiveRuns = activeRunCount > 0;
    if (
      interruptActiveRuns &&
      !window.confirm(
        t("settings.network.activeRunsConfirm", {
          count: activeRunCount,
        }),
      )
    ) {
      return;
    }
    if (
      !interruptActiveRuns &&
      mode === "off" &&
      !window.confirm(t("settings.network.offConfirm"))
    ) {
      return;
    }
    setBusyMode(mode);
    try {
      const next = await desktop.invoke<EgressManagerStatus>(
        NETWORK_EGRESS_CHANNELS.setMode,
        { mode, interruptActiveRuns },
      );
      setStatus(next);
      toast.success(
        mode === "off"
          ? t("settings.network.offEnabled")
          : t("settings.network.autoEnabled"),
      );
      await load();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const errorKey = message.includes("blocked_by_active_runs")
        ? "settings.network.activeRunsBlocked"
        : message.includes("interrupt_failed")
          ? "settings.network.activeRunsInterruptFailed"
          : message.includes("locked_by_environment")
            ? "settings.network.environmentLocked"
            : "settings.network.changeFailed";
      toast.error(t(errorKey));
    } finally {
      setBusyMode(null);
    }
  };

  const copyDiagnostics = async () => {
    const payload = JSON.stringify(
      buildEgressDiagnosticsExport(
        status,
        snapshots,
        diagnostics,
        runtimePhases,
      ),
      null,
      2,
    );
    try {
      await navigator.clipboard.writeText(payload);
      toast.success(t("settings.network.copied"));
    } catch {
      toast.error(t("settings.network.copyFailed"));
    }
  };

  if (!bridge()) {
    return (
      <SettingsSection
        title={t("settings.network.title")}
        desc={t("settings.network.desc")}
      >
        <p className="text-sm text-ink-meta">
          {t("settings.network.desktopOnly")}
        </p>
      </SettingsSection>
    );
  }

  if (capabilityChecked && !capability?.available) {
    return (
      <SettingsSection
        title={t("settings.network.title")}
        desc={t("settings.network.desc")}
      >
        <p className="text-sm text-ink-meta">
          {t("settings.network.canaryDisabled")}
        </p>
      </SettingsSection>
    );
  }

  const canSelectMode = capability?.policy.userConfigurable === true;
  const showAutoMode =
    capability?.policy.allowedModes.includes("auto") ||
    status?.mode === "auto" ||
    status?.mode === "direct";
  const showOffMode =
    capability?.policy.allowedModes.includes("off") || status?.mode === "off";

  return (
    <SettingsSection
      title={t("settings.network.title")}
      desc={t("settings.network.desc")}
    >
      {status && !status.enabled ? (
        <Card className="mb-5 rounded-xl shadow-xs">
          <CardContent className="py-4 text-sm text-ink-meta">
            {t("settings.network.canaryDisabled")}
          </CardContent>
        </Card>
      ) : (
        <section aria-labelledby="network-mode-heading" className="mb-6">
          <div className="mb-3">
            <h3
              id="network-mode-heading"
              className="text-sm font-semibold text-ink-heading"
            >
              {t("settings.network.modeSectionTitle")}
            </h3>
          </div>
          <Card className="rounded-xl shadow-xs">
            <CardContent className="divide-y divide-surface-border py-1">
              {showAutoMode && (
                <SettingsRow
                  className="px-0"
                  label={t("settings.network.autoLabel")}
                  desc={t("settings.network.autoDesc")}
                >
                  {managedMode ? (
                    <Badge variant="brand">
                      {t("settings.network.current")}
                    </Badge>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={
                        !status ||
                        !canSelectMode ||
                        status.emergencyOverride ||
                        busyMode !== null
                      }
                      loading={busyMode === "auto"}
                      onClick={() => void changeMode("auto")}
                    >
                      {t("settings.network.useAuto")}
                    </Button>
                  )}
                </SettingsRow>
              )}
              {showOffMode && (
                <SettingsRow
                  className="px-0"
                  label={t("settings.network.offLabel")}
                  desc={t("settings.network.offDesc")}
                >
                  {status?.mode === "off" ? (
                    <Badge variant="warning">
                      {t("settings.network.current")}
                    </Badge>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={!status || !canSelectMode || busyMode !== null}
                      loading={busyMode === "off"}
                      onClick={() => void changeMode("off")}
                    >
                      {t("settings.network.enableCompatibility")}
                    </Button>
                  )}
                </SettingsRow>
              )}
            </CardContent>
          </Card>
          {activeRunCount > 0 && (
            <p className="mt-2 text-xs leading-5 text-ink-meta" role="status">
              {t("settings.network.activeRunsHint", {
                count: activeRunCount,
              })}
            </p>
          )}
        </section>
      )}

      {status?.emergencyOverride && (
        <p className="mb-5 text-xs text-warning-text">
          {t("settings.network.environmentLocked")}
        </p>
      )}

      <section aria-labelledby="network-status-heading" className="mb-5">
        <div className="mb-3 flex items-start justify-between gap-4">
          <div>
            <h3
              id="network-status-heading"
              className="text-sm font-semibold text-ink-heading"
            >
              {t("settings.network.statusLabel")}
            </h3>
            <p className="mt-1 text-xs leading-5 text-ink-meta">
              {t("settings.network.statusDesc")}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {!loading &&
              (activeSnapshots.length > 0 || status?.lastErrorCode) && (
                <Badge variant={healthBadgeVariant[health]}>
                  {healthLabel}
                </Badge>
              )}
            {showDiagnosticsAction && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void copyDiagnostics()}
              >
                <Copy className="mr-1.5 h-3.5 w-3.5" />
                {t("settings.network.copyDiagnostics")}
              </Button>
            )}
          </div>
        </div>

        <Card className="rounded-xl shadow-xs">
          <CardContent className="p-0">
            {loading ? (
              <div className="px-5 py-8 text-center text-sm text-ink-meta">
                {t("settings.network.loading")}
              </div>
            ) : status && !status.enabled ? (
              <div className="flex flex-col items-center px-5 py-8 text-center">
                <Activity
                  aria-hidden="true"
                  className="mb-3 h-5 w-5 text-ink-muted"
                />
                <p className="text-sm font-medium text-ink-heading">
                  {t("settings.network.monitoringUnavailableTitle")}
                </p>
                <p className="mt-1 max-w-lg text-xs leading-5 text-ink-meta">
                  {t("settings.network.monitoringUnavailableDesc")}
                </p>
              </div>
            ) : status?.mode === "off" ? (
              <div className="flex flex-col items-center px-5 py-8 text-center">
                <Activity
                  aria-hidden="true"
                  className="mb-3 h-5 w-5 text-ink-muted"
                />
                <p className="text-sm font-medium text-ink-heading">
                  {t("settings.network.legacyMonitoringTitle")}
                </p>
                <p className="mt-1 max-w-lg text-xs leading-5 text-ink-meta">
                  {t("settings.network.legacyMonitoringDesc")}
                </p>
              </div>
            ) : activeSnapshots.length === 0 && runtimeActivities.length > 0 ? (
              <div className="divide-y divide-surface-border">
                {runtimeActivities.slice(0, 10).map((activity) => (
                  <article key={activity.id} className="px-5 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2">
                        <h4 className="font-medium text-ink-heading">
                          {networkRuntimeLabel(activity.runtime)}
                        </h4>
                        <Badge variant="metaNeutral">
                          {t(`settings.network.activity.${activity.stage}`)}
                        </Badge>
                      </div>
                      <span className="text-2xs text-ink-muted">
                        {t("settings.network.activity.elapsed", {
                          seconds: String(
                            Math.max(
                              0,
                              Math.round((Date.now() - activity.startedAt) / 1000),
                            ),
                          ),
                        })}
                      </span>
                    </div>
                    <p className="mt-2 text-xs leading-5 text-ink-meta">
                      {t("settings.network.activity.noRequestYet")}
                    </p>
                    {activity.targetOrigin && (
                      <p className="mt-1 break-all text-2xs text-ink-muted">
                        {activity.targetOrigin}
                      </p>
                    )}
                  </article>
                ))}
              </div>
            ) : activeSnapshots.length === 0 ? (
              <div className="flex flex-col items-center px-5 py-8 text-center">
                <Activity
                  aria-hidden="true"
                  className="mb-3 h-5 w-5 text-ink-muted"
                />
                <p className="text-sm font-medium text-ink-heading">
                  {t("settings.network.noConnectionsTitle")}
                </p>
                <p className="mt-1 max-w-lg text-xs leading-5 text-ink-meta">
                  {t("settings.network.noConnectionsDesc")}
                </p>
              </div>
            ) : (
              <div className="divide-y divide-surface-border">
                {activeSnapshots.slice(0, 10).map((item, index) => {
                  const timings = [
                    [t("settings.network.timing.resolve"), item.resolveMs],
                    [t("settings.network.timing.connect"), item.connectMs],
                    [t("settings.network.timing.response"), item.responseMs],
                    [
                      t("settings.network.timing.firstByte"),
                      item.firstByteMs,
                    ],
                    [t("settings.network.timing.total"), item.totalMs],
                  ].filter(
                    (entry): entry is [string, number] =>
                      entry[1] !== undefined,
                  );
                  return (
                    <article
                      key={`${item.runtime}-${item.targetOrigin}-${index}`}
                      className="px-5 py-4"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <h4 className="font-medium text-ink-heading">
                            {networkRuntimeLabel(item.runtime)}
                          </h4>
                          <Badge variant={healthBadgeVariant[item.health]}>
                            {t(`settings.network.health.${item.health}`)}
                          </Badge>
                        </div>
                        <span className="text-2xs text-ink-muted">
                          {t("settings.network.updatedAt", {
                            time: new Date(item.updatedAt).toLocaleTimeString(),
                          })}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-ink-body">
                        {t(networkRouteKey(item.route))}
                        <span aria-hidden="true"> · </span>
                        <span className="break-all text-ink-meta">
                          {item.targetOrigin}
                        </span>
                      </p>
                      <p className="mt-2 text-xs leading-5 text-ink-meta">
                        {t(networkHealthDetailKey(item))}
                      </p>
                      {timings.length > 0 && (
                        <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
                          {timings.map(([label, value]) => (
                            <div key={label} className="flex items-baseline gap-1">
                              <dt className="text-2xs text-ink-muted">
                                {label}
                              </dt>
                              <dd className="text-xs font-medium text-ink-body">
                                {value} ms
                              </dd>
                            </div>
                          ))}
                        </dl>
                      )}
                      {(item.fallbackCount > 0 || item.reconnectCount > 0) && (
                        <p className="mt-2 text-2xs text-warning-text">
                          {item.fallbackCount > 0 &&
                            t("settings.network.fallbackCount", {
                              count: String(item.fallbackCount),
                            })}
                          {item.fallbackCount > 0 &&
                            item.reconnectCount > 0 && (
                              <span aria-hidden="true"> · </span>
                            )}
                          {item.reconnectCount > 0 &&
                            t("settings.network.reconnectCount", {
                              count: String(item.reconnectCount),
                            })}
                        </p>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </section>
    </SettingsSection>
  );
};
