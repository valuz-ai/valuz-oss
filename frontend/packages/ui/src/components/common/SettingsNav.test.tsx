import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SettingsNav } from "./SettingsNav";

describe("SettingsNav", () => {
  it("renders grouped desktop navigation and preserves item selection", () => {
    const onValueChange = vi.fn();

    render(
      <SettingsNav
        value="general"
        onValueChange={onValueChange}
        items={[
          {
            id: "general",
            label: "General",
            group: { id: "personal", label: "Personal Settings" },
          },
          {
            id: "model",
            label: "Models",
            group: { id: "runtime", label: "Runtime & Tools" },
          },
        ]}
      />,
    );

    expect(screen.getByText("Personal Settings")).toBeTruthy();
    expect(screen.getByText("Runtime & Tools")).toBeTruthy();

    fireEvent.click(screen.getAllByRole("button", { name: "Models" })[0]);
    expect(onValueChange).toHaveBeenCalledWith("model");
  });
});
