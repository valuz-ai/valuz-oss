# 三个可抄的完整例子

假设插件 id 是 `acme.dashboard`，清单已按 `manifest.md` 写好。每个例子给出：权限、`src/index.tsx`、`locales`、测试。构建与测试的命令见 `workflow.md`。

## 1. 项目页标签页：读项目数据 + 本地存储

清单：`"permissions": ["projects:read", "storage"]`

```tsx
// src/index.tsx
import { useEffect, useState } from "react";
import { definePlugin, host, useTranslation, useValuz } from "@valuz/plugin-sdk";
import type { ProjectDetail, SlotProps } from "@valuz/plugin-sdk";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Spinner } from "@valuz/plugin-sdk/ui";

function DashboardTab({ projectId, navigate }: SlotProps<"project.detail.tabs">) {
  const { t } = useTranslation();
  const valuz = useValuz();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [visits, setVisits] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const detail = await valuz.projects.get(projectId);
        const seen = (await valuz.storage.get<number>(`visits:${projectId}`)) ?? 0;
        await valuz.storage.set(`visits:${projectId}`, seen + 1);
        if (!cancelled) {
          setProject(detail);
          setVisits(seen + 1);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, valuz]);

  if (error) return <p role="alert">{t("error", { message: error })}</p>;
  if (!project) return <Spinner />;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{project.name}</CardTitle>
      </CardHeader>
      <CardContent>
        <Badge>{t("visits", { count: visits })}</Badge>
        <Button onClick={() => navigate("/x/acme.dashboard/overview")}>{t("openOverview")}</Button>
        <Button variant="outline" onClick={() => host.toast({ title: t("saved"), variant: "success" })}>
          {t("save")}
        </Button>
      </CardContent>
    </Card>
  );
}

export default definePlugin({
  id: "acme.dashboard",
  apply(ctx) {
    ctx.registry.slot("project.detail.tabs", {
      id: "dashboard",
      key: "dashboard",
      label: "tab.title",
      component: DashboardTab,
    });
    ctx.log.info("acme.dashboard loaded");
  },
});
```

```json
// locales/en-US.json
{ "tab": { "title": "Dashboard" }, "visits": "Visited {{count}} times", "openOverview": "Overview",
  "save": "Save", "saved": "Saved", "error": "Could not load: {{message}}" }
// locales/zh-CN.json
{ "tab": { "title": "看板" }, "visits": "已访问 {{count}} 次", "openOverview": "总览",
  "save": "保存", "saved": "已保存", "error": "加载失败：{{message}}" }
```

测试：

```tsx
// src/index.test.tsx
import { describe, expect, it } from "vitest";
import { createTestHost, mockValuz } from "@valuz/plugin-sdk/testing";
import plugin from "./index";

describe("acme.dashboard", () => {
  it("registers the project tab and renders it", async () => {
    const valuz = mockValuz({ projects: [{ id: "p1", name: "Alpha", kind: "project", rootPath: null, icon: null }] });
    const host = createTestHost({
      valuz,
      locales: { "en-US": { tab: { title: "Dashboard" }, visits: "Visited {{count}} times" } },
    });
    await host.load(plugin);
    expect(host.capture().slots["project.detail.tabs"]).toContain("x:acme.dashboard:dashboard");
    // The component loads data asynchronously: use @testing-library/react with getSlotComponents for that.
    expect(host.renderSlot("project.detail.tabs", { projectId: "p1", navigate: () => {} })).toBeTypeOf("string");
  });
});
```

## 2. 连接器数据 + 页面：只读工具与写工具

先让用户在 Valuz 里**添加好 MCP 连接器**（slug 例如 `acme-data`）。清单：

```json
"permissions": ["connectors:read", "connectors:call"],
"requires": ["connector:acme-data"]
```

（调写工具再加 `connectors:write`，并且每次宿主都会先让用户确认。）

```tsx
import { useEffect, useState } from "react";
import { definePlugin, pageRoute, useTranslation, useValuz } from "@valuz/plugin-sdk";
import { EmptyState, LoadingState, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@valuz/plugin-sdk/ui";

function Positions() {
  const { t } = useTranslation();
  const valuz = useValuz();
  const [rows, setRows] = useState<Array<{ symbol: string; qty: number }> | null>(null);

  useEffect(() => {
    valuz.connectors
      .callTool("acme-data", "get_positions", {})            // 只读工具：connectors:call 就够
      .then((res) => setRows(((res.structuredContent as { positions?: Array<{ symbol: string; qty: number }> })?.positions) ?? []))
      .catch(() => setRows([]));
  }, [valuz]);

  if (rows === null) return <LoadingState />;
  if (rows.length === 0) return <EmptyState title={t("empty")} />;
  return (
    <Table>
      <TableHeader><TableRow><TableHead>{t("symbol")}</TableHead><TableHead>{t("qty")}</TableHead></TableRow></TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.symbol}><TableCell>{r.symbol}</TableCell><TableCell>{r.qty}</TableCell></TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default definePlugin({
  id: "acme.dashboard",
  apply(ctx) {
    pageRoute(ctx, { id: "positions", path: "/x/acme.dashboard/positions", title: "page.positions", icon: "star" }, Positions, { nav: true });
  },
});
```

调用前可以先看工具是不是只读：`const tools = await valuz.connectors.listTools("acme-data"); tools.find(t => t.name === "x")?.readOnly`。非只读工具不带 `{ write: true }` 会被后端拒绝（403 `write_tool_requires_confirmation`）。

测试用 `mockValuz({ connectors: { "acme-data": { get_positions: () => ({ positions: [{ symbol: "AAPL", qty: 10 }] }) } }, writeTools: ["place_order"] })`；`host.valuz.calls` 里能断言调了哪些方法。

## 3. 设置分区 + 配置 + 自动化 + 通知

清单：

```json
"permissions": ["automations:run", "notifications", "storage"],
"config": {
  "type": "object",
  "properties": { "portfolioId": { "type": "string", "title": "Portfolio id" } },
  "required": ["portfolioId"]
},
"automations": [
  { "name": "risk-summary", "runtime": "python", "entry": "automations/risk_summary.py",
    "input": { "type": "object", "properties": { "portfolioId": { "type": "string" } }, "required": ["portfolioId"] },
    "result": "artifact", "timeoutSec": 300, "trigger": "manual" }
]
```

```tsx
import { useState } from "react";
import { definePlugin, host, settingsPage, usePluginConfig, useTranslation, useValuz } from "@valuz/plugin-sdk";
import { Button } from "@valuz/plugin-sdk/ui";

function RiskPanel() {
  const { t } = useTranslation();
  const valuz = useValuz();
  const config = usePluginConfig() as { portfolioId?: string };
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      const { runId } = await valuz.automations.run("risk-summary", { portfolioId: config.portfolioId });
      const result = await valuz.automations.waitRun(runId, { timeoutMs: 120_000 });
      if (result.status === "succeeded") {
        await valuz.notifications.post({ title: t("done"), body: JSON.stringify(result.output).slice(0, 200), link: "/x/acme.dashboard/risk" });
      } else {
        host.toast({ title: t("failed"), description: result.errorMessage ?? undefined, variant: "error" });
      }
    } finally {
      setBusy(false);
    }
  }
  return <Button disabled={busy || !config.portfolioId} onClick={run}>{t("run")}</Button>;
}

export default definePlugin({
  id: "acme.dashboard",
  apply(ctx) {
    settingsPage(ctx, { id: "risk", title: "settings.risk" }, RiskPanel);
  },
});
```

自动化脚本 `automations/risk_summary.py` 的 `run(ctx)` 契约（输入怎么读、产出怎么交）见技能 `automation` 的 `references/python-runtime.md`。
测试：`mockValuz({ automations: { "risk-summary": () => ({ status: "succeeded", output: { var95: 1.2 } }) } })`，断言 `valuz.notifications` 里有一条。
