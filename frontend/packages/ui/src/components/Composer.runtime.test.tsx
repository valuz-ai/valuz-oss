/** @vitest-environment jsdom */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MAX_SESSION_ATTACHMENTS } from "@valuz/shared";
import { Composer, type RuntimeSelectorItem } from "./Composer";
import type { SkillSearchItem } from "./conversation/SkillSearchMenu";

const sampleRuntimes: RuntimeSelectorItem[] = [
  { id: "claude_agent", displayName: "Claude Agent", available: true },
  { id: "codex", displayName: "Codex Agent", available: true },
  {
    id: "deepagents",
    displayName: "Valuz Agent",
    available: false,
    unavailableReason: "binary missing",
  },
];

describe("Composer runtime selector (REP-107)", () => {
  it("shows provider selection hints in the model picker trigger", () => {
    render(
      <Composer
        providers={[
          {
            providerId: "valuz",
            providerName: "Valuz",
            modelId: "gpt-5.9",
            selectionHint: "2×",
            isDefault: true,
            source: "system",
          },
        ]}
        selectedProviderId="valuz"
        selectedModelId="gpt-5.9"
      />,
    );

    expect(screen.getByText("GPT 5.9 · 2×")).toBeTruthy();
  });

  it("does not render the runtime trigger when runtimes prop is empty", () => {
    render(<Composer runtimes={[]} />);
    expect(
      screen.queryByText(/Claude Agent|Codex Agent|Valuz Agent/),
    ).toBeNull();
  });

  it("falls back to the first available runtime label when none is selected", () => {
    render(<Composer runtimes={sampleRuntimes} selectedRuntimeId={null} />);
    // Trigger button shows the first available runtime — Valuz Agent
    // is unavailable, so claude_agent wins. Use queryAllByText because
    // the same label may also appear inside the dropdown if it's open.
    expect(screen.getAllByText("Claude Agent").length).toBeGreaterThan(0);
  });

  it("does NOT show a 默认 Runtime placeholder option", () => {
    render(<Composer runtimes={sampleRuntimes} />);
    expect(screen.queryByText(/默认\s*Runtime/)).toBeNull();
  });

  it("opens the dropdown and lists every runtime", () => {
    render(
      <Composer runtimes={sampleRuntimes} selectedRuntimeId="claude_agent" />,
    );
    // Click the trigger (the displayed label is the runtime name now).
    const triggers = screen.getAllByText("Claude Agent");
    fireEvent.click(triggers[0]);
    // After opening, all three runtime names appear in the dropdown.
    expect(screen.getAllByText("Claude Agent").length).toBeGreaterThan(0);
    expect(screen.getByText("Codex Agent")).toBeTruthy();
    expect(screen.getByText("Valuz Agent")).toBeTruthy();
  });

  it("calls onRuntimeChange + clears model on selection", () => {
    const onRuntimeChange = vi.fn();
    const onModelChange = vi.fn();
    render(
      <Composer
        runtimes={sampleRuntimes}
        selectedRuntimeId="claude_agent"
        selectedProviderId="ch-x"
        selectedModelId="some-model"
        providers={[
          {
            providerId: "ch-x",
            providerName: "Anthropic",
            modelId: "some-model",
            isDefault: false,
          },
        ]}
        onRuntimeChange={onRuntimeChange}
        onModelChange={onModelChange}
      />,
    );

    fireEvent.click(screen.getAllByText("Claude Agent")[0]);
    fireEvent.click(screen.getByText("Codex Agent"));

    expect(onRuntimeChange).toHaveBeenCalledWith("codex");
    expect(onModelChange).toHaveBeenCalledWith(null, null);
  });

  it("does not invoke onRuntimeChange when an unavailable runtime is clicked", () => {
    const onRuntimeChange = vi.fn();
    render(
      <Composer
        runtimes={sampleRuntimes}
        selectedRuntimeId="claude_agent"
        onRuntimeChange={onRuntimeChange}
      />,
    );

    fireEvent.click(screen.getAllByText("Claude Agent")[0]);
    // Valuz Agent is the unavailable one in sampleRuntimes.
    fireEvent.click(screen.getByText("Valuz Agent"));

    expect(onRuntimeChange).not.toHaveBeenCalled();
  });

  it("does not open the dropdown when modelLocked is true", () => {
    render(
      <Composer
        runtimes={sampleRuntimes}
        selectedRuntimeId="claude_agent"
        modelLocked
      />,
    );
    fireEvent.click(screen.getAllByText("Claude Agent")[0]);
    // The other runtimes would only appear if the dropdown opened.
    expect(screen.queryByText("Codex Agent")).toBeNull();
  });

  it("shows the runtime's display name when one is selected", () => {
    render(<Composer runtimes={sampleRuntimes} selectedRuntimeId="codex" />);
    expect(screen.getByText("Codex Agent")).toBeTruthy();
  });
});

describe("Composer agent selector layering", () => {
  it("renders the agent menu outside clipping panel containers", () => {
    const { getByTestId } = render(
      <div data-testid="clipping-panel" style={{ overflow: "hidden" }}>
        <Composer
          agents={[
            {
              slug: "valurion",
              name: "小万",
              runtimeLabel: "Claude Code",
              modelLabel: "Valuz Pro",
            },
          ]}
          selectedAgentSlug="valurion"
          allowAgentBrainOverride
          runtimes={sampleRuntimes}
          selectedRuntimeId="claude_agent"
          providers={[
            {
              providerId: "valuz",
              providerName: "Valuz",
              modelId: "valuz-pro",
              isDefault: true,
            },
          ]}
          selectedProviderId="valuz"
          selectedModelId="valuz-pro"
        />
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: /小万/ }));

    const agentMenu = document.querySelector(
      '[data-slot="composer-agent-menu"]',
    );

    expect(agentMenu).toBeTruthy();
    expect(getByTestId("clipping-panel").contains(agentMenu!)).toBe(false);

    fireEvent.mouseDown(agentMenu!);
    expect(document.body.contains(agentMenu!)).toBe(true);
  });
});

describe("Composer IME submission guard", () => {
  it("does not send when Enter confirms an active IME composition", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);

    fireEvent.keyDown(screen.getByRole("textbox"), {
      key: "Enter",
      isComposing: true,
    });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send for IME composition keyCode 229", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);

    fireEvent.keyDown(screen.getByRole("textbox"), {
      key: "Enter",
      keyCode: 229,
    });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("still sends on a normal Enter press", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);

    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });

    expect(onSend).toHaveBeenCalledTimes(1);
  });
});

describe("Composer slash-command pass-through", () => {
  const SKILLS: SkillSearchItem[] = [
    { id: "1", name: "deep-research", description: "research deeply" },
  ];

  /** Drive the ``/`` trigger the way real typing does: the picker only opens
   *  when the *last* keystroke is the bare ``/``, then subsequent input feeds
   *  the query. So set ``/`` first, then the full token. */
  const typeSlashToken = (editor: HTMLElement, token: string) => {
    editor.textContent = "/";
    fireEvent.input(editor);
    editor.textContent = token;
    fireEvent.input(editor);
  };

  it("sends a /command that matches no skill on Enter (no longer swallowed)", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} skills={SKILLS} />);
    const editor = screen.getByRole("textbox");

    typeSlashToken(editor, "/compact");
    // No skill matches "compact" → picker closed → Enter sends.
    fireEvent.keyDown(editor, { key: "Enter" });

    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("does NOT send while the skill picker is open with matches", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} skills={SKILLS} />);
    const editor = screen.getByRole("textbox");

    typeSlashToken(editor, "/deep");
    // "deep" matches deep-research → picker open → Enter is captured by it.
    fireEvent.keyDown(editor, { key: "Enter" });

    expect(onSend).not.toHaveBeenCalled();
  });
});

describe("Composer ``/`` picker decoupled from the toolbar skill button", () => {
  const SKILLS: SkillSearchItem[] = [
    { id: "1", name: "deep-research", description: "research deeply" },
  ];
  const typeSlashToken = (editor: HTMLElement, token: string) => {
    editor.textContent = "/";
    fireEvent.input(editor);
    editor.textContent = token;
    fireEvent.input(editor);
  };

  it("keeps the ``/`` picker closed when the toolbar button is hidden and slash is unset (back-compat)", () => {
    // showSkillSlash defaults to showSkillButton, so hiding the button still
    // disables the inline picker for callers that never opt in.
    const onSend = vi.fn();
    render(<Composer onSend={onSend} skills={SKILLS} showSkillButton={false} />);
    const editor = screen.getByRole("textbox");

    typeSlashToken(editor, "/deep");
    expect(screen.queryByText("deep-research")).toBeNull();
    // Picker never opened → Enter sends the verbatim command.
    fireEvent.keyDown(editor, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("opens the ``/`` picker via showSkillSlash even when the toolbar button is hidden (project conversation)", () => {
    // The project-conversation case: no "add skill" button, but the selected
    // agent's bound skills are invocable with ``/``.
    const onSend = vi.fn();
    render(
      <Composer
        onSend={onSend}
        skills={SKILLS}
        showSkillButton={false}
        showSkillSlash
      />,
    );
    const editor = screen.getByRole("textbox");

    typeSlashToken(editor, "/deep");
    expect(screen.queryByText("deep-research")).toBeTruthy();
    // Picker open with a match → Enter is captured, not sent.
    fireEvent.keyDown(editor, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });
});

describe("Composer pasted image attachments", () => {
  const pasteImage = (editor: HTMLElement, files: File[]) => {
    fireEvent.paste(editor, {
      clipboardData: {
        getData: vi.fn(() => "clipboard text that should not be inserted"),
        items: files.map((file) => ({
          kind: "file",
          type: file.type,
          getAsFile: () => file,
        })),
      },
    });
  };

  it("hands pasted images to upload-on-attach instead of inserting clipboard text", () => {
    const onLocalUpload = vi.fn();
    const file = new File(["png"], "screenshot.png", { type: "image/png" });
    render(<Composer uploadOnAttach onLocalUpload={onLocalUpload} />);
    const editor = screen.getByRole("textbox");

    pasteImage(editor, [file]);

    expect(onLocalUpload).toHaveBeenCalledWith([file]);
    expect(editor.textContent).not.toContain("clipboard text");
  });

  it("clamps pasted images to the remaining attachment slots", () => {
    const onLocalUpload = vi.fn();
    const first = new File(["a"], "first.png", { type: "image/png" });
    const second = new File(["b"], "second.png", { type: "image/png" });
    render(
      <Composer
        uploadOnAttach
        existingAttachmentCount={MAX_SESSION_ATTACHMENTS - 1}
        onLocalUpload={onLocalUpload}
      />,
    );
    const editor = screen.getByRole("textbox");

    pasteImage(editor, [first, second]);

    expect(onLocalUpload).toHaveBeenCalledWith([first]);
  });
});

describe("Composer local attachment inputs", () => {
  it("keeps file-picker uploads on the existing onLocalUpload path", () => {
    const onLocalUpload = vi.fn();
    const file = new File(["txt"], "notes.txt", { type: "text/plain" });
    const { container } = render(
      <Composer uploadOnAttach onLocalUpload={onLocalUpload} />,
    );
    const input = container.querySelector<HTMLInputElement>(
      "input[type='file']",
    );

    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { files: [file] } });

    expect(onLocalUpload).toHaveBeenCalledWith([file]);
  });

  it("keeps dropped files on the existing onFileDrop path", () => {
    const onFileDrop = vi.fn();
    const file = new File(["txt"], "dropped.txt", { type: "text/plain" });
    render(<Composer uploadOnAttach onFileDrop={onFileDrop} />);
    const editor = screen.getByRole("textbox");

    fireEvent.drop(editor, {
      dataTransfer: {
        files: [file],
      },
    });

    expect(onFileDrop).toHaveBeenCalledWith([file]);
  });
});
