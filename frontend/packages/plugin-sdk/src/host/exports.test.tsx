import { describe, expect, it } from "vitest";
import { definePlugin, slotComponent } from "../index";
import { createTestHost } from "../testing/test-host";
import * as pluginHost from "./index";

// The application-plugin contract is new: only its canonical public surface
// and CSS scope are available to packages built with this SDK.
describe("App Plugin host exports", () => {
  it("exposes the canonical adapter and rejects former export names", () => {
    expect(pluginHost.adaptAppPlugin).toBeTypeOf("function");
    expect(pluginHost).not.toHaveProperty("adaptThirdPartyPlugin");
    expect(pluginHost).not.toHaveProperty("EXTENSIONS_SETTINGS_GROUP");
  });

  it("renders only the canonical App Plugin CSS scope", async () => {
    const host = createTestHost();
    await host.load(definePlugin({
      id: "acme.scope",
      apply(ctx) {
        ctx.registry.slot("conversation.title.actions", {
          id: "scope",
          component: slotComponent(() => <button type="button">App Plugin</button>),
        });
      },
    }));
    const markup = host.renderSlot("conversation.title.actions", {});
    expect(markup).toContain('data-valuz-app-plugin="acme.scope"');
    expect(markup).not.toContain("data-valuz-ext");
  });
});
