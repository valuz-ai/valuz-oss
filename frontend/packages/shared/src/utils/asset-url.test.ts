import { describe, expect, it } from "vitest";
import { assetUrl } from "./asset-url";

describe("assetUrl", () => {
  it("joins a bare filename to the base", () => {
    expect(assetUrl("logo.png", "/")).toBe("/logo.png");
  });

  it("strips a leading dot-slash", () => {
    expect(assetUrl("./logo.png", "/")).toBe("/logo.png");
  });

  it("strips a leading slash", () => {
    expect(assetUrl("/logo.png", "/app/")).toBe("/app/logo.png");
  });

  it("supports a relative base for desktop/file builds", () => {
    expect(assetUrl("logo.png", "./")).toBe("./logo.png");
  });

  it("adds a trailing slash when the base omits one", () => {
    expect(assetUrl("logo.png", "/app")).toBe("/app/logo.png");
  });

  it("uses the vite-injected BASE_URL by default", () => {
    // In the Vitest/Vite test runner BASE_URL is "/", matching the webui.
    expect(assetUrl("logo.png")).toBe("/logo.png");
  });
});
