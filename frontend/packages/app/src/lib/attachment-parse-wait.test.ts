import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { waitForAttachmentsToSettle } from "./attachment-parse-wait";

const { listAttachments } = vi.hoisted(() => ({ listAttachments: vi.fn() }));

vi.mock("@valuz/core", () => ({ sessionsApi: { listAttachments } }));

const row = (over: Record<string, unknown> = {}) => ({
  id: "a1",
  parse_status: "parsing",
  consumed_at: null,
  ...over,
});

beforeEach(() => {
  vi.useFakeTimers();
  listAttachments.mockReset();
});
afterEach(() => vi.useRealTimers());

describe("waitForAttachmentsToSettle", () => {
  it("returns at once when nothing is parsing", async () => {
    listAttachments.mockResolvedValue({
      items: [row({ parse_status: "ready" })],
    });

    await waitForAttachmentsToSettle("s1", { pollMs: 10 });

    expect(listAttachments).toHaveBeenCalledTimes(1);
  });

  it("waits for the parse, then lets the turn go", async () => {
    listAttachments
      .mockResolvedValueOnce({ items: [row()] })
      .mockResolvedValueOnce({ items: [row()] })
      .mockResolvedValue({ items: [row({ parse_status: "ready" })] });

    const settled = vi.fn();
    void waitForAttachmentsToSettle("s1", { pollMs: 10 }).then(settled);

    await vi.advanceTimersByTimeAsync(5);
    expect(settled).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(40);
    expect(settled).toHaveBeenCalled();
  });

  it("does not wait on attachments an earlier turn already consumed", async () => {
    listAttachments.mockResolvedValue({
      items: [row({ consumed_at: "2026-08-21T00:00:00Z" })],
    });

    await waitForAttachmentsToSettle("s1", { pollMs: 10 });

    expect(listAttachments).toHaveBeenCalledTimes(1);
  });

  it("gives up rather than swallowing the message", async () => {
    // A parse that never settles must not lose the turn: past the deadline it
    // goes out with the raw source path, which is what it did before this
    // wait existed.
    listAttachments.mockResolvedValue({ items: [row()] });

    const settled = vi.fn();
    void waitForAttachmentsToSettle("s1", { timeoutMs: 50, pollMs: 10 }).then(
      settled,
    );
    await vi.advanceTimersByTimeAsync(200);

    expect(settled).toHaveBeenCalled();
  });

  it("keeps waiting through a failed read", async () => {
    // "Cannot tell" is not "settled" — treating a blip as done would ship the
    // turn without the extract for no reason.
    listAttachments
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue({ items: [row({ parse_status: "ready" })] });

    const settled = vi.fn();
    void waitForAttachmentsToSettle("s1", { pollMs: 10 }).then(settled);

    await vi.advanceTimersByTimeAsync(5);
    expect(settled).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(20);
    expect(settled).toHaveBeenCalled();
    expect(listAttachments).toHaveBeenCalledTimes(2);
  });

  it("never rejects, even when every read fails", async () => {
    listAttachments.mockRejectedValue(new Error("down"));

    await expect(
      (async () => {
        const p = waitForAttachmentsToSettle("s1", {
          timeoutMs: 30,
          pollMs: 10,
        });
        await vi.advanceTimersByTimeAsync(200);
        return p;
      })(),
    ).resolves.toBeUndefined();
  });
});
