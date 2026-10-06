/**
 * ``extension_manager`` operation record → what the confirmation card shows.
 *
 * The record's ``preview`` carries what the user is asked to approve (the
 * inspected manifest summary for an install / dev link, the plugin being
 * removed, the publish scope). Its exact nesting is the backend tool's to
 * choose, so every field is looked up defensively: the preview itself, then
 * the nested ``manifest`` / ``inspection`` / ``plugin`` objects it may wrap,
 * then the operation's ``input_payload``. Anything missing is simply absent;
 * the card renders what it has.
 */
import type { LocalizedText } from "@valuz/core";
import { getLocale } from "@valuz/shared/i18n";
import { localizedText } from "../settings/extensions/third-party-helpers";

export type ExtensionAction =
  | "install"
  | "dev_link"
  | "uninstall"
  | "publish"
  | "generic";

export interface ExtensionOperationLike {
  operation_type?: string;
  preview?: Record<string, unknown> | null;
  input_payload?: Record<string, unknown> | null;
}

export interface ExtensionOperationView {
  action: ExtensionAction;
  id: string;
  name: string;
  version: string;
  /** The version already installed, when this install updates it. */
  existingVersion: string;
  publisher: string;
  source: string;
  sha256: string;
  path: string;
  permissions: string[];
  addedPermissions: string[];
  requires: string[];
  unmetRequires: string[];
  hasBackend: boolean;
  scope: string;
  targets: string[];
  notes: string;
  /** ``null`` when the operation does not say (only an uninstall does). */
  purgeData: boolean | null;
}

const ACTIONS: readonly string[] = [
  "install",
  "dev_link",
  "uninstall",
  "publish",
];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asText = (value: unknown): string =>
  typeof value === "string" ? value : "";

const asList = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];

/** The operation's action: the record's type, then the preview, then the tool's. */
export const extensionAction = (
  operation: ExtensionOperationLike,
  toolAction?: string | null,
): ExtensionAction => {
  const fromType = (operation.operation_type ?? "").split(/[./]/).pop() ?? "";
  const candidates = [
    fromType,
    asText(operation.preview?.action),
    asText(operation.preview?.change),
    toolAction ?? "",
  ].map((value) => value.replace(/-/g, "_"));
  const hit = candidates.find((value) => ACTIONS.includes(value));
  return (hit as ExtensionAction | undefined) ?? "generic";
};

export const extensionOperationView = (
  operation: ExtensionOperationLike,
  toolAction?: string | null,
): ExtensionOperationView => {
  const preview = isRecord(operation.preview) ? operation.preview : {};
  const sources: Record<string, unknown>[] = [
    preview,
    ...["manifest", "inspection", "plugin"]
      .map((key) => preview[key])
      .filter(isRecord),
    isRecord(operation.input_payload) ? operation.input_payload : {},
  ];
  const pick = (...keys: string[]): unknown => {
    for (const source of sources) {
      for (const key of keys) {
        const value = source[key];
        if (value === undefined || value === null || value === "") continue;
        if (Array.isArray(value) && value.length === 0) continue;
        return value;
      }
    }
    return undefined;
  };

  const locale = getLocale();
  const id = asText(pick("id", "extension_id", "plugin_id"));
  const name = localizedText(pick("name") as LocalizedText | undefined, locale);

  const publisherRaw = pick("publisher");
  const publisher = isRecord(publisherRaw)
    ? asText(publisherRaw.name)
    : asText(publisherRaw);

  const sourceRaw = pick("source");
  let source = "";
  if (isRecord(sourceRaw)) {
    source = [
      asText(sourceRaw.kind),
      asText(sourceRaw.scope),
      asText(sourceRaw.path) || asText(sourceRaw.url),
    ]
      .filter(Boolean)
      .join(" · ");
  } else {
    source = asText(sourceRaw);
  }

  const existing = pick("existing");
  const existingVersion = isRecord(existing)
    ? asText(existing.version)
    : asText(pick("updated_from", "existing_version"));

  const purge = pick("purge_data");

  return {
    action: extensionAction(operation, toolAction),
    id,
    name,
    version: asText(pick("version")),
    existingVersion,
    publisher,
    source,
    sha256: asText(pick("sha256", "expected_sha256")),
    path: asText(pick("path", "source_path", "dev_path")),
    permissions: asList(pick("permissions")),
    addedPermissions: asList(pick("added_permissions")),
    requires: asList(pick("requires")),
    unmetRequires: asList(pick("unmet_requires")),
    hasBackend: pick("has_backend") === true,
    scope: asText(pick("scope")),
    targets: asList(pick("distribution_ids", "targets")),
    notes: asText(pick("notes")),
    purgeData: typeof purge === "boolean" ? purge : null,
  };
};
