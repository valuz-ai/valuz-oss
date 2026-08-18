import { describe, expect, it } from "vitest";

import { turnPreviewText } from "./turn-preview";

describe("turnPreviewText", () => {
  it("returns an empty string for a missing or blank message", () => {
    expect(turnPreviewText(undefined)).toBe("");
    expect(turnPreviewText(null)).toBe("");
    expect(turnPreviewText("   \n\t  ")).toBe("");
  });

  it("collapses newlines and runs of whitespace into single spaces", () => {
    expect(turnPreviewText("帮我分析\n\n这份财报   里的毛利率")).toBe(
      "帮我分析 这份财报 里的毛利率",
    );
  });

  it("strips leading /skill invocations but keeps slashes in the body", () => {
    expect(turnPreviewText("/research 看看 a/b 这个目录")).toBe(
      "看看 a/b 这个目录",
    );
    expect(turnPreviewText("/plan /deep 拆一下任务")).toBe("拆一下任务");
  });

  it("drops fenced code blocks whole, including an unterminated one", () => {
    expect(turnPreviewText("修一下这个\n```ts\nconst a = 1\n```\n谢谢")).toBe(
      "修一下这个 谢谢",
    );
    expect(turnPreviewText("看这段\n```py\nprint(1)")).toBe("看这段");
  });

  it("keeps link text, drops images", () => {
    expect(turnPreviewText("参考 [年报](https://x.com/a.pdf) 第三章")).toBe(
      "参考 年报 第三章",
    );
    expect(turnPreviewText("![chart](a.png) 这张图")).toBe("这张图");
  });

  it("removes heading, quote, list and emphasis markers", () => {
    expect(turnPreviewText("## 目标\n- 第一点\n- 第二点")).toBe(
      "目标 第一点 第二点",
    );
    expect(turnPreviewText("> 引用\n1. 一\n2) 二")).toBe("引用 一 二");
    expect(turnPreviewText("**很重要** 的 `code` 和 _斜体_")).toBe(
      "很重要 的 code 和 斜体",
    );
  });

  it("truncates past maxChars with an ellipsis and no dangling space", () => {
    expect(turnPreviewText("abcdefghij", 4)).toBe("abcd…");
    expect(turnPreviewText("abcd efgh", 5)).toBe("abcd…");
    expect(turnPreviewText("abcde", 5)).toBe("abcde");
  });

  it("returns an empty string when the message was only markup", () => {
    expect(turnPreviewText("```\ncode only\n```")).toBe("");
  });
});
