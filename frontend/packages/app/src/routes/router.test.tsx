import { render, screen } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryRouter,
} from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useCitationDocumentPreview } from "../components/CitationDocumentPreviewProvider";
import { WebPlatformProvider } from "../platform";
import { createAppRouteObjects } from "./router";

function CitationContextProbe() {
  const { openCitation } = useCitationDocumentPreview();
  return <div>citation context: {typeof openCitation}</div>;
}

describe("createAppRouteObjects", () => {
  it("provides citation document preview context for custom route roots", async () => {
    const router = createMemoryRouter(
      createAppRouteObjects({
        routes: [],
        Root: Outlet,
        layout: Outlet,
        extraRoutes: [
          {
            path: "/citation-context",
            element: <CitationContextProbe />,
          },
        ],
      }),
      { initialEntries: ["/citation-context"] },
    );

    render(
      <WebPlatformProvider>
        <RouterProvider router={router} />
      </WebPlatformProvider>,
    );

    expect(await screen.findByText("citation context: function")).toBeTruthy();
  });
});
