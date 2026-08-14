import { describe, expect, it } from "vitest";

import {
  compatibleRuntimes,
  isProviderRuntimeCompatible,
} from "./runtime-compat";
import type { LLMChannel } from "./providers-api";

const ch = (
  runtimesPerModel: (string[] | null)[],
): Pick<LLMChannel, "models"> => ({
  models: runtimesPerModel.map((runtimes, i) => ({
    id: `m${i}`,
    label: null,
    runtimes,
  })),
});

describe("runtime-compat", () => {
  it("is compatible with a runtime when any model declares it", () => {
    const p = ch([["claude_agent", "deepagents"]]);
    expect(isProviderRuntimeCompatible(p, "claude_agent")).toBe(true);
    expect(isProviderRuntimeCompatible(p, "deepagents")).toBe(true);
    expect(isProviderRuntimeCompatible(p, "codex")).toBe(false);
  });

  it("surfaces codex for a custom openai-response channel", () => {
    const p = ch([["codex"]]);
    expect(compatibleRuntimes(p)).toEqual(["codex"]);
  });

  it("compatibleRuntimes is the priority-ordered union across models", () => {
    const p = ch([["deepagents"], ["claude_agent", "deepagents"], ["codex"]]);
    expect(compatibleRuntimes(p)).toEqual([
      "claude_agent",
      "codex",
      "deepagents",
    ]);
  });

  it("surfaces deepseek_harness for a DeepSeek-channel model", () => {
    const p = ch([["deepagents", "deepseek_harness"]]);
    expect(compatibleRuntimes(p)).toEqual(["deepagents", "deepseek_harness"]);
  });

  it("tolerates null/empty model runtimes", () => {
    const p = ch([null, []]);
    expect(compatibleRuntimes(p)).toEqual([]);
  });
});
