import { describe, expect, it } from "vitest";

import {
  getSnapshot,
  initI18n,
  registerLocaleNamespace,
  subscribe,
  t,
} from "./index";

describe("i18n project messages", () => {
  it("keeps project creation and project import messages separate", () => {
    initI18n({ locale: "en-US", fallbackLocale: "en-US" });

    expect(t("project.created", { name: "Demo" })).toBe(
      'Project "Demo" created',
    );
    const importMessage = t("project.importCreated", {
      members: 2,
      automations: 1,
      agents: 3,
    });
    expect(importMessage).toContain("Imported");
    expect(importMessage).toContain("2 member(s)");
  });

  it("does not notify subscribers when runtime locale data is unchanged", () => {
    initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
    let notifications = 0;
    const unsubscribe = subscribe(() => {
      notifications += 1;
    });

    registerLocaleNamespace("qa-idempotent", "zh-CN", { label: "设备" });
    const firstSnapshot = getSnapshot();
    expect(notifications).toBe(1);
    expect(firstSnapshot.translations["qa-idempotent.label"]).toBe("设备");

    registerLocaleNamespace("qa-idempotent", "zh-CN", { label: "设备" });
    registerLocaleNamespace("qa-idempotent", "en-US", { label: "Device" });
    expect(notifications).toBe(1);
    expect(getSnapshot()).toBe(firstSnapshot);

    registerLocaleNamespace("qa-idempotent", "zh-CN", { label: "远程设备" });
    expect(notifications).toBe(2);
    expect(getSnapshot().translations["qa-idempotent.label"]).toBe("远程设备");
    unsubscribe();
  });
});
