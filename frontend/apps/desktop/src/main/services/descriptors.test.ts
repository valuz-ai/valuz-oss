import { describe, expect, it } from "vitest";
import { DescriptorRegistry, personalDescriptors } from "./descriptors";

describe("DescriptorRegistry", () => {
  it("upserts and removes service descriptors by name", () => {
    const registry = new DescriptorRegistry(personalDescriptors());

    registry.register({
      name: "plugin-echo",
      kind: "plugin",
      defaultPort: 20001,
      requiredForBoot: false,
    });

    registry.register({
      name: "plugin-echo",
      kind: "plugin",
      defaultPort: 20002,
      requiredForBoot: false,
    });

    expect(
      registry.snapshot().find((item) => item.name === "plugin-echo")
        ?.defaultPort,
    ).toBe(20002);
    expect(registry.unregister("plugin-echo")).toBe(true);
  });
});
