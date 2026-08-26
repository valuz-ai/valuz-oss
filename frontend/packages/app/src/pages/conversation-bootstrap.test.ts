import { describe, expect, it } from "vitest";

import { createConversationBootstrapGuard } from "./conversation-bootstrap";

describe("createConversationBootstrapGuard", () => {
  it("invalidates an older request when a newer route bootstrap starts", () => {
    const guard = createConversationBootstrapGuard();
    const first = guard.start();
    const second = guard.start();

    expect(first.isCurrent()).toBe(false);
    expect(second.isCurrent()).toBe(true);
  });

  it("does not let an obsolete cleanup cancel the current request", () => {
    const guard = createConversationBootstrapGuard();
    const first = guard.start();
    const second = guard.start();

    first.cancel();
    expect(second.isCurrent()).toBe(true);

    second.cancel();
    expect(second.isCurrent()).toBe(false);
  });
});
