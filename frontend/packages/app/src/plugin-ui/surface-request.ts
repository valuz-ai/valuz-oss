import type { UiRenderRequest } from "@valuz/core";

/**
 * What a plugin surface asks ``POST /v1/ui/render`` for: which occurrence of
 * the site (``instance``) and the slot context, reduced to what can cross the
 * wire.
 */

/** Ids in the slot context that tell one occurrence of a site from another. */
const INSTANCE_KEYS = [
  "sessionId",
  "projectId",
  "taskId",
  "turnId",
  "messageId",
  "toolUseId",
] as const;
const MAX_CONTEXT_STRING = 4000;

/** The slot context the backend sees: primitives only, strings bounded. */
export function wireContext(
  context: Record<string, unknown>,
): Record<string, string | number | boolean | null> {
  const out: Record<string, string | number | boolean | null> = {};
  for (const [key, value] of Object.entries(context)) {
    if (key === "renderDefault") continue;
    if (typeof value === "string") {
      out[key] =
        value.length > MAX_CONTEXT_STRING
          ? value.slice(0, MAX_CONTEXT_STRING)
          : value;
    } else if (
      typeof value === "number" ||
      typeof value === "boolean" ||
      value === null
    ) {
      out[key] = value;
    }
  }
  return out;
}

export function surfaceRequest(
  site: string,
  slotKey: string | undefined,
  context: Record<string, unknown>,
): UiRenderRequest {
  const wire = wireContext(context);
  const ids = INSTANCE_KEYS.map((key) => wire[key]).filter(
    (value): value is string | number =>
      value !== undefined && value !== null && value !== "",
  );
  if (slotKey) ids.push(slotKey);
  const sessionId = typeof wire.sessionId === "string" ? wire.sessionId : null;
  return {
    site,
    instance: ids.join(":") || "global",
    session_id: sessionId,
    context: wire,
  };
}
