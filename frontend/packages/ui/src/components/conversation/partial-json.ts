/**
 * Make a half-written JSON fragment parseable.
 *
 * A streamed A2UI payload is cut at an arbitrary byte, so the tail is usually
 * an unfinished string, key, or number. Waiting for the closing brace means a
 * component appears only once it is fully written — text arrives in whole
 * paragraphs rather than as it is typed. Closing the fragment lets the part
 * that *has* arrived render.
 *
 * The rule throughout: never invent a value. An unfinished string value is
 * closed, because the characters received are real; an unfinished key, a
 * dangling colon, or a half-written number is dropped, because completing
 * those would put data on screen that the model never sent.
 */

type Container = "object" | "array";

/** What the parser expects next inside the innermost container. */
type Expect = "key" | "colon" | "value" | "comma";

interface Frame {
  container: Container;
  expect: Expect;
  /** Offset of the comma or `{`/`[` that opened the current entry. */
  entryStart: number;
  /** Offset of this container's own opening bracket. */
  openIndex: number;
}

/**
 * Returns a parseable equivalent of `fragment`, or null when nothing useful
 * survives. A complete fragment is returned unchanged.
 */
export function completeJsonFragment(fragment: string): string | null {
  const text = fragment.trimEnd();
  if (!text) return null;

  const stack: Frame[] = [];
  let inString = false;
  let escaped = false;
  let stringStart = -1;
  /** Offset just past the last complete value at the current depth. */
  let safeEnd = -1;

  const top = (): Frame | undefined => stack[stack.length - 1];

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i] as string;

    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') {
        inString = false;
        const frame = top();
        if (frame?.container === "object" && frame.expect === "key") {
          frame.expect = "colon";
        } else if (frame) {
          frame.expect = "comma";
          safeEnd = i + 1;
        } else {
          safeEnd = i + 1;
        }
      }
      continue;
    }

    switch (ch) {
      case '"':
        inString = true;
        stringStart = i;
        break;
      case "{":
      case "[": {
        stack.push({
          container: ch === "{" ? "object" : "array",
          expect: ch === "{" ? "key" : "value",
          entryStart: i + 1,
          openIndex: i,
        });
        break;
      }
      case "}":
      case "]": {
        stack.pop();
        const frame = top();
        if (frame) frame.expect = "comma";
        safeEnd = i + 1;
        break;
      }
      case ":": {
        const frame = top();
        if (frame) frame.expect = "value";
        break;
      }
      case ",": {
        const frame = top();
        if (frame) {
          frame.expect = frame.container === "object" ? "key" : "value";
          frame.entryStart = i + 1;
        }
        break;
      }
      default: {
        // Literal: number, true/false/null. Complete only once the next
        // delimiter arrives, so `safeEnd` deliberately does not move here.
        break;
      }
    }
  }

  // Rebuild a valid document from what was safely received.
  let body = text;

  if (inString) {
    const frame = top();
    const isKey = frame?.container === "object" && frame.expect === "key";
    if (isKey) {
      // A key with no value yet carries nothing worth showing.
      body = text.slice(0, frame?.entryStart ?? stringStart).replace(/,\s*$/, "");
    } else {
      // A value mid-flight: the characters so far are real, so keep them and
      // close the quote. This is what makes text grow as it streams.
      body = `${trimDanglingEscape(text)}"`;
    }
  } else {
    const frame = top();
    if (frame && (frame.expect === "colon" || frame.expect === "value")) {
      // `{"a":1,"b"` or `{"a":1,"b":` — the entry has no value; drop it.
      body = text.slice(0, frame.entryStart).replace(/,\s*$/, "");
    } else if (frame && frame.expect === "comma" && safeEnd >= 0 && safeEnd < text.length) {
      // A literal still being written: `{"a":1` may be `12` next.
      body = text.slice(0, safeEnd);
    }
  }

  body = body.replace(/[,\s]+$/, "");

  // Close what is still open, innermost first.
  for (let i = stack.length - 1; i >= 0; i -= 1) {
    const frame = stack[i] as Frame;
    // Close a container only if its opening bracket survived the trim. Keying
    // this off the *entry* offset instead drops the closer whenever the
    // discarded entry sat past the new end — which left `{"a":1` unclosed.
    if (frame.openIndex >= body.length) continue;
    body += frame.container === "object" ? "}" : "]";
  }

  return isParseable(body) ? body : rewind(text, safeEnd);
}

/** A trailing backslash would escape the quote we are about to append. */
function trimDanglingEscape(text: string): string {
  let backslashes = 0;
  for (let i = text.length - 1; i >= 0 && text[i] === "\\"; i -= 1) backslashes += 1;
  return backslashes % 2 === 1 ? text.slice(0, -1) : text;
}

/**
 * Fallback: cut back to the last position known to end a complete value and
 * close from there. Loses the in-flight entry, but never produces nonsense.
 */
function rewind(text: string, safeEnd: number): string | null {
  if (safeEnd <= 0) return null;
  let body = text.slice(0, safeEnd);
  const closers: string[] = [];
  let inString = false;
  let escaped = false;
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{") closers.push("}");
    else if (ch === "[") closers.push("]");
    else if (ch === "}" || ch === "]") closers.pop();
  }
  body = body.replace(/[,\s]+$/, "");
  while (closers.length) body += closers.pop();
  return isParseable(body) ? body : null;
}

function isParseable(text: string): boolean {
  if (!text.trim()) return false;
  try {
    JSON.parse(text);
    return true;
  } catch {
    return false;
  }
}
