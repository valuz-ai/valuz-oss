import { describe, expect, it } from "vitest";

import {
  calculatePdfScale,
} from "./pdf-zoom";

describe("calculatePdfScale", () => {
  const page = { width: 600, height: 800 };

  it("fits the page width and follows a wider viewport", () => {
    expect(
      calculatePdfScale({
        mode: "fit-width",
        page,
        viewport: { width: 900, height: 700 },
        customScale: 1.25,
      }),
    ).toBeCloseTo(1.5);

    expect(
      calculatePdfScale({
        mode: "fit-width",
        page,
        viewport: { width: 1_200, height: 700 },
        customScale: 1.25,
      }),
    ).toBeCloseTo(2);
  });

  it("fits one whole page within both viewport dimensions", () => {
    expect(
      calculatePdfScale({
        mode: "fit-page",
        page,
        viewport: { width: 900, height: 640 },
        customScale: 1.25,
      }),
    ).toBeCloseTo(0.8);
  });

  it("keeps a custom zoom independent of viewport size", () => {
    expect(
      calculatePdfScale({
        mode: "custom",
        page,
        viewport: { width: 300, height: 200 },
        customScale: 1.75,
      }),
    ).toBe(1.75);
  });

  it("falls back to the custom scale before layout measurements are valid", () => {
    expect(
      calculatePdfScale({
        mode: "fit-width",
        page,
        viewport: { width: Number.NaN, height: 0 },
        customScale: 1.25,
      }),
    ).toBe(1.25);
  });

});
