import { describe, expect, it } from "vitest";
import { canSendProjectHandoff } from "./conversation-project-handoff";

const READY = {
  projectParam: "A",
  selectedProjectId: "A",
  draftBootstrapSettled: true,
};

describe("canSendProjectHandoff", () => {
  it("sends once the project is bound and bootstrap has settled", () => {
    expect(canSendProjectHandoff(READY)).toBe(true);
  });

  it("holds while bootstrap is still running, even with the project bound", () => {
    // The regression this exists for: bootstrap binds the project several
    // statements BEFORE it clears per-session state, so a send fired on the
    // binding alone had its optimistic turn wiped moments later — message
    // sent, but no bubble and no runtime-startup header.
    expect(
      canSendProjectHandoff({ ...READY, draftBootstrapSettled: false }),
    ).toBe(false);
  });

  it("holds until bootstrap has bound the project", () => {
    expect(
      canSendProjectHandoff({ ...READY, selectedProjectId: null }),
    ).toBe(false);
  });

  it("keeps holding while a different project is still bound", () => {
    expect(canSendProjectHandoff({ ...READY, selectedProjectId: "B" })).toBe(
      false,
    );
  });

  it("does not wait on a binding when the entry carries no project", () => {
    expect(
      canSendProjectHandoff({
        projectParam: null,
        selectedProjectId: null,
        draftBootstrapSettled: true,
      }),
    ).toBe(true);
  });

  it("still waits for bootstrap when there is no project", () => {
    expect(
      canSendProjectHandoff({
        projectParam: null,
        selectedProjectId: null,
        draftBootstrapSettled: false,
      }),
    ).toBe(false);
  });
});
