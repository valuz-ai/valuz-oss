import { render, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import type { ArtifactDescriptor } from "./artifact-viewer.types";
import { CodeMirrorRenderer } from "./CodeMirrorRenderer";

function artifact(
  previewKind: "code" | "plain" | "markdown",
): ArtifactDescriptor {
  const path =
    previewKind === "code"
      ? "example.unknown"
      : previewKind === "markdown"
        ? "notes.md"
        : "notes.txt";
  return {
    id: `artifact:${previewKind}`,
    kind: "project_file",
    path,
    name: path,
    previewKind,
    capabilities: {
      canPreview: true,
      canEdit: false,
      canOpenExternal: false,
      canCopyContent: true,
      canDownload: true,
    },
  };
}

const content = {
  kind: "text" as const,
  encoding: "utf-8" as const,
  content: "a very long line that should only wrap in plain text mode",
  truncated: false,
};

beforeAll(() => {
  // CodeMirror measures text ranges; jsdom intentionally omits these layout
  // APIs, so provide neutral geometry for renderer behavior tests.
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => new DOMRect(),
  });
});

describe("CodeMirrorRenderer", () => {
  it("enables line wrapping for plain text", async () => {
    const { container } = render(
      <CodeMirrorRenderer artifact={artifact("plain")} content={content} />,
    );

    await waitFor(() =>
      expect(container.querySelector(".cm-lineWrapping")).not.toBeNull(),
    );
  });

  it("preserves horizontal code layout", async () => {
    const { container } = render(
      <CodeMirrorRenderer artifact={artifact("code")} content={content} />,
    );

    await waitFor(() =>
      expect(container.querySelector(".cm-editor")).not.toBeNull(),
    );
    expect(container.querySelector(".cm-lineWrapping")).toBeNull();
  });

  it("can wrap source code when the host requests it", async () => {
    const { container } = render(
      <CodeMirrorRenderer
        artifact={artifact("code")}
        content={content}
        wrapLines
      />,
    );

    await waitFor(() =>
      expect(container.querySelector(".cm-lineWrapping")).not.toBeNull(),
    );
  });

  it("shows line numbers and an active line while remaining read-only", async () => {
    const { container } = render(
      <CodeMirrorRenderer artifact={artifact("code")} content={content} />,
    );

    await waitFor(() =>
      expect(container.querySelector(".cm-editor")).not.toBeNull(),
    );
    expect(container.querySelector(".cm-lineNumbers")).not.toBeNull();
    expect(container.querySelector(".cm-activeLine")).not.toBeNull();
    const editorContent = container.querySelector(".cm-content");
    expect(editorContent?.getAttribute("contenteditable")).toBe("true");
    expect(editorContent?.getAttribute("aria-readonly")).toBe("true");
    expect(editorContent?.getAttribute("spellcheck")).toBe("false");
    expect(editorContent?.getAttribute("autocorrect")).toBe("off");
    expect(editorContent?.getAttribute("autocapitalize")).toBe("off");
  });

  it("applies the no-underline theme to Markdown source", async () => {
    const { container } = render(
      <CodeMirrorRenderer
        artifact={artifact("markdown")}
        content={{
          ...content,
          content: "# Heading\n\n[Link](https://example.com)",
        }}
        wrapLines
      />,
    );

    await waitFor(() =>
      expect(container.querySelector(".cm-editor")).not.toBeNull(),
    );
    expect(container.querySelector(".valuz-markdown-source")).not.toBeNull();
  });
});
