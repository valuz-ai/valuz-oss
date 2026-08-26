/**
 * The data-ref shape (finance-components.md §3.2/§3.4) and its parameter
 * resolution across the three layers a param can come from: a literal baked
 * in at generation time, a Host-injected value read from render context, or
 * interactive local `$state`. Resolution is total and eager — a ref is
 * either fully resolvable or it isn't; there is no "resolve what we can,
 * fetch anyway" mode, because a partially-resolved param set silently
 * becomes a different (and wrong) query.
 *
 * See `index.ts` for why this module is destined for OSS, not the edition.
 * This file in particular has no edition-specific concept at all — it never
 * even sees a source registry.
 */

export interface DataRefRefresh {
  /**
   * Component-declared poll interval in seconds (§3.5). Floored against the
   * source's registered `minIntervalSec` at scheduling time, not here —
   * this module only parses what a data ref *asked for*, it never knows
   * what any source actually allows.
   */
  interval: number;
}

export interface HostParamRef {
  $host: string;
}

export interface StateParamRef {
  $state: string;
}

export type DataRefParamValue =
  string | number | boolean | HostParamRef | StateParamRef;

export interface DataRef {
  source: string;
  params: Record<string, DataRefParamValue>;
  refresh?: DataRefRefresh;
  /**
   * Canonical shape the slot should be filled with (shape-system.md §5).
   * Needed only when the source produces more than one shape (e.g. kline →
   * ChartData or Collection<MetricItem>); a single-shape source resolves
   * without it. The edition validates it against the source's declared
   * `produces` — this module only carries it.
   */
  shape?: string;
}

export function isHostParam(value: DataRefParamValue): value is HostParamRef {
  return typeof value === "object" && value !== null && "$host" in value;
}

export function isStateParam(value: DataRefParamValue): value is StateParamRef {
  return typeof value === "object" && value !== null && "$state" in value;
}

function isLiteralParam(value: unknown): value is string | number | boolean {
  return (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  );
}

function isParamValue(value: unknown): value is DataRefParamValue {
  if (isLiteralParam(value)) return true;
  if (typeof value !== "object" || value === null) return false;
  const rec = value as Record<string, unknown>;
  const keys = Object.keys(rec);
  if (keys.length !== 1) return false;
  if (typeof rec.$host === "string") return true;
  if (typeof rec.$state === "string") return true;
  return false;
}

/**
 * Parse an untrusted value (e.g. a model-generated A2UI payload's `data`
 * field) into a `DataRef`, or `null` if it doesn't match the shape. Every
 * param's layer tag is validated, not cast — a malformed `$host`/`$state`
 * entry (extra keys, non-string path) fails parsing rather than silently
 * being treated as a literal object param nothing downstream expects.
 */
export function parseDataRef(value: unknown): DataRef | null {
  if (typeof value !== "object" || value === null) return null;
  const rec = value as Record<string, unknown>;
  if (typeof rec.source !== "string" || rec.source.length === 0) return null;
  if (
    typeof rec.params !== "object" ||
    rec.params === null ||
    Array.isArray(rec.params)
  ) {
    return null;
  }

  const params: Record<string, DataRefParamValue> = {};
  for (const [key, raw] of Object.entries(
    rec.params as Record<string, unknown>,
  )) {
    if (!isParamValue(raw)) return null;
    params[key] = raw;
  }

  if (rec.shape !== undefined) {
    if (typeof rec.shape !== "string" || rec.shape.length === 0) return null;
  }
  const shape = rec.shape as string | undefined;

  if (rec.refresh === undefined) {
    return { source: rec.source, params, ...(shape ? { shape } : {}) };
  }
  if (typeof rec.refresh !== "object" || rec.refresh === null) return null;
  const refreshRec = rec.refresh as Record<string, unknown>;
  const interval = refreshRec.interval;
  if (
    typeof interval !== "number" ||
    !Number.isFinite(interval) ||
    interval <= 0
  ) {
    return null;
  }
  return {
    source: rec.source,
    params,
    refresh: { interval },
    ...(shape ? { shape } : {}),
  };
}

export type ResolvedParamValue = string | number | boolean;
export type ResolvedParams = Record<string, ResolvedParamValue>;

export interface RenderContext {
  /**
   * Host render-context values (artifact-binding-and-host-runtime.md §4),
   * e.g. the current company page's canonical `securityId`.
   */
  host?: Record<string, ResolvedParamValue | undefined>;
  /** Surface-local interactive state (§3.6), e.g. a screener form's current formula. */
  state?: Record<string, ResolvedParamValue | undefined>;
}

export interface MissingParam {
  /** The param key on the data ref (e.g. "symbol"). */
  key: string;
  layer: "host" | "state";
  /** The `$host`/`$state` path that was requested and not found. */
  path: string;
}

export interface ParamsResolved {
  ok: true;
  params: ResolvedParams;
}

export interface ParamsMissing {
  ok: false;
  missing: MissingParam[];
}

export type ParamsResolution = ParamsResolved | ParamsMissing;

/**
 * Resolve every param's layer. A ref is only fetchable when every param
 * resolves — Host params in particular must never fall back to a guessed
 * literal (§3.4: "Host 参数缺失时组件进 empty 态并说明缺什么,不回退到字面
 * 量猜测"), so any single missing `$host`/`$state` collapses the whole
 * resolution to `ok: false` rather than a partially-filled query.
 */
export function resolveParams(
  params: Record<string, DataRefParamValue>,
  ctx: RenderContext,
): ParamsResolution {
  const resolved: ResolvedParams = {};
  const missing: MissingParam[] = [];

  for (const [key, value] of Object.entries(params)) {
    if (isHostParam(value)) {
      const found = ctx.host?.[value.$host];
      if (found === undefined) {
        missing.push({ key, layer: "host", path: value.$host });
      } else {
        resolved[key] = found;
      }
    } else if (isStateParam(value)) {
      const found = ctx.state?.[value.$state];
      if (found === undefined) {
        missing.push({ key, layer: "state", path: value.$state });
      } else {
        resolved[key] = found;
      }
    } else {
      resolved[key] = value;
    }
  }

  if (missing.length > 0) return { ok: false, missing };
  return { ok: true, params: resolved };
}

/**
 * Human-readable reason for the empty state a missing-param ref renders
 * (§3.4) — never surfaced as an error, since the component itself is fine,
 * it just isn't bound to anything yet.
 */
export function describeMissingParams(missing: MissingParam[]): string {
  return missing
    .map(
      (m) =>
        `${m.key}: missing ${m.layer === "host" ? "$host" : "$state"} "${m.path}"`,
    )
    .join("; ");
}
