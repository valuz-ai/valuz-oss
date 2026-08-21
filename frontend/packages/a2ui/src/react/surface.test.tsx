import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VALUZ_BASE_CATALOG_ID } from "../catalog";
import { createValuzMessageProcessor } from "./catalog";
import { ValuzA2UISurface } from "./surface";
import {
  registerA2UIThemeExtension,
  resetA2UIThemeExtensionsForTests,
} from "../theme/registry";

const createSurface = {
  version: "v0.9.1" as const,
  createSurface: {
    surfaceId: "test-surface",
    catalogId: VALUZ_BASE_CATALOG_ID,
  },
};

describe("ValuzA2UISurface", () => {
  afterEach(() => act(resetA2UIThemeExtensionsForTests));

  it("renders catalog components and dispatches actions", () => {
    const onAction = vi.fn();
    const processor = createValuzMessageProcessor(onAction);
    processor.processMessages([
      createSurface,
      {
        version: "v0.9.1",
        updateComponents: {
          surfaceId: "test-surface",
          components: [
            { id: "root", component: "Stack", children: ["heading", "card"], gap: "lg" },
            { id: "heading", component: "TextContent", text: "Independent A2UI", variant: "h2" },
            { id: "card", component: "Card", children: ["body", "button"], title: "Reusable surface" },
            { id: "body", component: "TextContent", text: "Rendered by the Valuz catalog." },
            {
              id: "button",
              component: "Button",
              label: "Continue",
              action: { event: { name: "continue" } },
            },
          ],
        },
      },
    ]);

    const surface = processor.model.getSurface("test-surface");
    expect(surface).toBeDefined();
    const { container } = render(<ValuzA2UISurface surface={surface!} theme="dark" />);

    expect(screen.getByRole("heading", { name: "Independent A2UI" })).toBeTruthy();
    expect(screen.getByText("Reusable surface")).toBeTruthy();
    expect(container.querySelector('.valuz-a2ui[data-theme="dark"]')).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ name: "continue", surfaceId: "test-surface" }),
    );
  });

  it("reacts to A2UI data-model updates", () => {
    const processor = createValuzMessageProcessor();
    processor.processMessages([
      createSurface,
      {
        version: "v0.9.1",
        updateDataModel: { surfaceId: "test-surface", path: "/title", value: "First title" },
      },
      {
        version: "v0.9.1",
        updateComponents: {
          surfaceId: "test-surface",
          components: [
            { id: "root", component: "Stack", children: ["title"] },
            { id: "title", component: "TextContent", text: { path: "/title" }, variant: "h3" },
          ],
        },
      },
    ]);

    const surface = processor.model.getSurface("test-surface");
    render(<ValuzA2UISurface surface={surface!} />);
    expect(screen.getByText("First title")).toBeTruthy();

    act(() => {
      processor.processMessages([
        {
          version: "v0.9.1",
          updateDataModel: { surfaceId: "test-surface", path: "/title", value: "Updated title" },
        },
      ]);
    });
    expect(screen.getByText("Updated title")).toBeTruthy();
  });

  it("reacts to explicit distribution theme registration", () => {
    const processor = createValuzMessageProcessor();
    processor.processMessages([
      createSurface,
      {
        version: "v0.9.1",
        updateComponents: {
          surfaceId: "test-surface",
          components: [{ id: "root", component: "TextContent", text: "Theme" }],
        },
      },
    ]);
    const surface = processor.model.getSurface("test-surface")!;
    const { container } = render(<ValuzA2UISurface surface={surface} />);
    const root = container.querySelector<HTMLElement>(".valuz-a2ui")!;
    expect(root.dataset.theme).toBe("light");
    expect(root.style.getPropertyValue("--va2-test-accent")).toBe("");

    act(() => {
      registerA2UIThemeExtension({
        id: "test",
        tokens: { light: { "--va2-test-accent": "#123456" } },
      });
    });
    expect(root.style.getPropertyValue("--va2-test-accent")).toBe("#123456");
  });
});
