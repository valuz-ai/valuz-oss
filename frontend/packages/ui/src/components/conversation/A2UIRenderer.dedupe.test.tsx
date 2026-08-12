import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { A2UIRenderer } from "./A2UIRenderer";

/** Models occasionally restart mid-document and emit ``createSurface`` for an
 *  already-created surface. The state machine throws on the duplicate; without
 *  the dedupe the catch blanked the ENTIRE payload (empty tool card). */
describe("A2UIRenderer duplicate createSurface", () => {
  it("keeps rendering when the document re-creates an existing surface", () => {
    const messages = [
      {
        version: "v0.9.1",
        createSurface: { surfaceId: "main", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" },
      },
      {
        version: "v0.9.1",
        updateComponents: {
          surfaceId: "main",
          components: [
            { id: "root", component: "TextContent", text: "Hello workbench" },
          ],
        },
      },
      {
        version: "v0.9.1",
        createSurface: { surfaceId: "main", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" },
      },
      {
        version: "v0.9.1",
        updateComponents: {
          surfaceId: "main",
          components: [
            { id: "root", component: "TextContent", text: "Second pass" },
          ],
        },
      },
    ]
      .map((message) => JSON.stringify(message))
      .join("\n");

    const { container } = render(<A2UIRenderer body={messages} />);
    expect(container.textContent).toContain("Second pass");
  });
});
