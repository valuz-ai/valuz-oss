/**
 * valuz-tool-bridge — dsh plugin tools for every Valuz runtime.
 *
 * Runs in the resident manager host (VALUZ_DSH_ROLE=manager), where the
 * user's dsh plugins are loaded. It serves the tools registered on the
 * shared registry's global view — third-party plugin tools register there,
 * while dsh's own built-ins live in agent presets — over a loopback JSON API
 * the Valuz backend fronts as the always-on MCP server `valuz-dsh-plugins`.
 * Claude, Codex and DeepAgents sessions thereby call dsh plugin tools; dsh
 * sessions load the plugins natively and skip that server.
 *
 *   GET  /tools  → [{ name, description, inputSchema }]
 *   POST /call   { name, arguments } → { isError, text }
 *
 * Bearer-token authenticated; the endpoint + token are written to
 * `$DSH_HOME/valuz/tool-bridge.json` (0600) and removed on unload. Calls run
 * through `ctx.tools.execute`, so dsh's tool policy (pre-execute hooks,
 * approvals, timeouts) applies unchanged; an `ask` with no answerer fails
 * closed, as in dsh.
 */
import { randomBytes, randomUUID } from "node:crypto";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { homedir } from "node:os";
import { join } from "node:path";

export const name = "valuz-tool-bridge";
export const inject = ["tools"];

const CALL_TIMEOUT_MS = 10 * 60 * 1000;

/** Tools that already reach Valuz sessions by other routes, or need a dsh agent. */
function bridged(schema) {
  return !schema.name.startsWith("mcp__");
}

function contentText(content) {
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => (block?.type === "text" ? String(block.text ?? "") : JSON.stringify(block)))
    .filter((part) => part !== "")
    .join("\n");
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString("utf8");
  return raw === "" ? {} : JSON.parse(raw);
}

function reply(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

export function apply(ctx) {
  const token = randomBytes(24).toString("base64url");
  const home = process.env.DSH_HOME || join(homedir(), ".dsh");
  const stateDir = join(home, "valuz");
  const statePath = join(stateDir, "tool-bridge.json");

  const server = createServer(async (req, res) => {
    if (req.headers.authorization !== `Bearer ${token}`) return reply(res, 401, { error: "unauthorized" });
    try {
      if (req.method === "GET" && req.url === "/tools") {
        const tools = ctx.tools
          .schemas()
          .filter(bridged)
          .map((schema) => ({
            name: schema.name,
            description: schema.description,
            inputSchema: schema.parameters ?? { type: "object", properties: {} },
          }));
        return reply(res, 200, tools);
      }
      if (req.method === "POST" && req.url === "/call") {
        const { name: toolName, arguments: args } = await readJson(req);
        if (typeof toolName !== "string") return reply(res, 400, { error: "name is required" });
        const result = await ctx.tools.execute({
          callId: randomUUID(),
          name: toolName,
          arguments: args ?? {},
          signal: AbortSignal.timeout(CALL_TIMEOUT_MS),
        });
        return reply(res, 200, { isError: Boolean(result.isError), text: contentText(result.content) });
      }
      return reply(res, 404, { error: "not found" });
    } catch (error) {
      return reply(res, 500, { error: String(error?.message ?? error) });
    }
  });

  ctx.effect(() => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      mkdirSync(stateDir, { recursive: true });
      writeFileSync(
        statePath,
        JSON.stringify({ url: `http://127.0.0.1:${port}`, token, pid: process.pid }),
        { mode: 0o600 },
      );
      ctx.logger.info("valuz-tool-bridge serving dsh plugin tools on 127.0.0.1:%d", port);
    });
    return () => {
      rmSync(statePath, { force: true });
      server.close();
    };
  });
}
