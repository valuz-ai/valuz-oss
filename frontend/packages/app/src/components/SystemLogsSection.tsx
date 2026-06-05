/**
 * Settings → Preferences → 服务日志.
 *
 * Originally lived as a top-level ``/system`` route with its own
 * sidebar entry. Moved into the Settings page (under "Preferences")
 * so it sits alongside other operator-facing surfaces (账号 / 模型 /
 * 快捷键 / 关于) — backend introspection is a preference, not a
 * primary navigation target.
 *
 * Renders three pieces stacked, all driven by ``useSystemStore``:
 *
 *   1. ``SystemStatusCard`` — pid / port / version / kernel pin /
 *      uptime / active sessions / data dir / log path + warnings.
 *   2. ``SystemLogToolbar``  — search / level filter / follow tail
 *      / collapse repeats / copy / clear.
 *   3. ``SystemLogList``     — scrolling viewport with KV-aware
 *      rendering, group-collapse, and "jump to latest".
 *
 * Layout adapts to the Settings page's bounded scroll area: the log
 * list takes ``min(60vh, 600px)`` so it doesn't push the surrounding
 * SettingsSection off-screen, and has its own internal scroll.
 */

import { useCallback, useEffect, useMemo, useRef } from "react";
import { toast } from "sonner";
import {
  SystemLogList,
  SystemLogToolbar,
  SystemStatusCard,
  type SystemLogListHandle,
} from "@valuz/ui";
import {
  useSystemActions,
  useSystemLogs,
  useSystemStatus,
  useSystemStore,
  useTranslation,
} from "@valuz/core";
import type { LogLevel, LogLine } from "@valuz/shared";

export const SystemLogsSection = () => {
  const { t } = useTranslation();
  // ── Status ─────────────────────────────────────────────────────
  useSystemStatus();
  const status = useSystemStore((s) => s.status);
  const statusError = useSystemStore((s) => s.statusError);
  const statusLoading = useSystemStore((s) => s.statusLoading);
  const refreshStatus = useSystemStore((s) => s.refreshStatus);

  // ── Logs ───────────────────────────────────────────────────────
  const { available: logsAvailable } = useSystemLogs();
  const logs = useSystemStore((s) => s.logs);
  const view = useSystemStore((s) => s.view);
  const setSearchQuery = useSystemStore((s) => s.setSearchQuery);
  const toggleLevel = useSystemStore((s) => s.toggleLevel);
  const setFollowTail = useSystemStore((s) => s.setFollowTail);
  const setCollapseRepeats = useSystemStore((s) => s.setCollapseRepeats);
  const clearLogs = useSystemStore((s) => s.clearLogs);

  const systemActions = useSystemActions();

  // ── Filter pipeline ────────────────────────────────────────────
  //   1. case-insensitive substring search across msg + raw + KV
  //   2. compute per-level counts (post-search, pre-level-filter)
  //   3. drop levels the user toggled off
  const { filtered, levelCounts } = useMemo(() => {
    const q = view.searchQuery.trim().toLowerCase();
    const matches = (line: LogLine): boolean => {
      if (!q) return true;
      if (line.msg.toLowerCase().includes(q)) return true;
      if (line.raw.toLowerCase().includes(q)) return true;
      for (const [k, v] of Object.entries(line.fields)) {
        if (k.toLowerCase().includes(q)) return true;
        const sv = typeof v === "string" ? v : JSON.stringify(v);
        if (sv && sv.toLowerCase().includes(q)) return true;
      }
      return false;
    };

    const counts: Record<LogLevel, number> = {
      DEBUG: 0,
      INFO: 0,
      WARNING: 0,
      ERROR: 0,
      CRITICAL: 0,
      RAW: 0,
    };
    const out: LogLine[] = [];
    for (const line of logs) {
      if (!matches(line)) continue;
      counts[line.level] = (counts[line.level] ?? 0) + 1;
      if (view.enabledLevels.has(line.level)) {
        out.push(line);
      }
    }
    return { filtered: out, levelCounts: counts };
  }, [logs, view.searchQuery, view.enabledLevels]);

  const listRef = useRef<SystemLogListHandle>(null);

  const handleCopy = useCallback(() => {
    const text = filtered
      .map(
        (line) =>
          `${line.ts} [${line.level}] ${line.logger} - ${line.msg}` +
          (Object.keys(line.fields).length > 0
            ? " " + JSON.stringify(line.fields)
            : ""),
      )
      .join("\n");
    if (text.length === 0) {
      toast.info(t("settings.systemLogs.noLogsToCopy"));
      return;
    }
    void navigator.clipboard.writeText(text).then(
      () =>
        toast.success(
          t("settings.systemLogs.copiedLines", {
            count: String(filtered.length),
          }),
        ),
      () => toast.error(t("settings.systemLogs.copyFailed")),
    );
  }, [filtered, t]);

  const handleUserScrolledAway = useCallback(() => {
    if (view.followTail) setFollowTail(false);
  }, [view.followTail, setFollowTail]);

  // Re-pin the viewport to the bottom whenever the user re-enables
  // follow-tail (on the toolbar toggle).
  useEffect(() => {
    if (view.followTail) listRef.current?.scrollToBottom();
  }, [view.followTail]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <SystemStatusCard
        status={status}
        loading={statusLoading}
        error={statusError}
        actions={systemActions.available ? systemActions : null}
        onRefresh={() => void refreshStatus()}
      />

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-surface-border bg-card">
        <SystemLogToolbar
          searchQuery={view.searchQuery}
          onSearchChange={setSearchQuery}
          enabledLevels={view.enabledLevels}
          onToggleLevel={toggleLevel}
          followTail={view.followTail}
          onToggleFollowTail={setFollowTail}
          collapseRepeats={view.collapseRepeats}
          onToggleCollapseRepeats={setCollapseRepeats}
          levelCounts={levelCounts}
          totalShown={filtered.length}
          totalBuffered={logs.length}
          bufferFull={logs.length >= 2000}
          onClear={clearLogs}
          onCopy={handleCopy}
        />

        {!logsAvailable && (
          <div className="border-b border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-800">
            {t("settings.systemLogs.unavailable")}
            {status?.log_path ? `（${status.log_path}）` : ""}。
          </div>
        )}

        <SystemLogList
          ref={listRef}
          lines={filtered}
          searchQuery={view.searchQuery}
          collapseRepeats={view.collapseRepeats}
          followTail={view.followTail}
          onUserScrolledAway={handleUserScrolledAway}
        />
      </div>
    </div>
  );
};
