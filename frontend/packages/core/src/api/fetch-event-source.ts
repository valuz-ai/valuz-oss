/**
 * Minimal SSE-over-``fetch`` reader (replaces ``EventSource``).
 *
 * ``EventSource`` can't send request headers, so it can't carry an
 * ``Authorization: Bearer`` token. This reads the ``text/event-stream`` over
 * ``fetch`` instead — the same transport the rest of the app uses — so any
 * auth the host has wired onto ``fetch`` (e.g. the commercial overlay's bearer
 * token) rides along automatically. In OSS-only mode it works headerless.
 *
 * Behaviour kept on par with ``EventSource``: parses named events
 * (``event:`` / ``data:`` / ``id:`` frames), ignores ``:`` comment pings, and
 * auto-reconnects on drop. ``getUrl`` is re-read on every (re)connect, so a
 * caller can thread a cursor (e.g. ``?after_seq=N``) to resume without loss.
 */

export interface SSEFrame {
  /** Event name (``event:`` field). Defaults to ``"message"``. */
  event: string;
  /** Joined ``data:`` lines. */
  data: string;
  /** ``id:`` field, when present. */
  id: string | null;
}

function parseFrame(raw: string): SSEFrame | null {
  let event = "message";
  let id: string | null = null;
  const data: string[] = [];
  let hasField = false;
  for (const line of raw.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // blank / comment ping
    const i = line.indexOf(":");
    const field = i === -1 ? line : line.slice(0, i);
    let value = i === -1 ? "" : line.slice(i + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") {
      event = value;
      hasField = true;
    } else if (field === "data") {
      data.push(value);
      hasField = true;
    } else if (field === "id") {
      id = value;
      hasField = true;
    }
  }
  return hasField ? { event, data: data.join("\n"), id } : null;
}

/**
 * Open an SSE stream and dispatch each frame to ``onFrame``. Reconnects
 * automatically until the returned ``close`` function is called.
 *
 * @param getUrl   called on every (re)connect — return the (possibly
 *                 cursor-bearing) stream URL.
 * @param onFrame  invoked once per non-comment SSE frame.
 * @returns a ``close()`` that aborts the stream and stops reconnecting.
 */
export function fetchEventSource(
  getUrl: () => string,
  onFrame: (frame: SSEFrame) => void,
  opts: { reconnectDelayMs?: number } = {},
): () => void {
  const reconnectDelayMs = opts.reconnectDelayMs ?? 1000;
  let closed = false;
  let controller: AbortController | null = null;

  const connect = async (): Promise<void> => {
    if (closed) return;
    controller = new AbortController();
    try {
      const res = await fetch(getUrl(), {
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        let sep: number;
        while ((sep = buf.indexOf("\n\n")) !== -1) {
          const frame = parseFrame(buf.slice(0, sep));
          buf = buf.slice(sep + 2);
          if (frame) onFrame(frame);
        }
      }
    } catch {
      // Drop / abort / non-200 — fall through to reconnect (silent, like
      // EventSource). ``close()`` sets ``closed`` so abort doesn't loop.
    }
    if (!closed) setTimeout(connect, reconnectDelayMs);
  };

  void connect();

  return () => {
    closed = true;
    controller?.abort();
  };
}
