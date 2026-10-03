import { render, screen } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createPluginHost,
  personalProfile,
  sessionsApi,
  tasksApi,
  useRegistryStore,
} from "@valuz/core";
import { loadOssPlugins } from "@valuz/app/plugins";
import { buildRouteObjects } from "./router";

vi.mock("@valuz/app/lib/onboarding", () => ({
  isOnboarded: () => true,
}));

describe("webui routes", () => {
  beforeAll(async () => {
    // The routes are registered by the OSS plugins.
    useRegistryStore.getState().clearLayers();
    useRegistryStore.getState().hydrate(personalProfile);
    await loadOssPlugins(createPluginHost(), { inactive: [] });
  });

  beforeEach(() => {
    // ChatPage's session picker calls sessionsApi.list() on mount.
    // Stub it so the test environment doesn't try to hit the backend
    // and so we can assert on the empty-state UI deterministically.
    vi.spyOn(sessionsApi, "list").mockResolvedValue({ sessions: [] });
    vi.spyOn(tasksApi, "listAllTasks").mockResolvedValue({ tasks: [] });
  });

  it("should render the app shell when navigating to a conversation route", async () => {
    const router = createMemoryRouter(buildRouteObjects(), {
      initialEntries: ["/conversation/new"],
    });

    render(<RouterProvider router={router} />);

    expect(await screen.findByLabelText("Valuz Agent menu")).toBeTruthy();
  });
});
