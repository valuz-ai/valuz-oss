import { registerParserPluginUI } from "@valuz/core";

import zhCN from "./locale.zh-CN.json";
import enUS from "./locale.en-US.json";

export function register(): void {
  registerParserPluginUI({
    id: "mineru",
    i18nNamespace: "parser_mineru",
    locales: {
      "zh-CN": zhCN,
      "en-US": enUS,
    },
  });
}
