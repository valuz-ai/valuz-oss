# `@valuz/plugin-sdk` 全部接口

运行时的 `@valuz/plugin-sdk` 由宿主提供（共享模块表），所以插件里 `import` 即可，构建预设会把它改写成取宿主那一份。npm 上的包只给类型、构建和测试用。
入口：`@valuz/plugin-sdk`（定义、注册、hooks、host、类型）· `@valuz/plugin-sdk/ui`（UI 组件与设计令牌）· `@valuz/plugin-sdk/testing`（测试宿主）· `@valuz/plugin-sdk/build`（构建预设）。

## 1. 插件定义

```ts
import { definePlugin } from "@valuz/plugin-sdk";

export default definePlugin({
  id: "acme.dashboard",           // 必须等于清单的 id
  apply(ctx) {
    // ctx.pluginId              插件 id
    // ctx.registry              界面注册（见 §2）
    // ctx.effect(setup)         持有资源；setup 返回的函数在卸载时执行
    // ctx.plugin(child)         挂子插件（生命周期随本插件）
    // ctx.valuz                 Valuz 内置后端能力（见 §5）
    // ctx.config                插件配置的当前值（清单 config 的值）
    // ctx.log                   写到「插件」页该插件的日志：debug/info/warn/error(message, ...details)
  },
});
```

`apply` 可以是 `async`。抛错 = 加载失败（状态 `broken`，已注册的内容会回滚）。
插件默认导出必须是 `definePlugin(...)` 的返回值。

## 2. 界面注册

| 方法 | 做什么 |
|---|---|
| `ctx.registry.slot(name, { id, component, priority?, key?, label? })` | 往公开插槽贡献组件。`id` 是本地 id（宿主加 `x:<插件 id>:` 前缀）；`priority` 小的先渲染；**keyed 插槽**（`context-panel.tabs`、`project.detail.tabs`）必须给 `key`，`label` 是标签标题（locales 的键或纯文本，默认插件名） |
| `pageRoute(ctx, { id, path, title, description?, icon? }, Component, { nav? })` | 新页面。`path` 必须是 `/x/<插件 id>` 或其子路径；`nav: true` 同时加侧栏入口；`title` 是 locales 的键或纯文本；`icon` 是侧栏图标 id，可用：assistant、knowledge、skills、scheduled、playbooks、activity、settings、agents、connectors、plugins、marketplace、projectTasks、star、compass、watchlist、portfolio、dashboard、globe（未知 id 显示齿轮，默认 plugins） |
| `settingsPage(ctx, { id, title, description?, icon? }, Component)` | 新设置分区，放在「插件」分组下 |
| `sidebarItem(ctx, { id, label, icon?, path })` | 单独加一个侧栏入口，`path` 必须指向插件自己的页面 |
| `slotComponent(Component)` | 把普通组件当插槽组件用（只是类型适配） |
| `ctx.registry.route / settingsSection / navItem` | 上面三个 helper 的底层方法 |

越界（路由不在 `/x/<id>/` 下、未公开的插槽、侧栏项指向别处）会抛错，插件加载失败。
插槽组件收到宿主传的 props（见 `slots.md`）；这些 props 里的 `session` / `project` / `turn` / `task` / `resource` 是**公开类型**，字段稳定：

```ts
PublicSession  { id; title: string|null; projectId: string|null; agentSlug: string|null; createdAt: number|null }
PublicProject  { id; name; kind: string|null; rootPath: string|null; icon: string|null }
PublicTurn     { id; userText; assistantText; createdAt: number|null; failed: boolean }
PublicTask     { id; projectId; title; goal; status; leadAgentSlug; createdAt; updatedAt }
PublicResource { type; id; name: string|null; description: string|null }
PublicToolCall { id; name; status; input; output }
```

## 3. React hooks（组件里用）

| hook | 返回 |
|---|---|
| `useTranslation()` | 插件自己的 `t(key, params?)`：读 `locales/<lang>.json`（嵌套对象会拍平成 `a.b.c`），顺序：宿主语言 → 同语言的其它变体 → `en-US` → key 本身；`{{name}}` 占位符从 `params` 取 |
| `useValuz()` | §5 的客户端，等同 `ctx.valuz` |
| `usePluginConfig()` | 插件配置的当前值，变化时组件重新渲染 |
| `useHostContext()` | `{ locale, theme: "light"\|"dark", edition, deployment: "local"\|"cloud", orgId: string\|null }` |

## 4. 宿主交互 `host`

```ts
import { host } from "@valuz/plugin-sdk";

host.navigate("/x/acme.dashboard/report");            // 跳 Valuz 内页面（含自己的页面）
host.openSession(sessionId);  host.openProject(projectId);
host.toast({ title: "已保存", description?: "…", variant?: "default" | "success" | "error" | "warning" });
const ok = await host.confirm({ title: "删除？", body?: "…", confirmLabel?: "删除" });   // Promise<boolean>
await host.openExternal("https://example.com");       // 只接受 http(s)
await host.copyText("text");
await host.draftConversation({ projectId?, agent?, text });   // 开新对话并填好输入框，不发送
```

## 5. `ctx.valuz`（`useValuz()`）— Valuz 内置后端能力

下面除明确标为“不支持”的 `service.fetch` 外，都是已实现的 SDK 接口，stable / experimental 表示稳定级别，不表示“设计中”。涉及数据发现与连接器选型时读 [data-access.md](data-access.md)。

宿主代发请求并处理鉴权；错误统一为 `ValuzApiError { status, code, message }`（`import { ValuzApiError } from "@valuz/plugin-sdk"`）。**每个后端能力对应清单里的一项权限**：

| 方法 | 做什么 | 权限 | 级别 |
|---|---|---|---|
| `projects.list()` / `projects.get(id)` | 项目列表 / 详情（多一个 `instructions`） | `projects:read` | stable |
| `projects.files(id, { path?, depth? })` | 项目文件树 | `projects:read` | stable |
| `artifacts.list({ projectId?, sessionId? })` / `artifacts.content(revisionId)` | 产物列表 / 某个版本的文本内容 | `artifacts:read` | stable |
| `knowledge.search(query, { projectId?, kbIds?, topK? })` / `knowledge.get(docId)` | 知识库检索（命中含 `snippet`）/ 文档元信息（如 `filename`、`status`） | `knowledge:read` | stable |
| `conversations.draft({ projectId?, agent?, text })` | 开新对话并填好输入框，用户自己点发送（不消耗点数） | 无 | stable |
| `conversations.send({ sessionId?, projectId?, agent?, text })` | 直接发一条消息让 agent 开始工作（会消耗点数） | `conversations:write` | experimental |
| `conversations.events(sessionId, { afterSeq? })` | 读会话事件（消息、工具调用） | `conversations:read` | stable |
| `connectors.list()` | 当前用户的连接器 `{ id, slug, name, description, enabled, status, toolCount }` | `connectors:read` | stable |
| `connectors.listTools(connector)` | 某连接器的工具 `{ name, description, inputSchema, readOnly }`；`connector` 是 id 或 slug | `connectors:read` | stable |
| `connectors.callTool(connector, tool, args?)` | 调**只读**工具（声明了 `readOnlyHint: true` 的）→ `{ content, structuredContent, isError }` | `connectors:call` | stable |
| `connectors.callTool(connector, tool, args, { write: true })` | 调有副作用的工具，宿主先让用户确认 | `connectors:call` + `connectors:write` | experimental |
| `automations.run(name, input?)` | 触发本插件声明的自动化 → `{ runId, automationId }` | `automations:run` | stable |
| `automations.getRun(runId)` / `waitRun(runId, { timeoutMs?, intervalMs? })` / `latestRun(name)` | 读运行状态 / 等到结束 / 最近一次运行（`AutomationRun`：`status`、`done`、`output`、`files`、`errorMessage`…） | `automations:run` | stable |
| `storage.get(key)` / `set(key, value)` / `delete(key)` / `list(prefix?)` | 插件自己的持久数据（按用户 × 插件隔离，JSON 值）；`get` 没有时返回 `null` | `storage` | stable |
| `notifications.post({ title, body?, link? })` | 在 Valuz 通知列表里发一条通知；`link` 是应用内路径（如 `/x/acme.dashboard/report`）时点击可跳转 | `notifications` | experimental |
| `service.fetch(path, init)` | B 级后端服务 | — | 插件 API 1.x 不支持，调用会抛错 |

- 存储限额：键 ≤200 字符，单个值 ≤256 KiB，每个插件每个用户合计 ≤10 MiB；超限抛 413。数据只存在这台设备的 `valuz.db`，不跨设备同步；卸载时默认保留。
- experimental 的接口可以在 minor 版本里调整；stable 的破坏性变更只走 major。
- 没声明权限就调用 → 后端回 403 `plugin_permission_denied`，并写进插件日志（`audit`）。
- 不暴露：账号 / 组织 / 计费 / 鉴权 / API key / 模型渠道与密钥、其他用户的数据、扩展管理本身；也没有 edition 专属 SDK 模块（如 Finance 的自选、组合 API）。**这是接口边界，不是数据种类禁令**：行情等数据可以通过当前账号已授权的 MCP 连接器读取。
- 当前账号已有的连接器（包括部署提供的内置连接器）无需重复添加。插件声明权限后，用 `connectors.list()` 找实际 id / slug，再用 `listTools()` 查工具 schema 与 `readOnly`，最后按真实 schema 调 `callTool()`。具体流程见 [data-access.md](data-access.md)。
- `knowledge.get` 返回文档元信息，不返回解析后的 Markdown 正文；知识检索的文本片段在 `KnowledgeHit.snippet` 中。`artifacts.content` 是产物版本的完整文本读取接口。
- `knowledge.search` 的 `kbIds` 使用顶层知识库 ID，对应 HTTP 字段 `knowledge_base_ids`。未提供时沿用项目绑定范围；显式 `[]` 返回零命中；指定 ID 时只检索当前用户本人或宿主已授权共享的相应知识库，并在每次请求重新检查授权与存在状态。

## 6. 配置、文案、样式

- **配置**：清单 `config`（JSON Schema）→ 宿主在「插件」页生成表单 → `ctx.config` / `usePluginConfig()`。只有用户级配置。别把密钥放配置默认值里。
- **文案**：`locales/en-US.json`、`locales/zh-CN.json` 等，嵌套对象即可（`{ "report": { "title": "…" } }` → `t("report.title")`）。清单的 `name` / `description`、`pageRoute.title`、`settingsPage.title`、`sidebarItem.label`、keyed 插槽的 `label` 都可以直接写 locales 的键。
- **样式**：优先用 `@valuz/plugin-sdk/ui` 的组件；自带 CSS 写在 `frontend.styles` 里，颜色 / 圆角 / 字号取设计令牌 CSS 变量（`var(--color-primary)` 等），构建预设会给自带 CSS 加插件根元素的作用域。

## 7. UI 组件 `@valuz/plugin-sdk/ui`

与宿主同一份实现、同一套令牌，自动跟随明暗主题：

`Button`（`buttonVariants`）· `Input` · `Textarea` · `Select`（`SelectTrigger/Content/Item/Value/Group/Label/Separator`）· `Checkbox` · `Switch` · `Tabs`（`TabsList/Trigger/Content`）· `Table`（`TableHeader/Body/Row/Head/Cell/Caption/Footer`）· `Card`（`CardHeader/Title/Description/Content/Footer/Action`）· `Badge`（`badgeVariants`）· `Dialog`（`DialogTrigger/Content/Header/Title/Description/Footer/Close`）· `Popover`（`PopoverTrigger/Content/Anchor`）· `Tooltip`（`TooltipProvider/Trigger/Content`）· `DropdownMenu`（`…Trigger/Content/Item/CheckboxItem/RadioGroup/RadioItem/Label/Separator/Shortcut/Group/Sub…`）· `Skeleton` · `Spinner` · `LoadingState` · `Empty`（`EmptyHeader/Media/Title/Description/Content`）· `EmptyState` · `ChartContainer`（`ChartTooltip/ChartTooltipContent/ChartLegend/ChartLegendContent/ChartStyle`，类型 `ChartConfig`）

设计令牌：`tokens`（如 `tokens.colorPrimary === "var(--color-primary)"`，可直接放进 `style`）与 `tokenVariables`。

```tsx
import { Button, Card, CardContent, CardHeader, CardTitle, Badge, Spinner, tokens } from "@valuz/plugin-sdk/ui";
```

## 8. 测试 `@valuz/plugin-sdk/testing`

```ts
import { createTestHost, mockValuz } from "@valuz/plugin-sdk/testing";

const host = createTestHost({
  valuz: mockValuz({ /* 见 examples.md */ }),
  config: { currency: "CNY" },
  locale: "zh-CN",
  locales: { "en-US": { title: "Dash" }, "zh-CN": { title: "看板" } },
  hostContext: { deployment: "local" },
  confirm: true,                         // host.confirm 的回答
});
await host.load(plugin);                 // 走与 Valuz 相同的受限 ctx；越界会抛 PluginContractError
host.capture();                          // { slots, routes, settings, nav }：注册了什么（id 带 x:<插件 id>: 前缀）
host.renderSlot("project.detail.tabs", { projectId: "p1", navigate: () => {} });   // 静态标记
host.getSlotComponents(name);            // 配合 @testing-library/react 渲染
host.renderRoute("/x/acme.dashboard/report");  host.renderSettings("settings-id");
host.calls;                              // { navigate, toast, confirm, openExternal, copyText, drafts }
host.setLocale / setConfig / setHostContext;  await host.unload();
```
