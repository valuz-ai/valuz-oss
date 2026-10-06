import { describe, expect, it, vi } from "vitest";

import type { HostRequest, HostServices } from "./services";
import { createValuzClient, toValuzApiError, ValuzApiError } from "./valuz-client";

function setup(respond: (request: HostRequest) => unknown = () => ({})) {
  const requests: HostRequest[] = [];
  const confirm = vi.fn(async () => true);
  const draftConversation = vi.fn();
  const services = {
    request: vi.fn(async (request: HostRequest) => {
      requests.push(request);
      const value = respond(request);
      if (value instanceof Error) throw value;
      return value;
    }),
    confirm,
    draftConversation,
  } as unknown as Pick<HostServices, "request" | "confirm" | "draftConversation">;
  const valuz = createValuzClient({ pluginId: "acme.dashboard", services });
  return { valuz, requests, confirm, draftConversation };
}

const HEADER = { "X-Valuz-Plugin-Id": "acme.dashboard" };

describe("ctx.valuz request shapes", () => {
  it("projects", async () => {
    const { valuz, requests } = setup((r) =>
      r.path === "/v1/projects"
        ? { projects: [{ id: "p1", name: "One", kind: "project", root_path: "/a", icon: null }] }
        : r.path.endsWith("/files")
          ? { files: [{ name: "a.md", path: "a.md", type: "file" }] }
          : { id: "p1", name: "One", instructions_md: "be nice" },
    );

    expect(await valuz.projects.list()).toEqual([
      { id: "p1", name: "One", kind: "project", rootPath: "/a", icon: null },
    ]);
    expect(await valuz.projects.get("p1")).toMatchObject({ id: "p1", instructions: "be nice" });
    expect(await valuz.projects.files("p1", { path: "src", depth: 1 })).toHaveLength(1);

    expect(requests).toEqual([
      { method: "GET", path: "/v1/projects", headers: HEADER },
      { method: "GET", path: "/v1/projects/p1", headers: HEADER },
      {
        method: "GET",
        path: "/v1/projects/p1/files",
        query: { path: "src", depth: 1 },
        headers: HEADER,
      },
    ]);
  });

  it("artifacts, by project and by session", async () => {
    const { valuz, requests } = setup((r) =>
      r.path.includes("/revisions/")
        ? { revision_id: "r1", version_no: 2, content: "# hi" }
        : r.path === "/v1/artifacts"
          ? {
              items: [
                {
                  id: "a1",
                  display_name: "Report",
                  kind: "file",
                  version_no: 3,
                  updated_at: 9,
                  current: { id: "r9", file_name: "report.md", mime_type: "text/markdown" },
                },
              ],
            }
          : {
              items: [
                { id: "r5", artifact_id: "a5", file_name: "x.csv", version_no: 1, created_at: 4, session_id: "s1" },
              ],
            },
    );

    expect(await valuz.artifacts.list({ projectId: "p1" })).toEqual([
      {
        id: "a1",
        revisionId: "r9",
        name: "Report",
        kind: "file",
        versionNo: 3,
        fileName: "report.md",
        mimeType: "text/markdown",
        updatedAt: 9,
        sessionId: null,
      },
    ]);
    expect((await valuz.artifacts.list({ sessionId: "s1" }))[0]).toMatchObject({
      id: "a5",
      revisionId: "r5",
      sessionId: "s1",
    });
    expect(await valuz.artifacts.content("r1")).toEqual({
      revisionId: "r1",
      versionNo: 2,
      content: "# hi",
    });
    await expect(valuz.artifacts.list({})).rejects.toMatchObject({
      status: 400,
      code: "invalid_argument",
    });

    expect(requests.map((r) => [r.path, r.query])).toEqual([
      ["/v1/artifacts", { project_id: "p1" }],
      ["/v1/sessions/s1/artifacts", undefined],
      ["/v1/artifacts/revisions/r1/content", undefined],
    ]);
  });

  it("knowledge", async () => {
    const { valuz, requests } = setup((r) =>
      r.path === "/v1/docs/search"
        ? { hits: [{ document_id: "d1", filename: "a.pdf", score: 0.5, snippet: "x", page_ref: "p3" }] }
        : { id: "d1", filename: "a.pdf" },
    );
    expect(
      await valuz.knowledge.search("risk", { projectId: "p1", kbIds: ["k1"], topK: 3 }),
    ).toEqual([{ documentId: "d1", filename: "a.pdf", score: 0.5, snippet: "x", pageRef: "p3" }]);
    expect(await valuz.knowledge.get("d1")).toMatchObject({ id: "d1" });

    expect(requests[0]).toEqual({
      method: "POST",
      path: "/v1/docs/search",
      json: { query: "risk", project_id: "p1", top_k: 3, folder_ids: ["k1"] },
      headers: HEADER,
    });
    expect(requests[1]).toMatchObject({ method: "GET", path: "/v1/docs/d1" });
  });

  it("conversations: draft goes to the host, send creates then messages, events lists", async () => {
    const { valuz, requests, draftConversation } = setup((r) =>
      r.method === "POST" && r.path === "/v1/sessions"
        ? { id: "s9" }
        : r.path.endsWith("/events")
          ? { items: [{ seq: 1, timestamp: 5, event: { type: "x" } }] }
          : {},
    );

    await valuz.conversations.draft({ projectId: "p1", agent: "lead", text: "hello" });
    expect(draftConversation).toHaveBeenCalledWith({ projectId: "p1", agent: "lead", text: "hello" });

    expect(await valuz.conversations.send({ projectId: "p1", agent: "lead", text: "go" })).toEqual({
      sessionId: "s9",
    });
    expect(await valuz.conversations.send({ sessionId: "s1", text: "again" })).toEqual({
      sessionId: "s1",
    });
    await expect(valuz.conversations.send({ text: "x" })).rejects.toMatchObject({
      code: "invalid_argument",
    });
    expect(await valuz.conversations.events("s1", { afterSeq: 4 })).toEqual([
      { seq: 1, timestamp: 5, event: { type: "x" } },
    ]);

    expect(requests.map((r) => [r.method, r.path, r.json ?? r.query])).toEqual([
      ["POST", "/v1/sessions", { project_id: "p1", agent_slug: "lead" }],
      ["POST", "/v1/sessions/s9/messages", { prompt: "go" }],
      ["POST", "/v1/sessions/s1/messages", { prompt: "again" }],
      ["GET", "/v1/sessions/s1/events", { after_seq: 4 }],
    ]);
  });

  it("connectors: list, listTools, read-only and confirmed write calls", async () => {
    const { valuz, requests, confirm } = setup((r) => {
      if (r.path === "/v1/connectors") {
        return {
          connectors: [
            { id: "c1", slug: "acme-data", display_name: "Acme", description: null, enabled: true, status: "connected", tool_count: 2 },
          ],
        };
      }
      if (r.path.endsWith("/tools")) {
        return {
          connector_id: "c1",
          tools: [{ name: "get_positions", description: "d", input_schema: { type: "object" }, read_only: true }],
        };
      }
      return { content: [{ type: "text", text: "ok" }], structured_content: { positions: [] }, is_error: false };
    });

    expect((await valuz.connectors.list())[0]).toMatchObject({ slug: "acme-data", name: "Acme", toolCount: 2 });
    expect(await valuz.connectors.listTools("acme-data")).toEqual([
      { name: "get_positions", description: "d", inputSchema: { type: "object" }, readOnly: true },
    ]);

    const read = await valuz.connectors.callTool("acme-data", "get_positions", { projectId: "p1" });
    expect(read).toEqual({
      content: [{ type: "text", text: "ok" }],
      structuredContent: { positions: [] },
      isError: false,
    });
    expect(confirm).not.toHaveBeenCalled();

    await valuz.connectors.callTool("acme-data", "set_limit", { n: 1 }, { write: true });
    expect(confirm).toHaveBeenCalledTimes(1);

    expect(requests.slice(2).map((r) => [r.path, r.json])).toEqual([
      ["/v1/connectors/acme-data/tools/get_positions/call", { arguments: { projectId: "p1" }, allow_write: false }],
      ["/v1/connectors/acme-data/tools/set_limit/call", { arguments: { n: 1 }, allow_write: true }],
    ]);
  });

  it("does not call a write tool the user declined", async () => {
    const { valuz, requests, confirm } = setup();
    confirm.mockResolvedValueOnce(false);
    await expect(
      valuz.connectors.callTool("acme-data", "set_limit", {}, { write: true }),
    ).rejects.toMatchObject({ status: 403, code: "user_declined" });
    expect(requests).toEqual([]);
  });

  it("automations: run, getRun, waitRun, latestRun", async () => {
    let polls = 0;
    const { valuz, requests } = setup((r) => {
      if (r.method === "POST") return { automation_id: "a1", run_id: "run1", status: "queued" };
      if (r.path.endsWith("/runs/latest")) {
        return r.path.includes("none") ? Object.assign(new Error("nf"), { status: 404 }) : { run_id: "run7", status: "success" };
      }
      polls += 1;
      return { run_id: "run1", status: polls < 2 ? "running" : "success", artifact: { ok: true } };
    });

    expect(await valuz.automations.run("risk-summary", { portfolioId: "p" })).toEqual({
      runId: "run1",
      automationId: "a1",
    });
    expect((await valuz.automations.getRun("run1")).done).toBe(false);
    const done = await valuz.automations.waitRun("run1", { intervalMs: 1, timeoutMs: 1000 });
    expect(done).toMatchObject({ status: "success", done: true, output: { ok: true } });
    expect((await valuz.automations.latestRun("risk-summary"))?.runId).toBe("run7");
    expect(await valuz.automations.latestRun("none")).toBeNull();

    expect(requests[0]).toEqual({
      method: "POST",
      path: "/v1/extensions/third-party/acme.dashboard/automations/risk-summary/run",
      json: { input: { portfolioId: "p" } },
      headers: HEADER,
    });
    expect(requests[1]?.path).toBe("/v1/extensions/third-party/acme.dashboard/automation-runs/run1");
  });

  it("waitRun times out", async () => {
    const { valuz } = setup(() => ({ run_id: "r", status: "running" }));
    await expect(valuz.automations.waitRun("r", { timeoutMs: 5, intervalMs: 10 })).rejects.toMatchObject({
      code: "run_timeout",
    });
  });

  it("storage", async () => {
    const { valuz, requests } = setup((r) => {
      if (r.method === "GET" && r.path.endsWith("/storage")) return { items: [{ key: "k", size: 3, updated_at: 7 }] };
      if (r.method === "GET") return { key: "k", value: { a: 1 } };
      return {};
    });
    expect(await valuz.storage.get("k")).toEqual({ a: 1 });
    await valuz.storage.set("k", { a: 2 });
    await valuz.storage.delete("k");
    expect(await valuz.storage.list("pre")).toEqual([{ key: "k", size: 3, updatedAt: 7 }]);

    const base = "/v1/extensions/third-party/acme.dashboard/storage";
    expect(requests.map((r) => [r.method, r.path, r.json ?? r.query])).toEqual([
      ["GET", `${base}/k`, undefined],
      ["PUT", `${base}/k`, { value: { a: 2 } }],
      ["DELETE", `${base}/k`, undefined],
      ["GET", base, { prefix: "pre" }],
    ]);
  });

  it("storage.get answers null for a missing key", async () => {
    const { valuz } = setup(() => Object.assign(new Error("nf"), { status: 404 }));
    expect(await valuz.storage.get("nope")).toBeNull();
  });

  it("notifications", async () => {
    const { valuz, requests } = setup(() => ({ id: "n1" }));
    expect(await valuz.notifications.post({ title: "Done", body: "b", link: "/x/acme.dashboard" })).toEqual({ id: "n1" });
    expect(requests[0]).toEqual({
      method: "POST",
      path: "/v1/notifications",
      json: { title: "Done", body: "b", link: "/x/acme.dashboard" },
      headers: HEADER,
    });
  });

  it("service.fetch is not supported in plugin API 1.x", async () => {
    const { valuz } = setup();
    const { fetch: serviceFetch } = valuz.service;
    await expect(serviceFetch("/x")).rejects.toThrow(/not supported in plugin API 1\.x/);
  });
});

describe("error normalisation", () => {
  it("reads status, code and message from the host's ApiError body", () => {
    const error = Object.assign(new Error("API 403: ..."), {
      status: 403,
      body: JSON.stringify({ detail: { code: "plugin_permission_denied", message: "needs projects:read" } }),
    });
    const normalised = toValuzApiError(error);
    expect(normalised).toBeInstanceOf(ValuzApiError);
    expect(normalised).toMatchObject({
      status: 403,
      code: "plugin_permission_denied",
      message: "needs projects:read",
    });
  });

  it("falls back to http_<status> and keeps a string detail", () => {
    const error = Object.assign(new Error("x"), { status: 404, body: JSON.stringify({ detail: "Unknown project: p" }) });
    expect(toValuzApiError(error)).toMatchObject({ status: 404, code: "http_404", message: "Unknown project: p" });
  });

  it("treats a failure without status as a network error", () => {
    expect(toValuzApiError(new TypeError("Failed to fetch"))).toMatchObject({
      status: 0,
      code: "network_error",
    });
  });

  it("every method rejects with ValuzApiError", async () => {
    const { valuz } = setup(() => Object.assign(new Error("boom"), { status: 500 }));
    await expect(valuz.projects.list()).rejects.toBeInstanceOf(ValuzApiError);
  });
});
