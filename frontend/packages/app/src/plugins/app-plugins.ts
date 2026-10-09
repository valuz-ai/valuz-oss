import { definePlugin } from "@valuz/core";

import { getAppPluginRuntime } from "./app-plugins-runtime/runtime";

/**
 * Third-party plugins (ADR-034, doc 04 §4.2): reads the installed list from
 * the backend once the first-party app has settled, ``import()``s each enabled
 * plugin, and loads it into the plugin host through the restricted context.
 * Installs, enables, disables, updates and uninstalls apply on the spot.
 *
 * Registers nothing up front. Its backend counterpart is the optional
 * ``oss-app-plugins`` feature, active on local deployments only, so on a
 * cloud deployment (or a backend that has it switched off) this plugin is not
 * loaded at all; a backend that answers 403 / 404 to the list leaves it idle.
 */
export const ossAppPluginsPlugin = definePlugin({
  id: "oss-app-plugins",
  apply(ctx) {
    ctx.effect(() => {
      const runtime = getAppPluginRuntime();
      void runtime.start();
      return () => {
        void runtime.stop();
      };
    });
  },
});
