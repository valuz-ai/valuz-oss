import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ChevronRight, Download, FolderOpen, History, Trash2 } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  DeleteConfirmDialog,
  Input,
  SettingsRow,
  SettingsSection,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
} from "@valuz/ui";
import {
  backupApi,
  useTranslation,
  type BackupConfig,
  type BackupFileEntry,
  type BackupFrequency,
  type BackupVersionInfo,
} from "@valuz/core";
import { usePlatform } from "@valuz/app/platform";

const FREQUENCIES: BackupFrequency[] = ["manual", "every_6h", "daily", "weekly"];

const formatBytes = (n: number): string => {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
};

const formatTime = (ms: number | null | undefined): string =>
  ms ? new Date(ms).toLocaleString() : "—";

/** Inline one-level file browser over a version's manifest. */
const VersionFileBrowser = ({ versionId }: { versionId: string }) => {
  const { t } = useTranslation();
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<BackupFileEntry[]>([]);

  useEffect(() => {
    backupApi
      .listFiles(versionId, path)
      .then((res) => setEntries(res.entries))
      .catch(() => toast.error(t("settings.backup.loadFailed")));
  }, [versionId, path, t]);

  const crumbs = path ? path.split("/") : [];
  return (
    <div className="mt-2 rounded-lg border border-surface-border bg-surface-muted/30 p-2 text-xs">
      <div className="mb-1 flex flex-wrap items-center gap-1 text-ink-meta">
        <button className="hover:text-ink-heading" onClick={() => setPath("")}>
          {t("settings.backup.filesRoot")}
        </button>
        {crumbs.map((seg, i) => (
          <span key={`${seg}-${i}`} className="flex items-center gap-1">
            <ChevronRight className="h-3 w-3" />
            <button
              className="hover:text-ink-heading"
              onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}
            >
              {seg}
            </button>
          </span>
        ))}
      </div>
      {entries.length === 0 ? (
        <div className="py-2 text-ink-meta">{t("settings.backup.filesEmpty")}</div>
      ) : (
        <ul className="max-h-48 overflow-auto">
          {entries.map((entry) => (
            <li
              key={entry.path}
              className="flex items-center justify-between gap-2 rounded px-1 py-0.5 hover:bg-surface-muted"
            >
              {entry.kind === "dir" ? (
                <button
                  className="flex min-w-0 items-center gap-1.5 text-left hover:text-ink-heading"
                  onClick={() => setPath(entry.path)}
                >
                  <FolderOpen className="h-3.5 w-3.5 shrink-0 text-ink-meta" />
                  <span className="truncate">{entry.name}</span>
                </button>
              ) : (
                <span className="min-w-0 truncate">{entry.name}</span>
              )}
              <span className="flex shrink-0 items-center gap-2 text-ink-meta">
                {formatBytes(entry.size)}
                {entry.kind === "file" && (
                  <a
                    href={backupApi.fileDownloadUrl(versionId, entry.path)}
                    download
                    className="hover:text-ink-heading"
                    title={t("settings.backup.downloadFile")}
                  >
                    <Download className="h-3.5 w-3.5" />
                  </a>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export const BackupSection = () => {
  const { t } = useTranslation();
  const platform = usePlatform();
  const [config, setConfig] = useState<BackupConfig | null>(null);
  const [versions, setVersions] = useState<BackupVersionInfo[]>([]);
  const [destinationDraft, setDestinationDraft] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Set after a restore is staged — drives the "restart to apply" banner.
  const [restoreStaged, setRestoreStaged] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const cfg = await backupApi.getConfig();
      setConfig(cfg);
      setDestinationDraft(cfg.destination);
      const res = await backupApi.listVersions();
      setVersions(res.versions);
    } catch {
      toast.error(t("settings.backup.loadFailed"));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  // While a run is in flight, poll progress until it finishes, then reload.
  useEffect(() => {
    if (!config?.run.running) return;
    pollRef.current = setInterval(async () => {
      try {
        const run = await backupApi.currentRun();
        setConfig((c) => (c ? { ...c, run } : c));
        if (!run.running) {
          if (pollRef.current) clearInterval(pollRef.current);
          void load();
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
      }
    }, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [config?.run.running, load]);

  const patch = async (p: Parameters<typeof backupApi.patchConfig>[0]) => {
    try {
      const cfg = await backupApi.patchConfig(p);
      setConfig(cfg);
      setDestinationDraft(cfg.destination);
    } catch (err) {
      toast.error(
        err instanceof Error && err.message
          ? err.message
          : t("settings.backup.saveFailed"),
      );
    }
  };

  const runNow = async () => {
    setBusy(true);
    try {
      const res = await backupApi.runNow();
      setConfig((c) => (c ? { ...c, run: res.run } : c));
      toast.success(t("settings.backup.runStarted"));
    } catch {
      toast.error(t("settings.backup.runFailed"));
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    if (!deleteTarget) return;
    try {
      await backupApi.deleteVersion(deleteTarget);
      toast.success(t("settings.backup.versionDeleted"));
      void load();
    } catch {
      toast.error(t("settings.backup.saveFailed"));
    } finally {
      setDeleteTarget(null);
    }
  };

  const doRestore = async () => {
    if (!restoreTarget) return;
    try {
      const res = await backupApi.restore(restoreTarget);
      if (res.staged) {
        setRestoreStaged(true);
        toast.success(t("settings.backup.restoreStaged"));
      }
    } catch (err) {
      toast.error(
        err instanceof Error && err.message
          ? err.message
          : t("settings.backup.restoreFailed"),
      );
    } finally {
      setRestoreTarget(null);
    }
  };

  const pickDestination = async () => {
    const path = await platform.selectDirectory();
    if (!path) return;
    setDestinationDraft(path);
    await patch({ destination: path });
  };

  if (!config) {
    return (
      <SettingsSection
        title={t("settings.tab.backup.label")}
        desc={t("settings.tab.backup.desc")}
      >
        <div className="py-8 text-sm text-ink-meta">{t("common.loading")}</div>
      </SettingsSection>
    );
  }

  const running = config.run.running;
  const restoreOk = config.restore_result?.ok;

  return (
    <SettingsSection
      title={t("settings.tab.backup.label")}
      desc={t("settings.tab.backup.desc")}
    >
      {!config.supported && (
        <div className="mb-5 rounded-lg border border-warning-border bg-warning-light px-4 py-3 text-sm text-warning-text">
          {t("settings.backup.unsupported")}
        </div>
      )}

      {restoreStaged && (
        <div className="mb-5 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning-border bg-warning-light px-4 py-3 text-sm text-warning-text">
          <span>{t("settings.backup.restoreStagedBanner")}</span>
          {platform.relaunchApp && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => void platform.relaunchApp?.()}
            >
              {t("settings.backup.restartNow")}
            </Button>
          )}
        </div>
      )}

      {config.restore_result && (
        <div
          className={
            restoreOk
              ? "mb-5 rounded-lg border border-success-border bg-success-light px-4 py-3 text-sm text-success-text"
              : "mb-5 rounded-lg border border-error-border bg-error-light px-4 py-3 text-sm text-error-text"
          }
        >
          {restoreOk
            ? t("settings.backup.restoreDone", {
                version: String(config.restore_result.version_id ?? ""),
              })
            : t("settings.backup.restoreError", {
                error: String(config.restore_result.error ?? ""),
              })}
        </div>
      )}

      {/* config card */}
      <Card className="mb-5 rounded-xl shadow-xs">
        <CardContent className="py-5">
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.backup.enabledLabel")}
            desc={t("settings.backup.enabledDesc")}
          >
            <Switch
              checked={config.enabled}
              disabled={!config.supported}
              onCheckedChange={(v) => void patch({ enabled: v })}
            />
          </SettingsRow>
          <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.backup.frequencyLabel")}
            desc={t("settings.backup.frequencyDesc")}
          >
            <Select
              value={config.frequency}
              disabled={!config.supported}
              onValueChange={(v) => void patch({ frequency: v as BackupFrequency })}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FREQUENCIES.map((f) => (
                  <SelectItem key={f} value={f}>
                    {t(`settings.backup.frequency.${f}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingsRow>
          <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
          <div className="flex flex-col gap-2">
            <div>
              <div className="text-sm font-medium text-ink-heading">
                {t("settings.backup.destinationLabel")}
              </div>
              <div className="mt-0.5 text-xs text-ink-meta">
                {t("settings.backup.destinationDesc")}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Input
                value={destinationDraft}
                disabled={!config.supported}
                onChange={(e) => setDestinationDraft(e.target.value)}
                className="flex-1 font-mono text-xs"
              />
              {platform.isElectron && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!config.supported}
                  onClick={() => void pickDestination()}
                >
                  {t("settings.backup.chooseFolder")}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={
                  !config.supported || destinationDraft === config.destination
                }
                onClick={() => void patch({ destination: destinationDraft })}
              >
                {t("common.save")}
              </Button>
            </div>
          </div>
          <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.backup.scopeManagedProjects")}
            desc={t("settings.backup.scopeManagedProjectsDesc")}
          >
            <Switch
              checked={config.scope.managed_projects}
              disabled={!config.supported}
              onCheckedChange={(v) =>
                void patch({ scope: { ...config.scope, managed_projects: v } })
              }
            />
          </SettingsRow>
          <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.backup.scopeExternalProjects")}
            desc={t("settings.backup.scopeExternalProjectsDesc")}
          >
            <Switch
              checked={config.scope.external_projects}
              disabled={!config.supported}
              onCheckedChange={(v) =>
                void patch({ scope: { ...config.scope, external_projects: v } })
              }
            />
          </SettingsRow>
          <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.backup.scopeUserSkills")}
            desc={t("settings.backup.scopeUserSkillsDesc")}
          >
            <Switch
              checked={config.scope.user_skills}
              disabled={!config.supported}
              onCheckedChange={(v) =>
                void patch({ scope: { ...config.scope, user_skills: v } })
              }
            />
          </SettingsRow>
          <div className="mt-4 text-xs text-ink-meta">
            {t("settings.backup.secretsExcludedNote")}
          </div>
        </CardContent>
      </Card>

      {/* status card */}
      <Card className="mb-5 rounded-xl shadow-xs">
        <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4 text-sm">
          <div className="flex flex-col gap-1">
            <div className="text-ink-body">
              {t("settings.backup.lastRun")}: {formatTime(config.last_run.at)}
              {config.last_run.status === "failed" && (
                <span className="ml-2 text-error-text">
                  {t("settings.backup.lastRunFailed")}
                </span>
              )}
            </div>
            <div className="text-xs text-ink-meta">
              {t("settings.backup.nextRun")}: {formatTime(config.next_run_at)} ·{" "}
              {t("settings.backup.usage", {
                count: String(config.versions_count),
                size: formatBytes(config.total_bytes),
              })}
            </div>
          </div>
          <Button
            size="sm"
            disabled={!config.supported || running || busy}
            onClick={() => void runNow()}
          >
            {running
              ? t("settings.backup.running", {
                  size: formatBytes(config.run.processed_bytes),
                })
              : t("settings.backup.runNow")}
          </Button>
        </CardContent>
      </Card>

      {/* versions */}
      <div className="mb-2 flex items-center gap-2 text-sm font-medium text-ink-heading">
        <History className="h-4 w-4" />
        {t("settings.backup.versionsTitle")}
      </div>
      {versions.length === 0 ? (
        <div className="rounded-xl border border-dashed border-surface-border px-4 py-8 text-center text-sm text-ink-meta">
          {t("settings.backup.versionsEmpty")}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {versions.map((v) => (
            <Card key={v.id} className="rounded-xl shadow-xs">
              <CardContent className="py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <button
                    className="flex min-w-0 flex-col items-start text-left"
                    onClick={() => setExpanded(expanded === v.id ? null : v.id)}
                  >
                    <span className="text-sm font-medium text-ink-heading">
                      {formatTime(v.created_at)}
                      {v.kind !== "scheduled" && (
                        <span className="ml-2 rounded bg-surface-muted px-1.5 py-0.5 text-micro text-ink-meta">
                          {t(`settings.backup.kind.${v.kind}`)}
                        </span>
                      )}
                    </span>
                    <span className="text-xs text-ink-meta">
                      {formatBytes(v.total_bytes)} (+{formatBytes(v.new_bytes)}) ·{" "}
                      {t("settings.backup.versionCounts", {
                        sessions: String(v.counts.sessions),
                        documents: String(v.counts.documents),
                        skills: String(v.counts.skills),
                      })}
                    </span>
                  </button>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!config.supported || running}
                      onClick={() => setRestoreTarget(v.id)}
                    >
                      {t("settings.backup.restore")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => setDeleteTarget(v.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-ink-meta" />
                    </Button>
                  </div>
                </div>
                {expanded === v.id && <VersionFileBrowser versionId={v.id} />}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <DeleteConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={t("settings.backup.deleteTitle")}
        description={t("settings.backup.deleteDesc")}
        itemName={deleteTarget ?? undefined}
        onConfirm={() => void doDelete()}
      />
      <DeleteConfirmDialog
        open={restoreTarget !== null}
        onOpenChange={(open) => !open && setRestoreTarget(null)}
        title={t("settings.backup.restoreTitle")}
        description={t("settings.backup.restoreDesc")}
        itemName={restoreTarget ?? undefined}
        confirmLabel={t("settings.backup.restoreConfirm")}
        onConfirm={() => void doRestore()}
      />
    </SettingsSection>
  );
};
