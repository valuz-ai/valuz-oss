/** @vitest-environment jsdom */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { initI18n } from "@valuz/shared/i18n";
import type { NotificationEntry } from "@valuz/core";

import { NotificationCard } from "./NotificationCard";

const { submitActionMock, interveneMock, dismissNotificationMock } = vi.hoisted(
  () => ({
    submitActionMock: vi.fn().mockResolvedValue({}),
    interveneMock: vi.fn().mockResolvedValue({}),
    dismissNotificationMock: vi.fn(),
  }),
);

vi.mock("@valuz/core", async () => {
  const actual =
    await vi.importActual<typeof import("@valuz/core")>("@valuz/core");
  return {
    ...actual,
    sessionsApi: { ...actual.sessionsApi, submitAction: submitActionMock },
    tasksApi: { ...actual.tasksApi, intervene: interveneMock },
    dismissNotification: dismissNotificationMock,
  };
});

beforeAll(() => initI18n({ locale: "zh-CN", fallbackLocale: "en-US" }));

const base: NotificationEntry = {
  id: "n1",
  kind: "question",
  title: "architect",
  body: "选哪种布局？",
  route: "/tasks/t1",
  action: "answer",
  urgency: "actionable",
  task_id: "t1",
  project_id: "w1",
  session_id: "s1",
  pending_id: "p1",
  payload: {
    question_payload: {
      questions: [
        {
          question: "选哪种布局？",
          header: "布局",
          options: [{ label: "3×3" }, { label: "自由" }],
        },
      ],
    },
  },
  created_at: 1,
  read_at: null,
  resolved_at: null,
};

const renderCard = (entry: NotificationEntry) =>
  render(
    <MemoryRouter>
      <NotificationCard entry={entry} />
    </MemoryRouter>,
  );

describe("NotificationCard — question kind", () => {
  it("renders the AskUserQuestion inline and answers via submitAction (non-session)", async () => {
    renderCard(base);
    // The question renders (proving you can answer OUTSIDE the session).
    expect(screen.getByText("选哪种布局？")).toBeTruthy();
    // Select an option (enables submit), then hit the answer button.
    fireEvent.click(screen.getByText("3×3"));
    const submit = screen
      .getAllByRole("button")
      .find((b) => b.textContent?.includes("回答"));
    expect(submit).toBeTruthy();
    fireEvent.click(submit!);
    await waitFor(() => expect(submitActionMock).toHaveBeenCalled());
    const [sid, req] = submitActionMock.mock.calls[0];
    expect(sid).toBe("s1");
    expect(req.pending_id).toBe("p1");
    expect(req.decision).toBe("answer");
  });
});

describe("NotificationCard — task_failed kind", () => {
  const failure: NotificationEntry = {
    ...base,
    id: "n2",
    kind: "task_failed",
    title: "季度报告",
    body: "lead crashed",
    action: "resume",
    route: "/tasks/t9",
    task_id: "t9",
    pending_id: null,
    session_id: null,
    payload: { reason: "lead crashed" },
  };

  it("resume calls intervene(resume)", async () => {
    renderCard(failure);
    expect(screen.getByText("lead crashed")).toBeTruthy();
    const resumeBtn = screen
      .getAllByRole("button")
      .find((b) => b.textContent?.includes("恢复"));
    fireEvent.click(resumeBtn!);
    await waitFor(() => expect(interveneMock).toHaveBeenCalled());
    expect(interveneMock.mock.calls[0][0]).toBe("t9");
    expect(interveneMock.mock.calls[0][1]).toEqual({ action: "resume" });
  });

  it("dismiss removes optimistically via dismissNotification", async () => {
    renderCard(failure);
    const dismissBtn = screen
      .getAllByRole("button")
      .find((b) => b.textContent?.includes("清除"));
    fireEvent.click(dismissBtn!);
    await waitFor(() =>
      expect(dismissNotificationMock).toHaveBeenCalledWith("n2"),
    );
  });
});
