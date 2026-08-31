import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FilterPillGroup } from "./FilterPillGroup";

describe("FilterPillGroup", () => {
  it("renders counts and reports the selected filter", () => {
    const onValueChange = vi.fn();

    render(
      <FilterPillGroup
        aria-label="Scenario filters"
        value={null}
        onValueChange={onValueChange}
        options={[
          { value: null, label: "All" },
          { value: "monitoring", label: "Monitoring", count: 3 },
        ]}
      />,
    );

    const all = screen.getByRole("button", { name: "All" });
    const monitoring = screen.getByRole("button", { name: "Monitoring 3" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
    expect(all.className).toContain("rounded-full");
    expect(monitoring.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(monitoring);
    expect(onValueChange).toHaveBeenCalledWith("monitoring");
  });
});
