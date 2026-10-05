import { definePlugin } from "@valuz/core";

import { startPluginUi } from "../plugin-ui/host";

/**
 * The UI bus (hooks-and-plugin-ui.md §6): what backend plugins draw into
 * Valuz's UI slots, and their pushes (toast / status / log / notice /
 * invalidate). Registers nothing up front — surfaces mount only where the
 * backend says a plugin draws (see ``startPluginUi``).
 */
export const ossPluginUiPlugin = definePlugin({
  id: "oss-plugin-ui",
  apply(ctx) {
    ctx.effect(() => startPluginUi());
  },
});
