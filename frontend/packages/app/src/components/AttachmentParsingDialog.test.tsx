/** @vitest-environment jsdom */
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { initI18n } from "@valuz/shared/i18n";

import { AttachmentParsingDialog } from "./AttachmentParsingDialog";

beforeAll(() => initI18n({ locale: "en-US", fallbackLocale: "en-US" }));

describe("AttachmentParsingDialog (X-02 submit-while-parsing gate)", () => {
  it("renders nothing while closed", () => {
    render(
      <AttachmentParsingDialog open={false} onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(screen.queryByText("Submit anyway")).toBeNull();
  });

  it("fires onConfirm when the user chooses 'Submit anyway'", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <AttachmentParsingDialog open onConfirm={onConfirm} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByText("Submit anyway"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("fires onCancel when the user cancels", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <AttachmentParsingDialog open onConfirm={onConfirm} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByText("Cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
