import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  memoryApi,
  setMemoryApiBase,
  type MemoryRecordAdd,
} from "./memory-api";
import { setApiBaseResolver } from "./base-resolver";

const receipt = {
  status: "applied",
  revision: 2,
  affected_ids: [],
  affected_project_ids: [],
  replayed: false,
  context_notice: "retained context",
};
const respond = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

beforeEach(() => {
  setMemoryApiBase("http://default.test");
  setApiBaseResolver(() => "http://project.test");
});
afterEach(() => {
  vi.restoreAllMocks();
  setApiBaseResolver(null);
});

describe("memory records transport", () => {
  it("reads settings without requesting any legacy content", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      respond({
        enabled: true,
        auto_extract: false,
        custom_instructions: "",
      }),
    );
    await memoryApi.getSettings({ baseUrl: "http://authority.test" });
    expect(String(fetch.mock.calls[0][0])).toBe(
      "http://authority.test/v1/memory/settings",
    );
  });

  it("routes a paginated project snapshot to the explicit authority before project/default routing", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      respond({
        revision: 2,
        records: [],
        total: 40,
        offset: 20,
        limit: 20,
        context_notice: "",
      }),
    );
    await memoryApi.listRecords(
      { project_id: "project/a", target: "project", offset: 20, limit: 20 },
      { baseUrl: "http://authority.test" },
    );
    expect(String(fetch.mock.calls[0][0])).toBe(
      "http://authority.test/v1/memory/records?project_id=project%2Fa&target=project&offset=20&limit=20",
    );
    await memoryApi.listRecords({ project_id: "project/a" });
    expect(String(fetch.mock.calls[1][0])).toMatch(/^http:\/\/project.test\//);
  });

  it("sends only permitted manual-write fields and preserves the caller's operation token", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => respond(receipt));
    const payload = {
      operation_id: "retry-operation",
      authority_id: "authority-old",
      authority_epoch: 3,
      base_revision: 1,
      target: "user",
      content: "verified preference",
      owner_user_id: "foreign",
      source_refs: [{ kind: "manual", source_id: "forged" }],
      namespace: "foreign",
    } as MemoryRecordAdd;
    await memoryApi.addRecord(payload, { baseUrl: "http://authority.test" });
    await memoryApi.addRecord(payload, { baseUrl: "http://authority.test" });
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({
      operation_id: "retry-operation",
      authority_id: "authority-old",
      authority_epoch: 3,
      base_revision: 1,
      target: "user",
      content: "verified preference",
    });
    expect(fetch.mock.calls[0][1]?.body).toBe(fetch.mock.calls[1][1]?.body);
  });

  it("uses stable record IDs and distinct source forgetting without dropping DELETE replay tokens", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => respond(receipt));
    await memoryApi.correctRecord("record/a", {
      operation_id: "correction",
      base_revision: 2,
      content: "new",
    });
    await memoryApi.forgetRecord(
      "record/a",
      {
        operation_id: "forget",
        base_revision: 2,
        target: "project",
        project_id: "p",
      },
      { baseUrl: "http://authority.test" },
    );
    await memoryApi.forgetSource(
      {
        operation_id: "source",
        base_revision: 2,
        kind: "session",
        source_id: "s",
      },
      { baseUrl: "http://authority.test" },
    );
    expect(String(fetch.mock.calls[0][0])).toBe(
      "http://default.test/v1/memory/records/record%2Fa",
    );
    expect(fetch.mock.calls[1][1]?.method).toBe("DELETE");
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({
      operation_id: "forget",
      base_revision: 2,
      target: "project",
      project_id: "p",
    });
    expect(String(fetch.mock.calls[2][0])).toBe(
      "http://authority.test/v1/memory/sources/forget",
    );
  });

  it.each([409, 503])(
    "retains HTTP %s as a failure instead of returning an empty snapshot",
    async (status) => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        respond({ detail: "unavailable or conflict" }, status),
      );
      await expect(memoryApi.listRecords()).rejects.toMatchObject({ status });
    },
  );
});
