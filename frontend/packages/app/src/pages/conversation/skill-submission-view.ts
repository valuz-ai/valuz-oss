/**
 * ``skill.submit`` operation record → what the submission card renders.
 *
 * The card used to derive its state from a staging-directory scan, which
 * both confirm and dismiss delete — so after a reload a saved skill and a
 * discarded one looked identical. The operation record carries all of it
 * (the staged file list the user is approving, the version the save would
 * create, the collision they have to resolve, the terminal outcome), so
 * this is a pure mapping with no polling behind it.
 *
 * Shared by ConversationPage and the task-detail follow-up chat, which
 * render the same card from different hosts.
 */
import type { OperationView } from "@valuz/core";
import type {
  SkillSubmissionConflict,
  SkillSubmissionFileNode,
  SkillSubmissionState,
} from "@valuz/ui";

export interface SkillSubmissionView {
  slug: string;
  summary?: string;
  changeKind?: "create" | "update";
  state: SkillSubmissionState;
  errorMessage?: string;
  stagedFiles?: SkillSubmissionFileNode[];
  stagingPath?: string;
  nextVersion?: number | null;
  savedVersion?: number | null;
  conflictKind: SkillSubmissionConflict;
}

const CONFLICTS: SkillSubmissionConflict[] = [
  "none",
  "same_source",
  "diverged",
  "unprepared_collision",
];

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asFiles(value: unknown): SkillSubmissionFileNode[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out: SkillSubmissionFileNode[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    const path = asString(record.path);
    if (!path) continue;
    out.push({
      path,
      type: record.type === "directory" ? "directory" : "file",
      size: asNumber(record.size),
    });
  }
  return out;
}

/** Operation state → card state. ``executing`` reads as "confirming" so a
 *  card refreshed mid-save shows a spinner rather than an idle button. */
function cardState(state: string, busy: boolean): SkillSubmissionState {
  switch (state) {
    case "succeeded":
      return "confirmed";
    case "cancelled":
      return "dismissed";
    case "executing":
      return "confirming";
    case "failed":
    case "stale":
    case "expired":
    case "superseded":
      // Recoverable by the user: the draft is still staged and the record
      // is confirmable again once they resolve what went wrong (pick a
      // collision mode, ask the agent to re-submit stale content).
      return "error";
    default:
      return busy ? "confirming" : "pending";
  }
}

export function skillSubmissionView(
  operation: OperationView,
  busy?: "confirm" | "cancel" | null,
): SkillSubmissionView {
  const preview = operation.preview ?? {};
  const result = operation.result_payload ?? {};
  const rawConflict = asString(preview.conflict_kind) ?? "none";
  const conflictKind = (
    CONFLICTS.includes(rawConflict as SkillSubmissionConflict)
      ? rawConflict
      : "none"
  ) as SkillSubmissionConflict;

  let state = cardState(operation.state, busy === "confirm");
  if (busy === "cancel") state = "dismissing";

  // A confirmed save renders the version it produced; before that, the
  // version it would produce.
  const savedVersion = asNumber(result.version_no);
  const nextVersion = asNumber(preview.next_version);

  return {
    slug: asString(result.slug) ?? asString(preview.slug) ?? "",
    summary: asString(preview.summary),
    changeKind: preview.change_kind === "update" ? "update" : "create",
    state,
    errorMessage: operation.error_message ?? undefined,
    stagedFiles: asFiles(preview.files),
    stagingPath: asString(preview.staging_path),
    nextVersion,
    savedVersion,
    conflictKind,
  };
}
