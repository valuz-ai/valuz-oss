import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SettingsSection } from "./SettingsSection";

describe("SettingsSection actions", () => {
  it("renders the title block exactly as before when no actions are passed", () => {
    const { container } = render(
      <SettingsSection title="通用" desc="外观与快捷键">
        <p>内容</p>
      </SettingsSection>,
    );
    const titleBlock = container.querySelector("section > div");
    // No flex wrapper: the heading and description are direct children.
    expect(titleBlock?.className).toBe("mb-3");
    expect(
      Array.from(titleBlock?.children ?? []).map((el) => el.tagName),
    ).toEqual(["H2", "P"]);
  });

  it("treats an empty actions value like no actions", () => {
    const { container } = render(
      <SettingsSection title="通用" actions={null}>
        <p>内容</p>
      </SettingsSection>,
    );
    const titleBlock = container.querySelector("section > div");
    expect(
      Array.from(titleBlock?.children ?? []).map((el) => el.tagName),
    ).toEqual(["H2"]);
  });

  it("renders actions beside the title, after it", () => {
    const { container } = render(
      <SettingsSection
        title="通用"
        desc="外观与快捷键"
        actions={<button type="button">导出</button>}
      >
        <p>内容</p>
      </SettingsSection>,
    );
    const heading = screen.getByRole("heading", { name: "通用" });
    const action = screen.getByRole("button", { name: "导出" });
    expect(
      heading.compareDocumentPosition(action) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // Both live in the same title row; the section body is untouched.
    expect(container.querySelector("section > div")?.children).toHaveLength(1);
    expect(screen.getByText("外观与快捷键")).toBeTruthy();
    expect(screen.getByText("内容")).toBeTruthy();
  });
});
