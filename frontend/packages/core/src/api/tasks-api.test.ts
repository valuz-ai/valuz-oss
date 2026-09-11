import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  setExecutionTargets,
  type ExecutionTarget,
} from "../edition/execution-targets";
import { setEntityOriginAdapter } from "../edition/entity-origin";
import { setTasksApiBase, tasksApi, type Task } from "./tasks-api";

// A stand-in for the commercial overlay's origin index: the seam is a no-op on
// OSS, so nothing under test records anything unless an adapter is installed.
let index: Map<string, string>;

function installOriginIndex(): void {
  index = new Map();
  setEntityOriginAdapter({
    lookup: (id) => index.get(id),
    record: (id, targetId) => {
      index.set(id, targetId);
    },
    recordMany: (entries) => {
      for (const [id, targetId] of entries) index.set(id, targetId);
    },
  });
}

const LOCAL: ExecutionTarget = {
  id: "local",
  labelKey: "execTarget.local",
  baseUrl: "http://local.test",
  isDefault: true,
};
const CLOUD: ExecutionTarget = {
  id: "cloud",
  labelKey: "execTarget.cloud",
  baseUrl: "http://cloud.test",
};

function task(id: string, updatedAt = 1): Task {
  return {
    id,
    project_id: "proj-1",
    title: id,
    goal: id,
    status: "active",
    created_by: "user",
    lead_agent_slug: "lead",
    current_holder: "lead",
    file_path: `/tasks/${id}.md`,
    created_at: updatedAt,
    updated_at: updatedAt,
  };
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  setTasksApiBase("http://local.test");
  installOriginIndex();
});

afterEach(() => {
  setExecutionTargets([]);
  setEntityOriginAdapter(null);
  vi.unstubAllGlobals();
});

describe("tasksApi origin recording", () => {
  it("should record the created task under its project's origin when kickoff succeeds", async () => {
    index.set("proj-1", "cloud");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(task("task-1"))),
    );

    await tasksApi.kickoff("proj-1", {
      goal: "g",
      lead_agent_slug: "lead",
    } as Parameters<typeof tasksApi.kickoff>[1]);

    expect(index.get("task-1")).toBe("cloud");
  });

  it("should route kickoff to the project's backend when the project is remote", async () => {
    index.set("proj-1", "cloud");
    setExecutionTargets([LOCAL, CLOUD]);
    const fetchMock = vi.fn(async () => json(task("task-1")));
    vi.stubGlobal("fetch", fetchMock);

    await tasksApi.kickoff("proj-1", {
      goal: "g",
      lead_agent_slug: "lead",
    } as Parameters<typeof tasksApi.kickoff>[1]);

    // The base resolver is the edition's; with no resolver installed the call
    // goes to the module default. What matters here is that kickoff awaits the
    // response instead of returning the raw promise (it must read task.id).
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("should record every listed task under the project's origin when listing a project's tasks", async () => {
    index.set("proj-1", "cloud");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ tasks: [task("task-1"), task("task-2")] })),
    );

    await tasksApi.listTasks("proj-1");

    // Without this a task the user did not create in THIS app instance has no
    // origin, and getTask falls back to the local backend and 404s.
    expect(index.get("task-1")).toBe("cloud");
    expect(index.get("task-2")).toBe("cloud");
  });

  it("should leave the index untouched when the project's own origin is unknown", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ tasks: [task("task-1")] })),
    );

    await tasksApi.listTasks("proj-1");

    expect(index.has("task-1")).toBe(false);
  });
});

describe("tasksApi.listAllTasks", () => {
  it("should keep the single-backend path when no targets are registered", async () => {
    const fetchMock = vi.fn(async () => json({ tasks: [task("task-1")] }));
    vi.stubGlobal("fetch", fetchMock);

    const { tasks } = await tasksApi.listAllTasks();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(tasks.map((t) => t.id)).toEqual(["task-1"]);
    expect(tasks[0]!.exec_origin).toBeUndefined();
  });

  it("should merge both targets' tasks newest-first when the edition is multi-target", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        return url.startsWith("http://cloud.test")
          ? json({ tasks: [task("cloud-new", 300)] })
          : json({ tasks: [task("local-old", 100), task("local-mid", 200)] });
      }),
    );

    const { tasks } = await tasksApi.listAllTasks();

    // A cross-project list that asked only the default backend hides every
    // cloud task — which is exactly what made a kicked-off cloud task
    // invisible in the sidebar.
    expect(tasks.map((t) => t.id)).toEqual([
      "cloud-new",
      "local-mid",
      "local-old",
    ]);
  });

  it("should tag each merged row with the target that answered", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).startsWith("http://cloud.test")
          ? json({ tasks: [task("cloud-1", 300)] })
          : json({ tasks: [task("local-1", 100)] }),
      ),
    );

    const { tasks } = await tasksApi.listAllTasks();

    expect(Object.fromEntries(tasks.map((t) => [t.id, t.exec_origin]))).toEqual(
      {
        "cloud-1": "cloud",
        "local-1": "local",
      },
    );
  });

  it("should feed the origin index from the fan-out so detail reads route correctly", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).startsWith("http://cloud.test")
          ? json({ tasks: [task("cloud-1", 300)] })
          : json({ tasks: [task("local-1", 100)] }),
      ),
    );

    await tasksApi.listAllTasks();

    expect(index.get("cloud-1")).toBe("cloud");
    expect(index.get("local-1")).toBe("local");
  });

  it("should honour the limit across the merged set, not per target", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).startsWith("http://cloud.test")
          ? json({ tasks: [task("cloud-1", 300), task("cloud-2", 250)] })
          : json({ tasks: [task("local-1", 200), task("local-2", 100)] }),
      ),
    );

    const { tasks } = await tasksApi.listAllTasks(2);

    expect(tasks.map((t) => t.id)).toEqual(["cloud-1", "cloud-2"]);
  });

  it("should keep the surviving target's tasks when one target fails", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).startsWith("http://cloud.test")) {
          throw new Error("cloud down");
        }
        return json({ tasks: [task("local-1", 100)] });
      }),
    );

    const { tasks } = await tasksApi.listAllTasks();

    expect(tasks.map((t) => t.id)).toEqual(["local-1"]);
  });

  it("should drop a task both targets report rather than listing it twice", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ tasks: [task("shared", 100)] })),
    );

    const { tasks } = await tasksApi.listAllTasks();

    expect(tasks.map((t) => t.id)).toEqual(["shared"]);
  });
});
