import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ErrorMessageCard } from "./ErrorMessageCard";

describe("ErrorMessageCard layout boundaries", () => {
  it("wraps long unbroken details without forcing the card wider", () => {
    const longToken = `https://example.local/${"x".repeat(240)}`;
    const { container } = render(<ErrorMessageCard message={longToken} />);

    fireEvent.click(screen.getByRole("button", { name: /查看详情|details/i }));

    const details = screen.getByText(longToken);
    expect(details.className).toContain("max-w-full");
    expect(details.className).toContain("whitespace-pre-wrap");
    expect(details.className).toContain("break-words");
    expect(
      container.querySelector("[data-slot='error-card-content']")?.className,
    ).toContain("min-w-0");
  });
});
