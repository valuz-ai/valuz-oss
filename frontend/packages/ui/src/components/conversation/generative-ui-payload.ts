export type GenerativeUIProtocol = "openui-lang" | "a2ui-json";

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

export function parseGenerativeUIPayload(
  raw: string | GenerativeUIPayload | undefined | null,
): GenerativeUIPayload {
  if (raw && typeof raw === "object") return raw;

  const body = extractContentText(raw);
  const envelope = parseProtocolEnvelope(body);
  if (envelope) return envelope;

  return {
    protocol: looksLikeA2UI(body) ? "a2ui-json" : "openui-lang",
    body,
  };
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
    return payload.messages.map((message) => JSON.stringify(message)).join("\n");
  }
  if (payload.content !== undefined) return JSON.stringify(payload.content);
  return "";
}

function normalizeProtocol(value: unknown): GenerativeUIProtocol | null {
  if (typeof value !== "string") return null;
  const normalized = value.toLowerCase();
  if (normalized.includes("a2ui")) return "a2ui-json";
  if (normalized.includes("openui")) return "openui-lang";
  return null;
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
    value.version === "v0.9" &&
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
