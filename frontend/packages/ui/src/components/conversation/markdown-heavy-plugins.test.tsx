/**
 * Detection for the lazily-loaded markdown plugins.
 *
 * The whole win depends on two predicates, and they fail in opposite ways: a
 * missed diagram or formula renders as its own source text forever (a bug a
 * reader sees), while an over-eager match costs one unused chunk (a bug nobody
 * sees). These cases pin both directions so a later tightening of the regexes
 * cannot quietly turn the cheap failure into the expensive one.
 */

import { describe, expect, it, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

import {
  useHeavyMarkdownPlugins,
  __resetHeavyPluginCache,
} from "./markdown-heavy-plugins";

beforeEach(() => {
  __resetHeavyPluginCache();
});

describe("useHeavyMarkdownPlugins", () => {
  it("loads nothing for ordinary prose", async () => {
    const { result } = renderHook(() =>
      useHeavyMarkdownPlugins(
        "Just a paragraph with a [link](https://x.test).",
      ),
    );
    // Nothing to await — assert it stays empty across a settle.
    await Promise.resolve();
    expect(result.current).toEqual({});
  });

  it("loads nothing for a fenced code block that is not a diagram", async () => {
    const { result } = renderHook(() =>
      useHeavyMarkdownPlugins("```python\nprint('hi')\n```"),
    );
    await Promise.resolve();
    expect(result.current.mermaid).toBeUndefined();
  });

  it("does not treat the word mermaid in prose as a diagram", async () => {
    const { result } = renderHook(() =>
      useHeavyMarkdownPlugins(
        "We render diagrams with mermaid, which is nice.",
      ),
    );
    await Promise.resolve();
    expect(result.current.mermaid).toBeUndefined();
  });

  it("loads mermaid for a fenced mermaid block", async () => {
    const { result } = renderHook(() =>
      useHeavyMarkdownPlugins("```mermaid\ngraph TD; A-->B;\n```"),
    );
    await waitFor(() => expect(result.current.mermaid).toBeDefined());
    expect(result.current.math).toBeUndefined();
  });

  it("loads mermaid from the opening fence alone, mid-stream", async () => {
    // Streaming delivers the info string long before the diagram body; the
    // import has to start then, not when the closing fence lands.
    const { result } = renderHook(() =>
      useHeavyMarkdownPlugins("Here you go:\n\n```mermaid\ngraph TD;"),
    );
    await waitFor(() => expect(result.current.mermaid).toBeDefined());
  });

  it("loads math for display math", async () => {
    const { result } = renderHook(() =>
      useHeavyMarkdownPlugins("计算如下：\n\n$$\n增长率 = 15.71\\%\n$$"),
    );
    await waitFor(() => expect(result.current.math).toBeDefined());
    expect(result.current.mermaid).toBeUndefined();
  });

  it("loads math for inline math", async () => {
    const { result } = renderHook(() =>
      useHeavyMarkdownPlugins("The area is $\\pi r^2$ exactly."),
    );
    await waitFor(() => expect(result.current.math).toBeDefined());
  });

  it("loads math for backslash delimiters", async () => {
    const { result } = renderHook(() =>
      useHeavyMarkdownPlugins("Given \\(x > 0\\) the result holds."),
    );
    await waitFor(() => expect(result.current.math).toBeDefined());
  });

  it("does not treat a lone currency amount as math", async () => {
    const { result } = renderHook(() =>
      useHeavyMarkdownPlugins("The plan costs $50 per seat per month."),
    );
    await Promise.resolve();
    expect(result.current.math).toBeUndefined();
  });

  it("loads both when a document has a diagram and a formula", async () => {
    const { result } = renderHook(() =>
      useHeavyMarkdownPlugins("$$a=b$$\n\n```mermaid\ngraph TD; A-->B;\n```"),
    );
    await waitFor(() => {
      expect(result.current.math).toBeDefined();
      expect(result.current.mermaid).toBeDefined();
    });
  });
});
