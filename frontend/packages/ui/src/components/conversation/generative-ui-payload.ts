/**
 * A2UI v0.9.1 is the only generative-UI protocol. The envelope keeps an
 * explicit protocol so unknown payloads fail closed instead of being guessed.
 */
export type GenerativeUIProtocol = "a2ui-json";

export interface GenerativeUIPayload {
  protocol: GenerativeUIProtocol;
  body: string;
}

/**
 * Extract the raw text payload from a kernel tool-output string.
 *
 * MCP tool results surface on the frontend wrapped in a JSON content-block
 * envelope because the host toolkit MCP server returns ``TextContent`` and the
 * kernel JSON-stringifies the content blocks at the SSE boundary. Some
 * runtimes also emit a Python-repr variant. The renderers need the inner text,
 * not the envelope, so unwrap both; fall through to the raw string when
 * there's none.
 */
export function extractContentText(raw: string | undefined | null): string {
  const s = (raw ?? "").trim();
  if (!s) return "";

  try {
    const parsed: unknown = JSON.parse(s);
    const text = readTextBlocks(parsed);
    if (text !== null) return text;
  } catch {
    /* not JSON - try repr / fall through */
  }

  const repr = matchReprText(s);
  if (repr !== null) return repr;

  return s;
}

/** Receipt appended by the backend's UI-artifact sink (see the
 *  ``[[ui-artifact-receipt]]`` trailer in ``modules/genui/tools.py``). */
export interface GeneratedUiArtifactReceipt {
  artifact_id: string;
  revision_id: string;
  revision: number;
  host_type: string | null;
  host_id: string | null;
  slot: string;
  expected_revision_id: string | null;
  /** When the generation happened, in epoch milliseconds (the clock the
   *  binding's updated_at uses). Receipts persisted before 2026-08 carry
   *  epoch SECONDS — consumers must normalize before comparing against a
   *  millisecond timestamp — and older receipts omit the field entirely. */
  created_at?: number;
}

const RECEIPT_RE =
  /\n?\[\[ui-artifact-receipt\]\](\{[\s\S]*?\})\[\[\/ui-artifact-receipt\]\]\s*$/;

/**
 * Split a raw generate_ui tool output into the renderable body and the
 * (optional) artifact receipt trailer. The trailer rides in the persisted
 * tool result so the adopt affordance survives history replay; renderers
 * must never see it.
 */
export function extractUiArtifactReceipt(raw: string | undefined | null): {
  receipt: GeneratedUiArtifactReceipt | null;
  body: string;
} {
  const text = extractContentText(raw);
  const match = text.match(RECEIPT_RE);
  if (!match || match.index === undefined) return { receipt: null, body: text };
  let receipt: GeneratedUiArtifactReceipt | null = null;
  try {
    const parsed: unknown = JSON.parse(match[1]);
    if (
      isRecord(parsed) &&
      typeof parsed.artifact_id === "string" &&
      typeof parsed.revision_id === "string" &&
      typeof parsed.revision === "number"
    ) {
      receipt = parsed as unknown as GeneratedUiArtifactReceipt;
    }
  } catch {
    /* malformed trailer — treat as absent, keep it stripped from the body */
  }
  return { receipt, body: text.slice(0, match.index).trimEnd() };
}

/**
 * The A2UI payload inside a tool result, or null when there is not one.
 *
 * Null rather than a best-effort body: anything that is not an A2UI stream has
 * no renderer to go to, and passing it on would put raw source text on screen
 * where a rendered UI belongs.
 */
export function parseGenerativeUIPayload(
  raw: string | GenerativeUIPayload | undefined | null,
): GenerativeUIPayload | null {
  if (raw && typeof raw === "object") return raw;

  // Defensive strip: callers normally pass the pre-stripped body, but the
  // fullscreen/export paths may hand the raw tool output straight through.
  const body = extractUiArtifactReceipt(raw).body;
  const envelope = parseProtocolEnvelope(body);
  if (envelope) return envelope;

  return looksLikeA2UI(body) ? { protocol: "a2ui-json", body } : null;
}

function parseProtocolEnvelope(body: string): GenerativeUIPayload | null {
  const parsed = safeJsonParse(body);
  if (Array.isArray(parsed) && parsed.every(isA2UIMessage)) {
    return {
      protocol: "a2ui-json",
      body: parsed.map((message) => JSON.stringify(message)).join("\n"),
    };
  }
  if (!isRecord(parsed)) return null;

  if (isA2UIMessage(parsed)) {
    return { protocol: "a2ui-json", body: JSON.stringify(parsed) };
  }

  const protocol =
    normalizeProtocol(parsed.protocol) ?? normalizeProtocol(parsed.mime);
  if (!protocol) return null;

  return {
    protocol,
    body: readPayloadBody(parsed),
  };
}

function readPayloadBody(payload: Record<string, unknown>): string {
  if (typeof payload.content === "string") return payload.content;
  if (typeof payload.body === "string") return payload.body;
  if (Array.isArray(payload.messages)) {
    return payload.messages
      .map((message) => JSON.stringify(message))
      .join("\n");
  }
  if (payload.content !== undefined) return JSON.stringify(payload.content);
  return "";
}

function normalizeProtocol(value: unknown): GenerativeUIProtocol | null {
  if (typeof value !== "string") return null;
  return value.toLowerCase().includes("a2ui") ? "a2ui-json" : null;
}

function looksLikeA2UI(body: string): boolean {
  return parseA2UIMessages(body).some(isA2UIMessage);
}

function parseA2UIMessages(body: string): Record<string, unknown>[] {
  const trimmed = body.trim();
  if (!trimmed) return [];

  const parsed = safeJsonParse(trimmed);
  if (Array.isArray(parsed)) return parsed.filter(isRecord);
  if (isRecord(parsed) && isA2UIMessage(parsed)) return [parsed];

  return trimmed
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("{") && line.endsWith("}"))
    .map((line) => safeJsonParse(line))
    .filter((message): message is Record<string, unknown> => isRecord(message));
}

function isA2UIMessage(value: unknown): boolean {
  return (
    isRecord(value) &&
    value.version === "v0.9.1" &&
    [
      "createSurface",
      "updateComponents",
      "updateDataModel",
      "deleteSurface",
    ].some((key) => key in value)
  );
}

function readTextBlocks(parsed: unknown): string | null {
  const entries = Array.isArray(parsed) ? parsed : [parsed];
  const texts: string[] = [];
  for (const e of entries) {
    if (
      e &&
      typeof e === "object" &&
      typeof (e as Record<string, unknown>).text === "string"
    ) {
      texts.push((e as Record<string, string>).text);
    }
  }
  if (texts.length) return texts.join("");
  if (typeof parsed === "string") return parsed;
  return null;
}

function matchReprText(s: string): string | null {
  const m = s.match(/'text'\s*:\s*'((?:[^'\\]|\\.)*)'/);
  if (!m || !m[1]) return null;
  return m[1]
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "\t")
    .replace(/\\'/g, "'")
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, "\\");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function safeJsonParse(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}
