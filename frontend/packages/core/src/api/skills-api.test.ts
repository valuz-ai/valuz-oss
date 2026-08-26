import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setApiBaseResolver } from "./base-resolver";
import { setSkillsApiBase, skillsApi } from "./skills-api";

const LOCAL = "http://local.test";
const CLOUD = "http://cloud.test";

function emptyCatalog(projectId: string): Response {
  return new Response(JSON.stringify({ project_id: projectId, skills: [] }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("skillsApi project routing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setSkillsApiBase(LOCAL);
    setApiBaseResolver((ref) =>
      ref.projectId === "cloud-project" ? CLOUD : undefined,
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setApiBaseResolver(null);
  });

  it("loads a cloud project's catalog from the cloud backend", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(emptyCatalog("cloud-project"));

    await skillsApi.projectCatalog("cloud-project");

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${CLOUD}/v1/projects/cloud-project/skills`,
    );
  });
});
