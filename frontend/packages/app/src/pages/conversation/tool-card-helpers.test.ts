import { describe, expect, it } from "vitest";

import {
  automationProposalGate,
  hostDocumentFileName,
  normalizeAutomationTrigger,
  parseAutomationCreateInput,
  resolveGenUiHost,
} from "./tool-card-helpers";

const PANEL = { host_type: "finance.research-desk", host_id: "desk" };

describe("automation trigger compatibility", () => {
  it("normalizes the legacy cron proposal before confirmation", () => {
    expect(normalizeAutomationTrigger({ cron: "0 9 * * *" })).toEqual({
      kind: "cron",
      cron_expr: "0 9 * * *",
      timezone: null,
    });
  });

  it("normalizes a legacy trigger read from automation tool input", () => {
    expect(
      parseAutomationCreateInput(
        JSON.stringify({
          action: "create",
          name: "每日简报",
          trigger: { cron: "0 9 * * *" },
        }),
      )?.trigger,
    ).toEqual({
      kind: "cron",
      cron_expr: "0 9 * * *",
      timezone: null,
    });
  });

  it("keeps the current discriminated trigger contract", () => {
    expect(
      normalizeAutomationTrigger({
        kind: "cron",
        cron_expr: "30 8 * * 1",
        timezone: "Asia/Shanghai",
      }),
    ).toEqual({
      kind: "cron",
      cron_expr: "30 8 * * 1",
      timezone: "Asia/Shanghai",
    });
  });
});

describe("resolveGenUiHost", () => {
  it("should return null when neither the tool nor the panel names a host", () => {
    // Plain in-conversation visual: the inline card is the whole UX.
    expect(resolveGenUiHost(JSON.stringify({ request: "a chart" }), null)).toBe(
      null,
    );
  });

  it("should use the panel host when the model omitted target_host", () => {
    // The regression this function exists for. The model omits the argument
    // far more often than not; the server binds to the turn's host anyway, so
    // the card must agree or the generation paints in the wrong place.
    expect(
      resolveGenUiHost(JSON.stringify({ request: "the desk" }), PANEL),
    ).toEqual({
      host_type: "finance.research-desk",
      host_id: "desk",
      slot: "main",
    });
  });

  it("should let an explicit target_host override the panel host", () => {
    expect(
      resolveGenUiHost(
        JSON.stringify({
          request: "NVDA",
          target_host: {
            host_type: "finance.company-research",
            host_id: "US:NVDA",
          },
        }),
        PANEL,
      ),
    ).toEqual({
      host_type: "finance.company-research",
      host_id: "US:NVDA",
      slot: "main",
    });
  });

  it("should keep an explicit slot from target_host", () => {
    expect(
      resolveGenUiHost(
        JSON.stringify({
          target_host: { host_type: "h", host_id: "i", slot: "sidebar" },
        }),
        null,
      )?.slot,
    ).toBe("sidebar");
  });

  it("should fall back to the panel when target_host is half-formed", () => {
    // A host with no id cannot be addressed; treating it as an override would
    // strand the generation between two hosts.
    expect(
      resolveGenUiHost(
        JSON.stringify({ target_host: { host_type: "h" } }),
        PANEL,
      ),
    ).toEqual({ ...PANEL, slot: "main" });
  });

  it("should fall back to the panel while the input is still streaming", () => {
    // Tool input arrives token-by-token, so it is invalid JSON for most of the
    // run — exactly the window the mirror needs to be live in.
    expect(resolveGenUiHost('{"request": "the des', PANEL)).toEqual({
      ...PANEL,
      slot: "main",
    });
  });

  it("should tolerate an absent input", () => {
    expect(resolveGenUiHost(undefined, PANEL)).toEqual({
      ...PANEL,
      slot: "main",
    });
    expect(resolveGenUiHost(undefined, null)).toBe(null);
  });

  it("should ignore a panel host that is missing an id", () => {
    expect(resolveGenUiHost(undefined, { host_type: "h", host_id: "" })).toBe(
      null,
    );
  });
});

describe("hostDocumentFileName", () => {
  it("mirrors the server's per-host document naming exactly", () => {
    expect(
      hostDocumentFileName({
        host_type: "finance.research-desk",
        host_id: "desk",
        slot: "main",
      }),
    ).toBe("finance.research-desk.desk.main.a2ui.jsonl");
  });

  it("collapses unsafe runs and defaults the slot", () => {
    expect(
      hostDocumentFileName({
        host_type: "finance.company-research",
        host_id: "US:NVDA",
        slot: "",
      }),
    ).toBe("finance.company-research.US-NVDA.main.a2ui.jsonl");
  });
});

describe("automationProposalGate — the confirm gate", () => {
  const ok = { ok: true, proposal: { name: "x" } };
  const rejected = { ok: false, message: "bad" };

  it("only a server-validated proposal is submittable", () => {
    expect(automationProposalGate(ok, undefined)).toEqual({
      rejected: false,
      submittable: true,
    });
  });

  it("a rejected tool result is rejected and not submittable", () => {
    expect(automationProposalGate(rejected, undefined)).toEqual({
      rejected: true,
      submittable: false,
    });
  });

  it("a runtime error (unparseable/wrapped failure) is rejected", () => {
    expect(automationProposalGate(null, "error")).toEqual({
      rejected: true,
      submittable: false,
    });
  });

  it("no result yet (running / unparsed output) is not submittable but not rejected", () => {
    expect(automationProposalGate(null, undefined)).toEqual({
      rejected: false,
      submittable: false,
    });
  });

  it("ok:true without a proposal is not submittable", () => {
    expect(automationProposalGate({ ok: true, proposal: null }, undefined)).toEqual({
      rejected: false,
      submittable: false,
    });
  });
});
