import { render, screen } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { sessionsApi } from "@valuz/core";
import { routes } from "./router";

describe("webui routes", () => {
  beforeEach(() => {
    // ChatPage's session picker calls sessionsApi.list() on mount.
    // Stub it so the test environment doesn't try to hit the backend
    // and so we can assert on the empty-state UI deterministically.
    vi.spyOn(sessionsApi, "list").mockResolvedValue({ sessions: [] });
  });

  it("should render the chat page when navigating to /chat", async () => {
    const router = createMemoryRouter(routes, {
      initialEntries: ["/chat"],
    });

    render(<RouterProvider router={router} />);

    // The session picker header is the most stable static landmark on
    // an empty chat page — it's present even before any sessions load.
    expect(await screen.findByText(/sessions/i)).toBeTruthy();
    expect(
      await screen.findByText(/Select a session from the sidebar/i),
    ).toBeTruthy();
  });
});
