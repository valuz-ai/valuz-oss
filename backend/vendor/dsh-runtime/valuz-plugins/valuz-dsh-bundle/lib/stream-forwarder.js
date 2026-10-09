/**
 * valuz-stream-forwarder — forwards live model stream frames to the kernel.
 *
 * Since session-log format v4 the token stream is no longer a session event:
 * the agent loop emits transient `agent/assistant-stream` frames and records
 * only the settled `assistant/message`. The dsh SDK server forwards session
 * events but not these frames, so the kernel would lose token streaming. This
 * plugin writes each frame as one extra JSON-RPC notification line
 * (`valuz.assistant-stream`) on the SDK transport's stdout. Each write is one
 * complete line, so it cannot interleave inside another message.
 */
export const name = "valuz-stream-forwarder";

export function apply(ctx) {
  ctx.on("agent/assistant-stream", ({ agent, frame }) => {
    const sessionId = String(agent?.session?.id ?? "");
    if (sessionId === "") return;
    const line = JSON.stringify({
      jsonrpc: "2.0",
      method: "valuz.assistant-stream",
      params: { sessionId, frame },
    });
    process.stdout.write(line + "\n");
  });
}
