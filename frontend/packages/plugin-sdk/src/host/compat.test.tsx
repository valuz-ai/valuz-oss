import { describe, expect, it } from "vitest";
import { definePlugin, slotComponent } from "../index";
import { createTestHost } from "../testing/test-host";
import { adaptAppPlugin } from "./adapt";
import { adaptThirdPartyPlugin } from "./compat";

// Already packaged bundles can still resolve the old host entry point and CSS
// selector, while new SDK builders and host translations use canonical names.
describe("App Plugin package compatibility", () => {
  it("keeps the original adapter export and old scoped CSS selector usable", async () => {
    expect(adaptThirdPartyPlugin).toBe(adaptAppPlugin);
    const host = createTestHost();
    await host.load(definePlugin({
      id: "acme.legacy",
      apply(ctx) {
        ctx.registry.slot("conversation.title.actions", {
          id: "legacy",
          component: slotComponent(() => <button type="button">Legacy</button>),
        });
      },
    }));
    const markup = host.renderSlot("conversation.title.actions", {});
    expect(markup).toContain('data-valuz-app-plugin="acme.legacy"');
    expect(markup).toContain('data-valuz-ext="acme.legacy"');
    expect(markup).toContain("Legacy");
  });
});
