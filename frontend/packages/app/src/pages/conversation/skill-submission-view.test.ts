import { describe, expect, it } from "vitest";

import { skillSubmissionView } from "./skill-submission-view";
import type { OperationView } from "@valuz/core";

function operation(patch: Partial<OperationView>): OperationView {
  return {
    id: "op-1",
    project_id: null,
    operation_type: "skill.submit",
    operation_version: 1,
    actor_kind: "agent",
    actor_id: "session-1",
    origin_session_id: "session-1",
    origin_tool_call_id: null,
    origin_playbook_run_id: null,
    origin_automation_run_id: null,
    target_refs: [{ type: "skill", slug: "demo" }],
    input_payload: {},
    preview: {
      kind: "skill",
      slug: "demo",
      summary: "does things",
      change_kind: "create",
      conflict_kind: "none",
      next_version: 1,
      staging_path: "/w/.skill-staging/demo",
      files: [{ path: "SKILL.md", type: "file", size: 120 }],
    },
    expected_revisions: {},
    risk_level: "material",
    confirmation_policy: "confirm",
    state: "awaiting_confirmation",
    proposal_hash: "h",
    canonical_result_refs: [],
    result_payload: {},
    error_code: null,
    error_message: null,
    expires_at: null,
    superseded_by_id: null,
    latest_decision: null,
    created_at: 1,
    updated_at: 1,
    ...patch,
  };
}

describe("skillSubmissionView", () => {
  it("renders a pending proposal from the record, not from a staging scan", () => {
    const view = skillSubmissionView(operation({}));

    expect(view.state).toBe("pending");
    expect(view.slug).toBe("demo");
    expect(view.summary).toBe("does things");
    expect(view.nextVersion).toBe(1);
    expect(view.conflictKind).toBe("none");
    expect(view.stagedFiles).toEqual([
      { path: "SKILL.md", type: "file", size: 120 },
    ]);
    expect(view.stagingPath).toBe("/w/.skill-staging/demo");
  });

  it("reports a saved skill with the version it produced", () => {
    const view = skillSubmissionView(
      operation({
        state: "succeeded",
        result_payload: { slug: "demo", version_no: 3 },
      }),
    );

    expect(view.state).toBe("confirmed");
    expect(view.savedVersion).toBe(3);
  });

  it("keeps a dismissed submission distinguishable after a reload", () => {
    // The whole point of the record: staging is gone in both cases, so a
    // scan cannot tell these apart.
    expect(skillSubmissionView(operation({ state: "cancelled" })).state).toBe(
      "dismissed",
    );
    expect(
      skillSubmissionView(
        operation({ state: "succeeded", result_payload: { version_no: 1 } }),
      ).state,
    ).toBe("confirmed");
  });

  it("surfaces a stale submission as an error the user can act on", () => {
    const view = skillSubmissionView(
      operation({
        state: "stale",
        error_code: "OPERATION_STALE",
        error_message: "the staged files changed after they were submitted",
      }),
    );

    expect(view.state).toBe("error");
    expect(view.errorMessage).toContain("changed after");
  });

  it("passes the collision through so the card can ask", () => {
    const view = skillSubmissionView(
      operation({
        preview: {
          slug: "demo",
          conflict_kind: "unprepared_collision",
          next_version: 2,
        },
      }),
    );

    expect(view.conflictKind).toBe("unprepared_collision");
    expect(view.nextVersion).toBe(2);
  });

  it("falls back to a known conflict kind for an unexpected value", () => {
    const view = skillSubmissionView(
      operation({ preview: { slug: "demo", conflict_kind: "whatever" } }),
    );

    expect(view.conflictKind).toBe("none");
  });

  it("shows an in-flight decision as busy", () => {
    expect(skillSubmissionView(operation({}), "confirm").state).toBe(
      "confirming",
    );
    expect(skillSubmissionView(operation({}), "cancel").state).toBe(
      "dismissing",
    );
    // executing on the server reads the same way after a refresh
    expect(skillSubmissionView(operation({ state: "executing" })).state).toBe(
      "confirming",
    );
  });

  it("tolerates a record with no preview at all", () => {
    const view = skillSubmissionView(
      operation({ preview: {}, result_payload: {} }),
    );

    expect(view.slug).toBe("");
    expect(view.stagedFiles).toBeUndefined();
    expect(view.nextVersion).toBeNull();
    expect(view.conflictKind).toBe("none");
  });
});
