import type {
  DshBundleInfo,
  DshChangeResult,
  DshLocalizedText,
  DshManagementError,
} from "@valuz/core";
import { t } from "@valuz/shared/i18n";

/**
 * Bundles Valuz and dsh themselves rely on. They are shown as 「内置」 and are
 * neither toggleable nor removable here, regardless of what ``listBundles``
 * says (dsh also reports them as ``readOnlyReason: "management-required"``
 * once its manager has started; this list keeps them locked before that).
 */
export const MANAGED_DSH_BUNDLES: ReadonlySet<string> = new Set([
  "@deepseek-ai/dsh-base",
  "@deepseek-ai/dsh-web-app",
  "@deepseek-ai/dsh-sdk-app",
  "valuz-dsh-bundle",
]);

/**
 * Installation-provided bundles with a process surface of their own (ACP stdio,
 * one-shot runner, a second minimal SDK). Valuz's dsh sessions already serve
 * SDK JSON-RPC from this profile, so the backend refuses to switch these on;
 * mirrors ``SURFACE_BUNDLES`` in ``modules/dsh_plugins/manager.py``.
 */
export const INCOMPATIBLE_DSH_BUNDLES: ReadonlySet<string> = new Set([
  "@deepseek-ai/dsh-acp-app",
  "@deepseek-ai/dsh-headless",
  "@deepseek-ai/dsh-sdk-minimal",
]);

/** Cannot be switched on in Valuz's managed profile. */
export const isIncompatibleBundle = (bundle: DshBundleInfo): boolean =>
  INCOMPATIBLE_DSH_BUNDLES.has(bundle.name) && !bundle.enabled;

/** Built-in: Valuz-managed, or one dsh itself refuses to switch off / remove. */
export const isLockedBundle = (bundle: DshBundleInfo): boolean =>
  MANAGED_DSH_BUNDLES.has(bundle.name) || bundle.readOnlyReason !== undefined;

/** Removable only when dsh says so (a profile-owned dependency) and it is not built-in. */
export const isRemovableBundle = (bundle: DshBundleInfo): boolean =>
  bundle.removable && !isLockedBundle(bundle);

/**
 * Pick display text for ``locale``. dsh keys translations by lowercase language
 * id with a required ``en`` fallback; a bare string is literal text.
 */
export function localizedText(
  text: DshLocalizedText | undefined,
  locale: string,
): string | undefined {
  if (text === undefined) return undefined;
  if (typeof text === "string") return text || undefined;
  const lower = locale.toLowerCase();
  return text[lower] ?? text[lower.split("-")[0] ?? ""] ?? text.en ?? undefined;
}

export const bundleTitle = (bundle: DshBundleInfo, locale: string): string =>
  localizedText(bundle.meta?.title, locale) ?? bundle.name;

export const bundleDescription = (
  bundle: DshBundleInfo,
  locale: string,
): string | undefined =>
  localizedText(bundle.meta?.description, locale) ??
  (bundle.description || undefined);

const KNOWN_ERROR_CODES: ReadonlySet<string> = new Set([
  "management-required",
  "unaddressable",
  "unknown-plugin",
  "invalid-spec",
  "ambiguous-install",
  "not-bundle",
  "not-removable",
  "stop-profile",
  "bundle-in-use",
  "stale-approval",
  "incompatible-version",
  "operation-error",
]);

/** A dsh ``ManagementError`` as a localized label plus dsh's own diagnostic. */
export function describeManagementError(
  error: DshManagementError | undefined,
): {
  label: string;
  diagnostic?: string;
} {
  if (!error) return { label: t("extensions.dsh.errorCode.operation-error") };
  const label = KNOWN_ERROR_CODES.has(error.code)
    ? t(`extensions.dsh.errorCode.${error.code}` as Parameters<typeof t>[0])
    : t("extensions.dsh.errorCode.other", { code: error.code });
  return error.diagnostic ? { label, diagnostic: error.diagnostic } : { label };
}

/** One line for a toast: ``label`` or ``label: diagnostic``. */
export function managementErrorLine(
  error: DshManagementError | undefined,
): string {
  const { label, diagnostic } = describeManagementError(error);
  return diagnostic ? `${label}: ${diagnostic}` : label;
}

export type ChangeTone = "success" | "warning" | "error";

/**
 * Classify the outcome of a bundle toggle / removal. dsh answers a refused or
 * failed change with HTTP 200, so the transport never throws for it.
 */
export function changeTone(result: DshChangeResult): ChangeTone {
  switch (result.application) {
    case "applied":
      return "success";
    case "restart-required":
    case "overridden":
      return "warning";
    default:
      return "error";
  }
}
