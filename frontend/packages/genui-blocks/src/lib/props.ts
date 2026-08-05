import type { Trend } from "./schema";

/**
 * Readers for props that arrive from a model rather than from code.
 *
 * Blocks that carry *data* (a ranked list, a quote, a breadth count) are handed
 * whatever key the model reached for that turn — `changePct` one time,
 * `change_pct` or `pct` the next, a number where the schema says string. The
 * schema is the contract the prompt teaches; these readers are what keeps a
 * block rendering when the model misses it, which it does often enough that
 * tolerating aliases is cheaper than losing the block.
 *
 * Every reader is total: it returns an empty string / undefined rather than
 * throwing, so a malformed field degrades to a missing line instead of an
 * unmounted component.
 */

export function toArray(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (value === undefined || value === null) return [];
  return [value];
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

/**
 * A data item as a flat record.
 *
 * A2UI nests a component's own fields under `props`, so an item that is really
 * a component descriptor (`{component, props}`) has to be flattened before its
 * fields can be read. Anything else is returned as-is.
 */
export function readRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) return {};
  const isDescriptor =
    typeof value.component === "string" || typeof value.type === "string";
  const nested = value.props;
  return isDescriptor && isRecord(nested) ? { ...value, ...nested } : value;
}

export function readText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** First key that carries text. Keys are tried in order, blanks skipped. */
export function readTextFromKeys(
  record: Record<string, unknown>,
  keys: string[],
): string {
  for (const key of keys) {
    if (!(key in record)) continue;
    const text = readText(record[key]);
    if (text) return text;
  }
  return "";
}

/** A count that may arrive as `1422`, `"1422"` or `"1,422"`. */
export function readLooseNumber(value: unknown): number | undefined {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value.replace(/,/g, ""));
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

/**
 * Direction of a change, from either a stated trend or the figure itself.
 *
 * A signed percentage is the common case — the model writes `"+0.56%"` and
 * never says which way that points.
 */
export function inferTrend(value: string): Trend {
  const normalized = value.trim().toLowerCase();
  if (
    normalized.startsWith("+") ||
    normalized === "up" ||
    normalized === "rise" ||
    normalized === "positive"
  ) {
    return "up";
  }
  if (
    normalized.startsWith("-") ||
    normalized === "down" ||
    normalized === "fall" ||
    normalized === "negative"
  ) {
    return "down";
  }
  return "flat";
}

/** Grouped count, e.g. `1422` → `1,422`. */
export function formatCount(value: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
}
