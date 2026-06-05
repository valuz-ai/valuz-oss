import { registerParserPluginUI } from "@valuz/core";

import zhCN from "./locale.zh-CN.json";
import enUS from "./locale.en-US.json";

export function register(): void {
  registerParserPluginUI({
    id: "valuz_ocr",
    i18nNamespace: "parser_valuz_ocr",
    locales: {
      "zh-CN": zhCN,
      "en-US": enUS,
    },
  });
}
