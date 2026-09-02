/**
 * A skill's version history, as a view of its own.
 *
 * The first cut hung this list off the detail page's metadata sidebar, which
 * read as an afterthought: versions are a way of looking at the skill, level
 * with its files, not a property of it. So the page has tabs and this owns
 * one of them — a timeline on the left, and on the right the selected
 * version's own files, rendered with the same tree-plus-viewer shape the
 * files tab uses.
 *
 * Comparison reuses the conversation's diff visuals (``UnifiedDiffView`` and
 * the ``diff`` package already in ``@valuz/ui``) so "what changed between two
 * versions of a skill" and "what this turn changed" look like one idea.
 */
import { useCallback, useEffect, useState } from "react";
import { FileText, GitCompare, Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { skillsApi, useTranslation } from "@valuz/core";
import type { SkillVersionDetail, SkillVersionItem } from "@valuz/core";
import { Button, SkillVersionList, TwoSidedDiffView } from "@valuz/ui";
import type { SkillVersionEntry } from "@valuz/ui";
import { t as _t } from "@valuz/shared/i18n";

import { isBinaryContent } from "./skill-file-preview";

interface SkillVersionsTabProps {
  skillId: string;
  /** Refetch the skill after a restore — the library copy changed. */
  onRestored?: () => void;
}

const toEntry = (item: SkillVersionItem): SkillVersionEntry => ({
  revisionId: item.revision_id,
  versionNo: item.version_no,
  createdAt: item.created_at,
  byteSize: item.byte_size,
  isCurrent: item.is_current,
  createdBy: item.created_by ?? null,
});

export function SkillVersionsTab({
  skillId,
  onRestored,
}: SkillVersionsTabProps) {
  const { t } = useTranslation();
  const [versions, setVersions] = useState<SkillVersionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillVersionDetail | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [contentLoading, setContentLoading] = useState(false);
  const [compareId, setCompareId] = useState<string | null>(null);
  const [compareContent, setCompareContent] = useState<string | null>(null);
  const [restoringId, setRestoringId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await skillsApi.listVersions(skillId);
      setVersions(res.items);
      // Newest first is how the list reads; open on it.
      const newest = res.items[res.items.length - 1];
      setSelectedId((current) =>
        current && res.items.some((v) => v.revision_id === current)
          ? current
          : (newest?.revision_id ?? null),
      );
    } catch {
      setVersions([]);
    } finally {
      setLoading(false);
    }
  }, [skillId]);

  useEffect(() => {
    void load();
  }, [load]);

  // The selected version's own file list — which files a version holds is
  // part of what changed, so it comes from that version's archive.
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    void skillsApi
      .getVersion(skillId, selectedId)
      .then((res) => {
        if (cancelled) return;
        setDetail(res);
        setSelectedPath((current) =>
          current && res.files.some((f) => f.path === current)
            ? current
            : (res.files.find((f) => f.path.endsWith("SKILL.md"))?.path ??
              res.files[0]?.path ??
              null),
        );
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      });
    return () => {
      cancelled = true;
    };
  }, [skillId, selectedId]);

  useEffect(() => {
    if (!selectedId || !selectedPath) {
      setContent("");
      return;
    }
    let cancelled = false;
    setContentLoading(true);
    void skillsApi
      .readVersionFile(skillId, selectedId, selectedPath)
      .then((res) => {
        if (!cancelled) {
          setContent(
            isBinaryContent(res.content)
              ? _t("skill.versionBinaryFile" as Parameters<typeof _t>[0])
              : res.content,
          );
        }
      })
      .catch(() => {
        if (!cancelled) setContent("");
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [skillId, selectedId, selectedPath]);

  // The comparison side. A file missing from the other version is not an
  // error — it is the diff (added or removed), so it reads as empty.
  useEffect(() => {
    if (!compareId || !selectedPath) {
      setCompareContent(null);
      return;
    }
    let cancelled = false;
    void skillsApi
      .readVersionFile(skillId, compareId, selectedPath)
      .then((res) => {
        if (!cancelled) setCompareContent(res.content);
      })
      .catch(() => {
        if (!cancelled) setCompareContent("");
      });
    return () => {
      cancelled = true;
    };
  }, [skillId, compareId, selectedPath]);

  const selected = versions.find((v) => v.revision_id === selectedId) ?? null;
  const compare = versions.find((v) => v.revision_id === compareId) ?? null;

  const showDiff = compareContent !== null && !!selectedPath;

  const handleRestore = useCallback(
    async (revisionId: string) => {
      setRestoringId(revisionId);
      try {
        const res = await skillsApi.restoreVersion(skillId, revisionId);
        toast.success(
          _t("skill.versionRestored" as Parameters<typeof _t>[0], {
            version: String(res.version_no),
          }),
        );
        await load();
        onRestored?.();
      } catch (cause) {
        toast.error(
          cause instanceof Error
            ? cause.message
            : _t("skill.versionRestoreFailed" as Parameters<typeof _t>[0]),
        );
      } finally {
        setRestoringId(null);
      }
    },
    [skillId, load, onRestored],
  );

  const toggleCompare = () => {
    if (compareId) {
      setCompareId(null);
      return;
    }
    // Default to the version before the selected one — "what did this change".
    const index = versions.findIndex((v) => v.revision_id === selectedId);
    const previous = index > 0 ? versions[index - 1] : null;
    setCompareId(previous?.revision_id ?? null);
    if (!previous) {
      toast.info(
        _t("skill.versionNothingToCompare" as Parameters<typeof _t>[0]),
      );
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-brand" />
      </div>
    );
  }

  if (versions.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="max-w-md text-center text-sm leading-relaxed text-ink-meta">
          {t("skill.versionsEmpty" as Parameters<typeof t>[0])}
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* Timeline */}
      <div className="w-[280px] shrink-0 overflow-y-auto border-r border-surface-border p-3">
        <SkillVersionList
          bare
          versions={versions.map(toEntry)}
          selectedId={selectedId}
          compareId={compareId}
          onSelect={setSelectedId}
          restoringId={restoringId}
        />
      </div>

      {/* The selected version: its own files, and optionally the diff */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between gap-3 border-b border-surface-border px-4 py-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0 font-mono text-xs text-ink-heading">
              v{selected?.version_no ?? "?"}
            </span>
            {compare ? (
              <span className="shrink-0 text-2xs text-ink-meta">
                {t("skill.versionComparedWith" as Parameters<typeof t>[0], {
                  version: String(compare.version_no),
                })}
              </span>
            ) : null}
            <span className="truncate text-xs text-ink-label">
              {selectedPath ?? "—"}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-2xs"
              onClick={toggleCompare}
            >
              <GitCompare className="mr-1 h-3 w-3" />
              {compareId
                ? t("skill.versionStopCompare" as Parameters<typeof t>[0])
                : t("skill.versionCompare" as Parameters<typeof t>[0])}
            </Button>
            {selected && !selected.is_current ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-2xs"
                disabled={!!restoringId}
                onClick={() => void handleRestore(selected.revision_id)}
                title={t("skill.versionRestoreHint" as Parameters<typeof t>[0])}
              >
                {restoringId === selected.revision_id ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : (
                  <RotateCcw className="mr-1 h-3 w-3" />
                )}
                {t("skill.versionRestore" as Parameters<typeof t>[0])}
              </Button>
            ) : null}
          </div>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* That version's files */}
          <div className="w-[220px] shrink-0 overflow-y-auto border-r border-surface-border p-2">
            {(detail?.files ?? []).map((file) => (
              <button
                key={file.path}
                type="button"
                onClick={() => setSelectedPath(file.path)}
                className={`flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-2xs transition-colors ${
                  selectedPath === file.path
                    ? "bg-brand-light text-brand"
                    : "text-ink-body hover:bg-surface-2"
                }`}
              >
                <FileText className="h-3 w-3 shrink-0 text-ink-label" />
                <span className="truncate">{file.path}</span>
              </button>
            ))}
          </div>

          <div className="min-w-0 flex-1 overflow-auto p-4">
            {contentLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-ink-meta" />
            ) : showDiff && selectedPath ? (
              <TwoSidedDiffView
                path={selectedPath}
                before={compareContent ?? ""}
                after={content}
                beforeLabel={compare ? `v${compare.version_no}` : ""}
                afterLabel={selected ? `v${selected.version_no}` : ""}
              />
            ) : (
              <pre className="m-0 whitespace-pre-wrap break-words font-mono text-2xs leading-relaxed text-ink-body">
                {content}
              </pre>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
