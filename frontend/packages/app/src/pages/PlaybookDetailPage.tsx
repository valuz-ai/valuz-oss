/**
 * Playbook detail — the definition and everything that has run from it.
 *
 * Mirrors the automation detail page's shape (breadcrumb, title row, then a
 * two-column body whose columns scroll independently) because the two answer
 * the same question about different objects. It is deliberately a separate
 * page rather than a shared one: a playbook is a versioned document that runs
 * on demand, an automation is a schedule, and the two are expected to grow
 * apart.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ChevronRight, ListChecks, Play } from "lucide-react";
import { toast } from "sonner";
import { Button, EmptyState, PageLoader, StatusPill } from "@valuz/ui";
import {
  playbooksApi,
  useEntityOrigin,
  useTranslation,
  type PlaybookDetail,
  type PlaybookRun,
} from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";
import { formatCreatedAt } from "@valuz/app/components";

type I18nKey = Parameters<ReturnType<typeof useTranslation>["t"]>[0];
const k = (key: string) => key as I18nKey;

/** Runs carry more states than the pill knows; collapse to its vocabulary. */
function pillStatusOf(status: PlaybookRun["status"]): string {
  switch (status) {
    case "completed":
      return "completed";
    case "failed":
      return "failed";
    case "stopped":
      return "stopped";
    case "queued":
      return "pending";
    default:
      // planning / running / waiting_approval all read as "in flight"
      return "running";
  }
}

export const PlaybookDetailPage = () => {
  const { playbookId = "" } = useParams<{ playbookId: string }>();
  // Pinned before any fetch: on a multi-target edition the definition lives on
  // one backend and an unpinned id resolves to the local one.
  useEntityOrigin(playbookId, "playbook");
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { t } = useTranslation();
  const { setHideHeader, setContentInnerClassName } = useProjectOutlet();

  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<PlaybookDetail | null>(null);
  const [runs, setRuns] = useState<PlaybookRun[]>([]);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    setHideHeader(true);
    setContentInnerClassName("p-0");
    return () => {
      setHideHeader(false);
      setContentInnerClassName(undefined);
    };
  }, [setHideHeader, setContentInnerClassName]);

  const loadAll = useCallback(async () => {
    if (!playbookId) return;
    setLoading(true);
    try {
      const [detailRes, runsRes] = await Promise.all([
        playbooksApi.get(playbookId),
        playbooksApi.listRuns({ definitionId: playbookId }),
      ]);
      setDetail(detailRes);
      setRuns(runsRes);
    } catch (error) {
      toast.error(String(error));
    } finally {
      setLoading(false);
    }
  }, [playbookId]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // Where the back arrow goes: whoever linked here says so, otherwise the
  // playbook library.
  const backTarget = searchParams.get("from") ?? "/playbooks";
  const backLabel =
    backTarget === "/playbooks" ? t(k("playbook.title")) : t(k("common.back"));

  const sortedRuns = useMemo(
    () => [...runs].sort((a, b) => b.created_at - a.created_at),
    [runs],
  );

  const handleRun = useCallback(async () => {
    if (!detail) return;
    setRunning(true);
    try {
      const run = await playbooksApi.run(detail.definition.id, {});
      if (run.session_id) navigate(`/conversation/${run.session_id}`);
      else await loadAll();
    } catch (error) {
      toast.error(String(error));
    } finally {
      setRunning(false);
    }
  }, [detail, loadAll, navigate]);

  if (loading) return <PageLoader />;
  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState variant="plain" title={t(k("playbook.notFound"))} />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-card">
      {/* Breadcrumb — same position and weight as the automation detail. */}
      <div className="flex min-w-0 shrink-0 items-center gap-2 px-5 pt-5 text-sm leading-5">
        <button
          type="button"
          onClick={() => navigate(backTarget)}
          className="inline-flex shrink-0 items-center gap-1 text-ink-meta transition-colors hover:text-ink-heading"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          <span>{backLabel}</span>
        </button>
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
        <span className="min-w-0 truncate font-medium text-ink-heading">
          {t(k("playbook.detailTitle"))}
        </span>
      </div>

      <div className="flex min-h-0 w-full flex-1 flex-col overflow-hidden px-5">
        <div className="flex shrink-0 items-start justify-between pt-4 pb-5">
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold text-ink-heading">
              {detail.definition.name}
            </h1>
            <p className="mt-1 flex items-center gap-2 text-sm text-ink-meta">
              <span>
                {t(k("playbook.versionLabel"), {
                  version: String(detail.definition.current_version),
                })}
              </span>
              <span>·</span>
              <span>{formatCreatedAt(detail.definition.updated_at)}</span>
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2 pt-1">
            <Button size="sm" onClick={handleRun} loading={running}>
              <Play className="h-3.5 w-3.5" />
              {t(k("playbook.run"))}
            </Button>
          </div>
        </div>

        {/* Two columns, each scrolling on its own — runs left, content right. */}
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_360px] overflow-hidden border-t border-surface-border/70">
          <div className="flex min-w-0 flex-col overflow-hidden pt-5">
            <h2 className="mb-3 shrink-0 pr-8 text-sm font-medium text-ink-meta">
              {t(k("playbook.executionHistory"))}
            </h2>
            {sortedRuns.length > 0 ? (
              <div className="min-h-0 flex-1 overflow-y-auto pb-6 pr-8">
                {sortedRuns.map((run) => (
                  <button
                    key={run.id}
                    type="button"
                    onClick={() => {
                      if (run.task_id) navigate(`/tasks/${run.task_id}`);
                      else if (run.session_id)
                        navigate(`/conversation/${run.session_id}`);
                    }}
                    disabled={!run.task_id && !run.session_id}
                    className="mb-1 flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-surface-soft disabled:cursor-default disabled:hover:bg-transparent"
                  >
                    <span className="flex min-w-0 flex-col">
                      <span className="truncate text-sm text-ink-heading">
                        {formatCreatedAt(run.created_at)}
                      </span>
                      <span className="truncate text-xs text-ink-meta">
                        {t(k("playbook.versionLabel"), {
                          version: String(run.definition_version),
                        })}
                      </span>
                    </span>
                    <StatusPill
                      status={pillStatusOf(run.status)}
                      label={t(k(`playbook.runStatus.${run.status}`))}
                    />
                  </button>
                ))}
              </div>
            ) : (
              <div className="flex flex-1 justify-center py-8">
                <EmptyState
                  variant="plain"
                  title={t(k("playbook.noExecutions"))}
                  icon={<ListChecks className="h-5 w-5" />}
                />
              </div>
            )}
          </div>

          <div className="flex min-w-0 flex-col overflow-hidden border-l border-surface-border pb-6 pl-6 pt-5 text-sm">
            <h2 className="mb-3 shrink-0 text-sm font-medium text-ink-meta">
              {t(k("playbook.content"))}
            </h2>
            <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-[#f7f8fa] bg-surface-soft/40 p-4">
              <p className="whitespace-pre-wrap text-sm leading-6 text-ink-body">
                {detail.current_version.content}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
