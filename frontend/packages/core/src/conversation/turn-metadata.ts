import type { ConversationTurnMetadata } from "@valuz/shared";

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

/** Both history flat payloads and live metadata objects carry the same host fact. */
export function parseTurnMetadata(
  raw: unknown,
): ConversationTurnMetadata | undefined {
  let value: unknown = raw;
  if (typeof raw === "string") {
    try {
      value = JSON.parse(raw);
    } catch {
      return undefined;
    }
  }
  const input = record(record(value)?.background_input);
  if (
    !input ||
    input.source !== "background" ||
    typeof input.input_id !== "string" ||
    !input.input_id ||
    input.input_id.length > 36
  )
    return undefined;
  const presentation = record(input.presentation);
  if (input.presentation != null && !presentation) return undefined;
  return {
    background_input: {
      input_id: input.input_id,
      source: "background",
      ...(presentation ? { presentation: { ...presentation } } : {}),
    },
  };
}
