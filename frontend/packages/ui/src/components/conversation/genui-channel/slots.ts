/**
 * The data-slot path convention (finance-components.md §3.7 item 1): every
 * (source, resolved params) pair occupies exactly one DataModel path, so the
 * scheduler's merged polling and the host's `DataModel.set` on patch arrival
 * agree on where a slot lives without either side needing anything beyond
 * the source id and the resolved params.
 *
 * See `index.ts` for why this module is destined for OSS. This file has no
 * edition-specific concept — "source" here is just the opaque id a data ref
 * carries, never looked up against any registry.
 */

import type { ResolvedParams } from "./dataRef";

/**
 * FNV-1a 32-bit over the canonically-ordered params. Keys are sorted before
 * hashing — `JSON.stringify` preserves insertion order, and two data refs
 * that build the same params object field-by-field in a different order (a
 * near-certainty across independently-generated payloads) must still land on
 * the same slot, or merged polling silently splits into two pollers hitting
 * the same endpoint. A hash (rather than the canonical string itself) keeps
 * the path short and free of characters that would need JSON Pointer
 * escaping (`/`, `~`) if a param value happened to contain them.
 */
function paramsDigest(params: ResolvedParams, shape?: string): string {
  // The shape participates in the digest: one source+params can be mapped to
  // more than one canonical shape (shape-system.md §5 disambiguation), and
  // two refs asking for different shapes must not share a slot — the fetch
  // result of one is the wrong value for the other.
  const canonical = JSON.stringify([
    shape ?? null,
    Object.keys(params)
      .sort()
      .map((key) => [key, params[key]]),
  ]);
  let hash = 0x811c9dc5;
  for (let i = 0; i < canonical.length; i++) {
    hash ^= canonical.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/**
 * Deterministic DataModel path for (source, params): same input always
 * produces the same path; different params (including different key order,
 * different values) practically never collide within one surface's slot
 * set.
 */
export function slotPath(
  source: string,
  params: ResolvedParams,
  shape?: string,
): string {
  return `/data/${source}/${paramsDigest(params, shape)}`;
}
