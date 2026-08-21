import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("./A2UIBody", () => ({
  default: (props: { body: string }) => <div data-testid="renderer">{props.body}</div>,
}));
vi.mock("../../hooks/use-i18n", () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));

import { GenerativeUICard } from "./GenerativeUICard";

const STREAM = [
  JSON.stringify({ version: "v0.9.1", createSurface: { surfaceId: "s", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" } }),
  JSON.stringify({ version: "v0.9.1", updateComponents: { surfaceId: "s", components: [{ id: "root", component: "TextContent", text: "Chart" }] } }),
].join("\n");

describe("GenerativeUICard", () => {
  it("renders an A2UI payload and marks the new scope", async () => {
    const { container } = render(<GenerativeUICard a2ui={STREAM} />);
    expect((await screen.findByTestId("renderer")).textContent).toBe(STREAM);
    expect(container.querySelector('[data-a2ui-scope="generative-ui"]')).toBeTruthy();
  });

  it("opens a fullscreen preview", () => {
    render(<GenerativeUICard a2ui={STREAM} />);
    fireEvent.click(screen.getByRole("button", { name: "genui.fullscreen" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getAllByTestId("renderer")).toHaveLength(2);
  });

  it("shows reasoning while running and removes it after completion", () => {
    const { rerender } = render(<GenerativeUICard status="running" thinking="planning" />);
    expect(screen.getByTestId("genui-thinking").textContent).toBe("planning");
    rerender(<GenerativeUICard a2ui={STREAM} status="success" thinking="planning" />);
    expect(screen.queryByTestId("genui-thinking")).toBeNull();
  });
});
