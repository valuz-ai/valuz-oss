import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
});
