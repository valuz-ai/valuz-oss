/** @vitest-environment jsdom */
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const navigate = vi.fn();
vi.mock("react-router-dom", () => ({
  useNavigate: () => navigate,
}));

import { useProjectHandoff } from "./useProjectHandoff";

/** Everything the hook needs that this test does not care about. */
const inert = () => ({
  id: "s1",
  searchParams: new URLSearchParams(),
  selectedProjectId: "p1",
  draft: null,
  attachmentsParsing: false,
  markPendingConsumed: vi.fn(),
  historyCursorRef: { current: 0 },
  projectSendHandoffRef: { current: null },
  handoffSessionIdRef: { current: null as string | null },
  draftBootstrapSettled: true,
  setTurnStartAnchor: vi.fn(),
  setSending: vi.fn(),
  setParsingConfirmOpen: vi.fn(),
  getDisplayBusy: () => false,
  performEnqueue: vi.fn(),
  performSend: vi.fn(),
});

const at = (state: unknown) => ({
  pathname: "/conversation/s1",
  search: "",
  hash: "",
  state,
  key: "k",
});

const render = (state: unknown) => {
  const setPendingUserMessage = vi.fn();
  renderHook(() =>
    useProjectHandoff({
      ...inert(),
      location: at(state) as never,
      setPendingUserMessage,
    } as never),
  );
  return setPendingUserMessage;
};

describe("useProjectHandoff", () => {
  it("shows the files that shipped with the handed-over turn", () => {
    // The receiving page cannot work these out for itself: both it and the
    // sending page stamp the rows consumed, so its own pending view is empty
    // by the time the attachments load lands. Seeding an empty list is what
    // made an attached image show up only when the server echoed the turn
    // back — the text sat on screen alone for seconds first.
    const attachments = [{ name: "shot.png", size: 73101 }];
    const setPendingUserMessage = render({
      handoff: { text: "look at this", sentAt: Date.now(), attachments },
    });

    expect(setPendingUserMessage).toHaveBeenCalledWith(
      expect.objectContaining({ text: "look at this", attachments }),
    );
  });

  it("still shows the message when the turn carried no files", () => {
    const setPendingUserMessage = render({
      handoff: { text: "hello", sentAt: Date.now() },
    });

    expect(setPendingUserMessage).toHaveBeenCalledWith(
      expect.objectContaining({ text: "hello", attachments: [] }),
    );
  });

  it("ignores a handoff old enough to be a reload replay", () => {
    const setPendingUserMessage = render({
      handoff: {
        text: "old",
        sentAt: Date.now() - 60_000,
        attachments: [{ name: "a.png", size: 1 }],
      },
    });

    expect(setPendingUserMessage).not.toHaveBeenCalled();
  });
});
