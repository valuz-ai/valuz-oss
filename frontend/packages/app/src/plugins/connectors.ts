import { definePlugin } from "@valuz/core";

import { ConnectorsPage } from "../pages";
import { pageRoute } from "./define";
import { routePlacement } from "./layout";

/** Data connectors and MCP integrations: the connectors page. */
export const ossConnectorsPlugin = definePlugin({
  id: "oss-connectors",
  apply(ctx) {
    pageRoute(
      ctx,
      {
        id: "connectors",
        path: "/connectors",
        label: "nav.connectors",
        description:
          "Data connectors and MCP integrations available to the agent.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      ConnectorsPage,
      routePlacement("connectors"),
    );
  },
});
