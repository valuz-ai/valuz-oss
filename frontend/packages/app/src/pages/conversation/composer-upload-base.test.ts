import { describe, expect, it, vi } from "vitest";

import { resolveComposerUploadBase } from "./composer-upload-base";

const CLOUD = "https://cloud.example/agent";
const LOCAL = "http://127.0.0.1:8000";

/** Stands in for the edition's entity→backend registry. */
const registry = (owned: Record<string, string>) =>
  vi.fn((ref: { sessionId?: string; projectId?: string }, fallback: string) => {
    const id = ref.sessionId ?? ref.projectId ?? "";
    return owned[id] ?? fallback;
  });

describe("resolveComposerUploadBase", () => {
  it("sends a quick-chat upload to the picked service, not the default", () => {
    // The regression. ``chat-default`` is the 临时 sentinel bootstrap assigns
    // to every quick chat; no project row owns it, so the registry has no
    // opinion. Reading that as "no backend" sent the file to the module
    // default (local) while the turn was created on 云端服务, and the message
    // shipped without it.
    const base = resolveComposerUploadBase({
      selectedSessionId: null,
      selectedProjectId: "chat-default",
      execTargetBaseUrl: CLOUD,
      resolveBase: registry({}),
    });

    expect(base).toBe(CLOUD);
  });

  it("follows the picked service back to local", () => {
    // Same guarantee in the other direction — neither is special-cased.
    const base = resolveComposerUploadBase({
      selectedSessionId: null,
      selectedProjectId: "chat-default",
      execTargetBaseUrl: LOCAL,
      resolveBase: registry({}),
    });

    expect(base).toBe(LOCAL);
  });

  it("never asks the registry about the sentinel", () => {
    const resolveBase = registry({});

    resolveComposerUploadBase({
      selectedSessionId: null,
      selectedProjectId: "chat-default",
      execTargetBaseUrl: CLOUD,
      resolveBase,
    });

    expect(resolveBase).not.toHaveBeenCalled();
  });

  it("a draft in a real project follows the project", () => {
    const base = resolveComposerUploadBase({
      selectedSessionId: null,
      selectedProjectId: "proj-1",
      execTargetBaseUrl: LOCAL,
      resolveBase: registry({ "proj-1": CLOUD }),
    });

    expect(base).toBe(CLOUD);
  });

  it("an open conversation outranks the picker", () => {
    // The bar is locked once a session exists, so the pick is stale state and
    // the session's own backend is the only right answer.
    const base = resolveComposerUploadBase({
      selectedSessionId: "sess-1",
      selectedProjectId: "chat-default",
      execTargetBaseUrl: LOCAL,
      resolveBase: registry({ "sess-1": CLOUD }),
    });

    expect(base).toBe(CLOUD);
  });

  it("single-backend builds still get the module default", () => {
    // OSS registers no targets and no resolver — every answer is "no opinion",
    // which must stay ``undefined`` so the api module keeps its own base.
    const base = resolveComposerUploadBase({
      selectedSessionId: null,
      selectedProjectId: null,
      execTargetBaseUrl: undefined,
      resolveBase: registry({}),
    });

    expect(base).toBeUndefined();
  });
});
