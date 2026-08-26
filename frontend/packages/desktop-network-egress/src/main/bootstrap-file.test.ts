import { chmodSync, lstatSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { publishDevEgressBootstrap } from "./bootstrap-file";

describe("publishDevEgressBootstrap", () => {
  it("publishes a complete 0600 one-shot payload in a private directory", () => {
    const root = mkdtempSync(join(tmpdir(), "valuz-egress-bootstrap-"));
    chmodSync(root, 0o700);
    const path = join(root, "bootstrap.json");

    publishDevEgressBootstrap(path, {
      mode: "auto",
      controlEndpoint: "http://127.0.0.1:43123",
      bootstrapToken: "memory-only-token",
      expiresAt: 123_456,
    });

    expect(JSON.parse(readFileSync(path, "utf8"))).toMatchObject({
      bootstrapToken: "memory-only-token",
    });
    expect(lstatSync(path).mode & 0o777).toBe(0o600);
    expect(() =>
      publishDevEgressBootstrap(path, {
        mode: "auto",
        controlEndpoint: "http://127.0.0.1:43123",
        bootstrapToken: "replacement",
        expiresAt: 123_456,
      }),
    ).toThrow("egress_bootstrap_file_exists");
  });
});
