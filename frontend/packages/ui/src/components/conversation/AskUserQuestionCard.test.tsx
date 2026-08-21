/** @vitest-environment jsdom */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  AskUserQuestionCard,
  type AskUserQuestionItem,
} from "./AskUserQuestionCard";

const question = (
  overrides: Partial<AskUserQuestionItem> = {},
): AskUserQuestionItem => ({
  question: "第4个条件「最新收盘价相对近60日最低价的涨幅上限」应设为多少？",
  header: "60日涨幅上限",
  options: [{ label: "20%" }, { label: "30%" }],
  ...overrides,
});

describe("AskUserQuestionCard — header placement", () => {
  it("hoists a lone header into the title row, out of the question block", () => {
    render(
      <AskUserQuestionCard questions={[question()]} onSubmit={() => {}} />,
    );

    const pill = screen.getByText("60日涨幅上限");
    // In the title row (zh-CN is the default test locale) …
    expect(pill.closest("div")?.textContent).toContain("请选择");
    // … which sits outside the question's fieldset.
    expect(pill.closest("fieldset")).toBeNull();
    // Rendered once — not duplicated above the question.
    expect(screen.getAllByText("60日涨幅上限")).toHaveLength(1);
  });

  it("keeps per-question headers when the card asks more than one question", () => {
    render(
      <AskUserQuestionCard
        questions={[
          question(),
          question({ question: "回测区间取多久？", header: "回测区间" }),
        ]}
        onSubmit={() => {}}
      />,
    );

    // Each pill stays with its own question …
    for (const label of ["60日涨幅上限", "回测区间"]) {
      expect(screen.getByText(label).closest("fieldset")).not.toBeNull();
    }
    // … and none of them is hoisted into the title row.
    const title = screen.getByText("请选择").parentElement;
    expect(title?.textContent).toBe("请选择");
  });

  it("renders the question alone when the model supplies no header", () => {
    render(
      <AskUserQuestionCard
        questions={[question({ header: undefined })]}
        onSubmit={() => {}}
      />,
    );

    expect(screen.queryByText("60日涨幅上限")).toBeNull();
    expect(screen.getByText(/第4个条件/)).toBeTruthy();
  });
});
