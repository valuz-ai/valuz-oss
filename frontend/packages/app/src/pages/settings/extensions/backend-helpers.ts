import type {
  BackendExtensionRow,
  BackendExtensionStatus,
} from "@valuz/core";

type BadgeVariant = "success" | "error" | "brand" | "metaNeutral";

/** Status tags use the semantic (state) colours; a switched-off plugin is neutral. */
export const BACKEND_STATUS_VARIANT: Record<
  BackendExtensionStatus,
  BadgeVariant
> = {
  active: "success",
  failed: "error",
  pending: "brand",
  disabled: "metaNeutral",
  disposed: "metaNeutral",
};

/** i18n key suffix of a known status; ``disposed`` (unloaded) reads as 已停用 too. */
export const BACKEND_STATUS_LABEL: Record<
  BackendExtensionStatus,
  "active" | "failed" | "pending" | "disabled"
> = {
  active: "active",
  failed: "failed",
  pending: "pending",
  disabled: "disabled",
  disposed: "disabled",
};

/**
 * Whether the plugin is running enabled in this process. ``disabled`` (switched
 * off at start) and ``disposed`` (unloaded) are not; every other status
 * (active, failed, pending) means it was enabled.
 */
export const bootEnabled = (row: BackendExtensionRow): boolean =>
  row.status !== "disabled" && row.status !== "disposed";

/**
 * A change is recorded but not yet running: what the next start will do
 * differs from what this boot did, or the user just toggled it here.
 * Required plugins cannot be toggled, so they are never pending.
 */
export const isPendingRestart = (
  row: BackendExtensionRow,
  toggled: ReadonlySet<string>,
): boolean =>
  !row.required && (row.desiredEnabled !== bootEnabled(row) || toggled.has(row.id));
