import { registerParserPluginUI } from "@valuz/core";

import zhCN from "./locale.zh-CN.json";
import enUS from "./locale.en-US.json";

export function register(): void {
  registerParserPluginUI({
    id: "light_local",
    i18nNamespace: "parser_light_local",
    locales: {
      "zh-CN": zhCN,
      "en-US": enUS,
    },
  });
}
