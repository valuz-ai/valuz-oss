import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { A2UIGallery } from "./Gallery";

vi.mock("../react", async (importOriginal) => ({
  ...await importOriginal<typeof import("../react")>(),
  ValuzA2UISurface: () => null,
}));

describe("A2UI Gallery menu scrolling", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "#layout");
    vi.spyOn(HTMLElement.prototype, "scrollTo").mockImplementation(function scrollTo(
      this: HTMLElement,
      x: number,
      y: number,
    ) {
      const options = x as unknown as number | ScrollToOptions;
      this.scrollTop = typeof options === "number" ? y : options.top ?? 0;
    });
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      callback(0);
      return 1;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps chart specimens inside the Gallery content track", () => {
    const stylesheet = readFileSync(resolve(__dirname, "gallery.css"), "utf8");

    expect(stylesheet).toMatch(
      /\.demo-specimen-list\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/s,
    );
    expect(stylesheet).toMatch(
      /\.demo-specimen\s*\{[^}]*max-width:\s*100%[^}]*min-width:\s*0/s,
    );
  });

  it("starts unopened menus at the top and restores each visited menu independently", () => {
    const first = render(<A2UIGallery embedded />);
    const content = first.container.querySelector<HTMLElement>(".demo-content");
    expect(content).not.toBeNull();

    content!.scrollTop = 420;
    fireEvent.scroll(content!);

    fireEvent.click(screen.getByRole("button", { name: /内容/ }));
    expect(content).toHaveProperty("scrollTop", 0);

    content!.scrollTop = 160;
    fireEvent.scroll(content!);

    fireEvent.click(screen.getByRole("button", { name: /布局/ }));
    expect(content).toHaveProperty("scrollTop", 420);

    fireEvent.click(screen.getByRole("button", { name: /内容/ }));
    expect(content).toHaveProperty("scrollTop", 160);

    first.unmount();
    const reloaded = render(<A2UIGallery embedded />);
    expect(reloaded.container.querySelector(".demo-content")).toHaveProperty("scrollTop", 0);
  });

  it("shows all eight chart palettes at the top of the chart gallery", () => {
    render(<A2UIGallery embedded />);

    fireEvent.click(screen.getByRole("button", { name: /图表与可视化/ }));

    const showcase = screen.getByRole("complementary", { name: "图表色板" });
    expect(showcase.querySelectorAll(".demo-palette-card")).toHaveLength(8);
    expect(within(showcase).getByText("ocean")).toBeTruthy();
    expect(within(showcase).getByText("orchid")).toBeTruthy();
    expect(within(showcase).getByText("emerald")).toBeTruthy();
    expect(within(showcase).getByText("spectrum")).toBeTruthy();
    expect(within(showcase).getByText("sunset")).toBeTruthy();
    expect(within(showcase).getByText("vivid")).toBeTruthy();
    expect(within(showcase).getByText("steel")).toBeTruthy();
    expect(within(showcase).getByText("amber")).toBeTruthy();
    expect(showcase.querySelectorAll(".demo-palette-strip i")).toHaveLength(88);
  });

  it("lets every chart specimen select one palette independently", () => {
    render(<A2UIGallery embedded />);
    fireEvent.click(screen.getByRole("button", { name: /图表与可视化/ }));

    expect(screen.getAllByLabelText(/色板，当前/)).toHaveLength(16);
    const linePicker = screen.getByLabelText("LineChart 色板，当前 ocean");
    fireEvent.click(linePicker);
    const lineOptions = screen.getByRole("radiogroup", { name: "LineChart 选择色板" });
    fireEvent.click(within(lineOptions).getByRole("radio", { name: /vivid/ }));

    expect(screen.getByLabelText("LineChart 色板，当前 vivid")).toBeTruthy();
    expect(screen.getByLabelText("AreaChart 色板，当前 ocean")).toBeTruthy();
  });

  it("lets every professional chart specimen select a palette", () => {
    render(<A2UIGallery embedded />);
    fireEvent.click(screen.getByRole("button", { name: /专业图表/ }));

    expect(screen.getAllByLabelText(/色板，当前/)).toHaveLength(9);
    const histogramPicker = screen.getByLabelText("HistogramChart 色板，当前 ocean");
    fireEvent.click(histogramPicker);
    const histogramOptions = screen.getByRole("radiogroup", { name: "HistogramChart 选择色板" });
    fireEvent.click(within(histogramOptions).getByRole("radio", { name: /sunset/ }));

    expect(screen.getByLabelText("HistogramChart 色板，当前 sunset")).toBeTruthy();
    expect(screen.getByLabelText("WaterfallChart 色板，当前 ocean")).toBeTruthy();
  });
});
