import { definePlugin } from "@valuz/core";

import { DshPluginsBlock } from "../pages/settings/extensions/DshPluginsBlock";
import { registerExtensionsBlock } from "../pages/settings/extensions/blocks";

/**
 * Standard DSH plugins: the block of Settings → 扩展 that manages them
 * through dsh's own plugin manager. The page is core; this block goes with
 * the plugin.
 */
export const ossDshPluginsPlugin = definePlugin({
  id: "oss-dsh-plugins",
  apply(ctx) {
    ctx.effect(() =>
      registerExtensionsBlock("oss-dsh-plugins", DshPluginsBlock),
    );
  },
});
