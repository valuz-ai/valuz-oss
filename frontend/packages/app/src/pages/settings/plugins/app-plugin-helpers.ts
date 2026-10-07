import { ApiError } from "@valuz/core";
import type {
  LocalizedText,
  AppPluginSource,
  AppPluginStatus,
} from "@valuz/core";

type BadgeVariant = "success" | "warning" | "error" | "brand" | "metaNeutral";

/** Status tags use the semantic (state) colours; a switched-off plugin is neutral. */
export const APP_PLUGIN_STATUS_VARIANT: Record<
  AppPluginStatus,
  BadgeVariant
> = {
  enabled: "success",
  disabled: "metaNeutral",
  incompatible: "error",
  broken: "error",
  "requires-unmet": "warning",
  blocked: "error",
};

const KNOWN_STATUSES: readonly string[] = [
  "enabled",
  "disabled",
  "incompatible",
  "broken",
  "requires-unmet",
  "blocked",
];

export const isKnownStatus = (status: string): status is AppPluginStatus =>
  KNOWN_STATUSES.includes(status);

/** A status the backend cannot run: turning it on is meaningless. */
export const isUnrunnable = (status: string): boolean =>
  status === "incompatible" || status === "broken" || status === "blocked";

/**
 * The text for ``locale`` out of a manifest value that is either a plain
 * string or a per-language object: exact locale, then its language, then
 * English / Chinese, then whatever is there.
 */
export const localizedText = (
  value: LocalizedText | null | undefined,
  locale: string,
): string => {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  const language = locale.split("-")[0];
  const candidates = [
    value[locale],
    ...Object.entries(value)
      .filter(([key]) => key.split("-")[0] === language)
      .map(([, text]) => text),
    value["en-US"],
    value["zh-CN"],
    ...Object.values(value),
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate) return candidate;
  }
  return "";
};

export const formatBytes = (bytes: number | null | undefined): string => {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) {
    return "";
  }
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
};

/** The server's ``detail`` (``ApiError.message``), or whatever was thrown. */
export const errorDetail = (error: unknown): string =>
  error instanceof Error ? error.message : String(error ?? "");

/** True when the backend says app plugins do not exist here. */
export const isUnavailableError = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 403 || error.status === 404);

/**
 * The validation messages a failed install / config write carries: the
 * ``errors`` list of an ``invalid_manifest`` / config 400, or FastAPI's own
 * ``[{loc, msg}]`` list. Empty when the body has none.
 */
export const serverErrorList = (error: unknown): string[] => {
  if (!(error instanceof ApiError) || !error.body) return [];
  let detail: unknown;
  try {
    detail = (JSON.parse(error.body) as { detail?: unknown }).detail;
  } catch {
    return [];
  }
  const raw: unknown = Array.isArray(detail)
    ? detail
    : detail && typeof detail === "object"
      ? (detail as { errors?: unknown }).errors
      : undefined;
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const item of raw) {
    if (typeof item === "string") {
      out.push(item);
    } else if (item && typeof item === "object") {
      const entry = item as { msg?: unknown; message?: unknown; loc?: unknown };
      const text =
        typeof entry.msg === "string"
          ? entry.msg
          : typeof entry.message === "string"
            ? entry.message
            : null;
      if (!text) continue;
      const where = Array.isArray(entry.loc)
        ? entry.loc.filter((part) => part !== "body").join(".")
        : "";
      out.push(where ? `${where}: ${text}` : text);
    }
  }
  return out;
};

/** Where the bytes came from, for the row's meta line (path or URL, if any). */
export const sourceLocation = (
  source: AppPluginSource,
  devPath?: string | null,
): string | null =>
  source.kind === "dev"
    ? (devPath ?? source.path ?? null)
    : source.kind === "url"
      ? (source.url ?? null)
      : source.kind === "file"
        ? (source.path ?? null)
        : null;

// ── Config form (generated from the manifest's ``config`` JSON Schema) ─────

export type ConfigFieldKind =
  "string" | "number" | "integer" | "boolean" | "enum" | "string-list";

export interface ConfigField {
  key: string;
  kind: ConfigFieldKind;
  label: string;
  description: string;
  required: boolean;
  defaultValue: unknown;
  /** Enum members, in schema order (values keep their JSON type). */
  options: unknown[];
}

/** What the form holds per field: text for inputs, a boolean for switches. */
export type ConfigDraft = Record<string, string | boolean>;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const kindOf = (property: Record<string, unknown>): ConfigFieldKind | null => {
  if (Array.isArray(property.enum) && property.enum.length > 0) return "enum";
  const type = Array.isArray(property.type)
    ? property.type.find((entry) => entry !== "null")
    : property.type;
  switch (type) {
    case "string":
    case "number":
    case "integer":
    case "boolean":
      return type;
    case "array": {
      const items = property.items;
      return isRecord(items) && items.type === "string" ? "string-list" : null;
    }
    default:
      return null;
  }
};

/**
 * The simple fields of a config schema, in declaration order. Properties of
 * a shape the form cannot edit (objects, arrays of objects…) are left out;
 * {@link buildConfigPayload} carries their stored values through untouched.
 */
export const parseConfigSchema = (
  schema: Record<string, unknown> | null | undefined,
  locale: string,
): ConfigField[] => {
  if (!schema || !isRecord(schema.properties)) return [];
  const required = new Set(
    Array.isArray(schema.required)
      ? schema.required.filter((key): key is string => typeof key === "string")
      : [],
  );
  const fields: ConfigField[] = [];
  for (const [key, raw] of Object.entries(schema.properties)) {
    if (!isRecord(raw)) continue;
    const kind = kindOf(raw);
    if (!kind) continue;
    fields.push({
      key,
      kind,
      label:
        localizedText(raw.title as LocalizedText | undefined, locale) || key,
      description: localizedText(
        raw.description as LocalizedText | undefined,
        locale,
      ),
      required: required.has(key),
      defaultValue: raw.default,
      options: kind === "enum" ? (raw.enum as unknown[]) : [],
    });
  }
  return fields;
};

const draftValue = (field: ConfigField, value: unknown): string | boolean => {
  if (field.kind === "boolean") return value === true;
  if (value === undefined || value === null) return "";
  if (field.kind === "string-list") {
    return Array.isArray(value) ? value.map(String).join("\n") : "";
  }
  return String(value);
};

/** The form's starting state: the stored value, else the schema default. */
export const initialConfigDraft = (
  fields: ConfigField[],
  values: Record<string, unknown>,
): ConfigDraft => {
  const draft: ConfigDraft = {};
  for (const field of fields) {
    const stored = values[field.key];
    draft[field.key] = draftValue(
      field,
      stored !== undefined ? stored : field.defaultValue,
    );
  }
  return draft;
};

const splitList = (text: string): string[] =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

const isBlank = (field: ConfigField, value: string | boolean | undefined) =>
  field.kind === "boolean"
    ? false
    : field.kind === "string-list"
      ? splitList(String(value ?? "")).length === 0
      : String(value ?? "").trim() === "";

export type ConfigErrorCode = "required" | "invalidNumber" | "invalidInteger";

/** Field key → what is wrong with it (empty when the draft can be saved). */
export const validateConfigDraft = (
  fields: ConfigField[],
  draft: ConfigDraft,
): Record<string, ConfigErrorCode> => {
  const errors: Record<string, ConfigErrorCode> = {};
  for (const field of fields) {
    const value = draft[field.key];
    if (isBlank(field, value)) {
      if (field.required) errors[field.key] = "required";
      continue;
    }
    if (field.kind === "number" || field.kind === "integer") {
      const parsed = Number(String(value).trim());
      if (!Number.isFinite(parsed)) {
        errors[field.key] = "invalidNumber";
      } else if (field.kind === "integer" && !Number.isInteger(parsed)) {
        errors[field.key] = "invalidInteger";
      }
    }
  }
  return errors;
};

/**
 * The ``values`` body for ``PUT /config``: the stored values (so keys the form
 * cannot edit survive), overlaid with every field the form owns. A cleared
 * optional field is dropped rather than sent empty.
 */
export const buildConfigPayload = (
  fields: ConfigField[],
  draft: ConfigDraft,
  stored: Record<string, unknown>,
): Record<string, unknown> => {
  const payload: Record<string, unknown> = { ...stored };
  for (const field of fields) {
    const value = draft[field.key];
    delete payload[field.key];
    if (field.kind === "boolean") {
      payload[field.key] = value === true;
      continue;
    }
    if (isBlank(field, value)) continue;
    const text = String(value);
    switch (field.kind) {
      case "string":
        payload[field.key] = text;
        break;
      case "number":
      case "integer":
        payload[field.key] = Number(text.trim());
        break;
      case "enum":
        payload[field.key] = field.options.find(
          (option) => String(option) === text,
        );
        break;
      case "string-list":
        payload[field.key] = splitList(text);
        break;
    }
  }
  return payload;
};
