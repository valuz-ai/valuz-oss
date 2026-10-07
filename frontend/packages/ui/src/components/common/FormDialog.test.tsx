import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { FormDialog } from "./FormDialog";

afterEach(cleanup);

it("labels a form dialog without pointing to a missing description", () => {
  render(<FormDialog open onOpenChange={() => {}} title="Install"><span>Fields</span></FormDialog>);
  expect(screen.getByRole("dialog", { name: "Install" }).hasAttribute("aria-describedby")).toBe(false);
});

it("associates an authored description with its form dialog", () => {
  render(<FormDialog open onOpenChange={() => {}} title="Install" description="Requested permissions"><span>Fields</span></FormDialog>);
  const dialog = screen.getByRole("dialog", { name: "Install" });
  expect(document.getElementById(dialog.getAttribute("aria-describedby")!)?.textContent).toBe("Requested permissions");
});
