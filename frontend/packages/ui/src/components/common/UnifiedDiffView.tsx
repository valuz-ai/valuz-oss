/**
 * A unified-diff string with +/- background colouring.
 *
 * Deliberately plain: no syntax highlighting, no line numbers — the goal is a
 * quick visual scan, not a code-review surface. Shared so that every place
 * the product shows "what changed" looks the same, whether that is a turn's
 * file edits or two versions of a skill.
 */
import type { FC } from "react";
import { useMemo } from "react";
import { createPatch } from "diff";

export interface UnifiedDiffViewProps {
  diff: string;
}

export const UnifiedDiffView: FC<UnifiedDiffViewProps> = ({ diff }) => {
  const lines = useMemo(() => diff.split("\n"), [diff]);
  return (
    <div className="overflow-x-auto rounded-md border border-surface-border bg-surface-soft text-2xs">
      <pre className="m-0 px-3 py-2 font-mono leading-relaxed">
        {lines.map((line, idx) => {
          // Skip the patch's own file headers — they were stripped by
          // ``createPatch`` to "" but the leading "===" + "---"/"+++"
          // separators still appear; render them muted so they don't
          // visually compete with the actual content.
          if (line.startsWith("+++") || line.startsWith("---")) {
            return (
              <span
                key={idx}
                className="block whitespace-pre-wrap text-ink-meta"
              >
                {line || " "}
              </span>
            );
          }
          if (line.startsWith("@@")) {
            return (
              <span
                key={idx}
                className="block whitespace-pre-wrap text-brand/80"
              >
                {line}
              </span>
            );
          }
          if (line.startsWith("+")) {
            return (
              <span
                key={idx}
                className="block whitespace-pre-wrap bg-success/10 text-success"
              >
                {line || " "}
              </span>
            );
          }
          if (line.startsWith("-")) {
            return (
              <span
                key={idx}
                className="block whitespace-pre-wrap bg-error-light text-error-text"
              >
                {line || " "}
              </span>
            );
          }
          return (
            <span key={idx} className="block whitespace-pre-wrap text-ink-body">
              {line || " "}
            </span>
          );
        })}
      </pre>
    </div>
  );
};

export interface TwoSidedDiffViewProps {
  /** Shown as the patch's file name. */
  path: string;
  before: string;
  after: string;
  beforeLabel?: string;
  afterLabel?: string;
}

/**
 * The two-sided form, for callers that hold both texts rather than a patch.
 * Building the patch here keeps the ``diff`` package a dependency of this
 * package alone — an app-layer page should not have to take it on to show
 * what changed.
 */
export const TwoSidedDiffView: FC<TwoSidedDiffViewProps> = ({
  path,
  before,
  after,
  beforeLabel = "",
  afterLabel = "",
}) => {
  const patch = useMemo(
    () => createPatch(path, before, after, beforeLabel, afterLabel),
    [path, before, after, beforeLabel, afterLabel],
  );
  return <UnifiedDiffView diff={patch} />;
};
