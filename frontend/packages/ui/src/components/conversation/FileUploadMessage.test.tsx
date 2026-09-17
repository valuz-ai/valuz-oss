import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FileUploadMessage } from "./FileUploadMessage";

describe("FileUploadMessage", () => {
  it("renders the conversation attachment icon from the shared file type set", () => {
    render(
      <FileUploadMessage
        fileName="chart.png"
        fileSize="2.0 KB"
        status="ready"
      />,
    );

    expect(screen.getByText("chart.png")).toBeTruthy();
    expect(screen.getByTestId("conversation-file-type-icon")).toBeTruthy();
  });

  it("stays a plain receipt when the host cannot open the file", () => {
    // The share replay renders the same turns for a viewer with no file
    // access — drawing a control that cannot work would be a lie.
    render(<FileUploadMessage fileName="chart.png" status="ready" />);

    expect(screen.queryByRole("button")).toBeNull();
  });

  it("opens the file when the card is clicked", () => {
    const onOpen = vi.fn();
    render(
      <FileUploadMessage fileName="chart.png" status="ready" onOpen={onOpen} />,
    );

    fireEvent.click(screen.getByRole("button"));

    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("opens the file from the keyboard", () => {
    const onOpen = vi.fn();
    render(
      <FileUploadMessage fileName="chart.png" status="ready" onOpen={onOpen} />,
    );

    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });

    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
