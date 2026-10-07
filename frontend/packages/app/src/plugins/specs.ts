import type { ValuzPlugin } from "@valuz/core";

import { ossActivityPlugin } from "./activity";
import { ossAgentPluginsPlugin } from "./agent-plugins";
import { ossAgentsPlugin } from "./agents";
import { ossAutomationsPlugin } from "./automations";
import { ossBackupPlugin } from "./backup";
import { ossBrowserPlugin } from "./browser";
import { ossConnectorsPlugin } from "./connectors";
import { ossCorePlugin } from "./core";
import { ossDshPluginsPlugin } from "./dsh-plugins";
import { ossKnowledgePlugin } from "./knowledge";
import { ossMarketplacePlugin } from "./marketplace";
import { ossMemoryPlugin } from "./memory";
import { ossPluginUiPlugin } from "./plugin-ui";
import { ossSkillsPlugin } from "./skills";
import { ossTasksPlugin } from "./tasks";
import { ossAppPluginsPlugin } from "./app-plugins";

export interface OssPluginSpec {
  plugin: ValuzPlugin;
  /**
   * A required plugin that fails stops the boot (the host shows its boot
   * failure page) and is never skipped. An optional one is left disabled and
   * the rest still load — and is skipped when its backend counterpart is off.
   */
  required: boolean;
}

/**
 * The OSS app's own features as plugins, in load order.
 *
 * Each plugin id is the id of its BACKEND counterpart (``oss-automations`` here
 * is ``oss-automations`` there): that is the whole pairing, so a backend plugin
 * that is switched off keeps its frontend namesake from loading (see
 * ``loadOssPlugins``). A backend feature with no frontend surface of its own
 * has no entry here.
 *
 * Load order only decides the order of SLOT contributions (the OSS plugins make
 * none today); routes, settings sections and sidebar items are positioned by
 * placement (./layout), so it never changes what the user sees. ``oss-core``
 * must stay first: it starts each registry list the others hang off.
 */
export const ossPluginSpecs: readonly OssPluginSpec[] = [
  { plugin: ossCorePlugin, required: true },
  { plugin: ossAgentsPlugin, required: true },
  { plugin: ossTasksPlugin, required: false },
  { plugin: ossAutomationsPlugin, required: false },
  { plugin: ossActivityPlugin, required: false },
  { plugin: ossSkillsPlugin, required: false },
  { plugin: ossConnectorsPlugin, required: false },
  { plugin: ossKnowledgePlugin, required: false },
  { plugin: ossMemoryPlugin, required: false },
  { plugin: ossBrowserPlugin, required: false },
  { plugin: ossBackupPlugin, required: false },
  { plugin: ossMarketplacePlugin, required: false },
  { plugin: ossAgentPluginsPlugin, required: false },
  { plugin: ossDshPluginsPlugin, required: false },
  { plugin: ossPluginUiPlugin, required: false },
  { plugin: ossAppPluginsPlugin, required: false },
];

/** The OSS plugins in canonical order — see {@link ossPluginSpecs}. */
export const ossPlugins: readonly ValuzPlugin[] = ossPluginSpecs.map(
  (spec) => spec.plugin,
);
