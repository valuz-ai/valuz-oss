import type { ComponentType } from "react";

import { createContributions } from "../../../lib/contributions";

/**
 * The blocks on the Plugins page below the 「Valuz 扩展」 list. The page is
 * core; what it hosts is not — each block belongs to the plugin whose feature
 * it manages (the dsh plugins manager is ``oss-dsh-plugins``), so the block
 * disappears with the plugin.
 */
export const pluginSettingsBlocks = createContributions<ComponentType>();

export const registerPluginSettingsBlock = (
  id: string,
  Component: ComponentType,
  options?: { order?: number },
): (() => void) => pluginSettingsBlocks.register(id, Component, options);
