import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CronInput, cronToSimpleParts } from "./CronInput";

describe("cronToSimpleParts", () => {
  it("should parse daily expression", () => {
    expect(cronToSimpleParts("0 9 * * *")).toEqual({
      frequency: "daily",
      hour: 9,
      minute: 0,
      weekday: 1,
      monthDay: 1,
    });
  });

  it("should parse weekdays expression (1-5)", () => {
    const parts = cronToSimpleParts("30 8 * * 1-5");
    expect(parts?.frequency).toBe("weekdays");
    expect(parts?.hour).toBe(8);
    expect(parts?.minute).toBe(30);
  });

  it("should parse weekly expression with single weekday", () => {
    const parts = cronToSimpleParts("0 9 * * 3");
    expect(parts?.frequency).toBe("weekly");
    expect(parts?.weekday).toBe(3);
  });

  it("should normalise day-of-week 0 to 7 for Sunday", () => {
    const parts = cronToSimpleParts("0 9 * * 0");
    expect(parts?.weekday).toBe(7);
  });

  it("should parse monthly expression", () => {
    const parts = cronToSimpleParts("0 9 15 * *");
    expect(parts?.frequency).toBe("monthly");
    expect(parts?.monthDay).toBe(15);
  });

  it("should preserve non-quarter minute (e.g. 5)", () => {
    const parts = cronToSimpleParts("5 9 * * *");
    expect(parts?.minute).toBe(5);
  });

  it("should return null for ranges like '0 9-17 * * *'", () => {
    expect(cronToSimpleParts("0 9-17 * * *")).toBeNull();
  });

  it("should return null for step expressions like '*/15 * * * *'", () => {
    expect(cronToSimpleParts("*/15 * * * *")).toBeNull();
  });

  it("should return null for lists like '0 9 * 1,7 *'", () => {
    expect(cronToSimpleParts("0 9 * 1,7 *")).toBeNull();
  });

  it("should return null for 6-field (second-granularity) cron", () => {
    expect(cronToSimpleParts("*/30 0 9 * * *")).toBeNull();
  });

  it("should return null for empty input", () => {
    expect(cronToSimpleParts("")).toBeNull();
  });
});

describe("CronInput component", () => {
  it("should switch to advanced mode for unrepresentable cron expression", () => {
    render(<CronInput value="*/15 * * * *" onChange={() => {}} />);
    // Advanced text input contains the raw value
    const input = screen.getByPlaceholderText(
      "0 9 * * 1-5",
    ) as HTMLInputElement;
    expect(input).not.toBeNull();
    expect(input.value).toBe("*/15 * * * *");
  });

  it("should render advanced text input with the value when bypassing simple-mode rules", () => {
    render(<CronInput value="0 9 1,15 * *" onChange={() => {}} />);
    const input = screen.getByPlaceholderText(
      "0 9 * * 1-5",
    ) as HTMLInputElement;
    expect(input.value).toBe("0 9 1,15 * *");
  });
});
