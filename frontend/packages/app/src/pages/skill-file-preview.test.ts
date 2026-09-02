/**
 * What counts as "cannot preview" in the skill file viewer.
 *
 * The previous heuristic scored the share of printable-ASCII characters, so a
 * SKILL.md written in Chinese — most of them — scored near zero and rendered
 * as "[Binary file - cannot preview]".
 */
import { describe, expect, it } from "vitest";

import { isBinaryContent } from "./skill-file-preview";

describe("isBinaryContent", () => {
  it("treats a Chinese skill manifest as text", () => {
    const skillMd = [
      "---",
      "name: daily-weather-forecast",
      "description: 查询全国及全球主要城市的每日天气预报，输出简洁文字摘要加 Markdown 表格。",
      "---",
      "",
      "# 每日天气预报",
      "",
      "使用免费 Open-Meteo API，无需任何密钥。当用户提到“天气预报”“今天天气”时使用此技能。",
    ].join("\n");

    expect(isBinaryContent(skillMd)).toBe(false);
  });

  it("treats plain English markdown as text", () => {
    expect(isBinaryContent("# Title\n\nSome prose, a `code` span.\n")).toBe(
      false,
    );
  });

  it("treats an empty file as text", () => {
    expect(isBinaryContent("")).toBe(false);
  });

  it("tolerates a stray replacement character", () => {
    expect(
      isBinaryContent("一段正常的中文说明，只有一个坏字节 � 在里面。"),
    ).toBe(false);
  });

  it("flags content carrying NUL bytes", () => {
    expect(isBinaryContent("PK\u0000\u0003binary")).toBe(true);
  });

  it("flags content that mostly failed to decode", () => {
    expect(isBinaryContent("�����ab")).toBe(true);
  });
});
