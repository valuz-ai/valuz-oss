---
name: valuz-plugin-dev
description: 在会话里从零开发一个Valuz 应用插件（给界面加页面、标签页、按钮、工具卡片、设置分区，读项目 / 产物 / 知识库 / 连接器的数据，跑代码自动化），构建、测试、装进用户本机的 Valuz 并排错，最后打包发布。Use when the user wants to build, extend, debug, install or publish a Valuz App Plugin — e.g. "在项目页加一个显示 XX 数据的标签页", "给会话标题栏加个导出按钮", "做一个插件把连接器的数据展示出来", "把这个插件发布给团队".
version: 2
tags: [official, plugin]
---

# Valuz 应用插件开发

你要为用户写一个**Valuz 应用插件**：一个 zip 包，里面是清单 `valuz-plugin.json` + 构建好的前端 `frontend/index.js`（ESM）+ 可选的样式、文案、自动化脚本。
插件跑在用户本机的 Valuz 界面里，只能通过公开的扩展面做事：**新增**页面 / 插槽内容 / 设置分区 / 侧栏入口，用 `ctx.valuz` 调 Valuz 自带的后端能力。
它**不能**删除或替换第一方界面，没有自己的后端进程（插件 API 1.x 不支持 `backend`）。

应用插件（App Plugin）扩展 Valuz 的界面与功能；智能体插件（Agent Plugin）打包技能和 MCP 连接器，由 `valuz plugin agent …` 管理。官方 / 第三方是来源维度，与插件类型独立。

三件工具，缺一不可，各管一段：

| 工具 | 作用 |
|---|---|
| 会话命令 `valuz-plugin` | 在工作区里 `create` / `build` / `test` / `validate` / `pack`（离线可用，SDK 与模板随应用分发） |
| 工具 `app_plugin_manager` | `validate` / `pack` / `status` / `logs` / `reload`，以及要用户确认的 `dev_link` / `install` / `uninstall` / `publish` |
| 本技能 | 清单规则、SDK 全部接口、12 个公开插槽、验证与排错流程 |

按需读 `references/`：`manifest.md`（清单字段与规则）· `sdk-api.md`（SDK 全部接口与权限）· `slots.md`（12 个公开插槽的位置与 props）· `examples.md`（三个可抄的完整例子）· `workflow.md`（命令、确认卡片、调试、发布的细节）· [`data-access.md`](references/data-access.md)（数据能力发现、现有连接器复用、真实数据验证）。

## 0. 先找已有能力

用户只需描述想做的功能，不需要知道 SDK 方法、权限名或 MCP 工具名。涉及数据时，先读 [`references/sdk-api.md`](references/sdk-api.md) §5 的**已实现接口表**，再按 [`references/data-access.md`](references/data-access.md) 自己发现当前账号的数据能力。不要把未检查、缺权限或未接入说成 SDK 不支持。

| 用户想做什么 | 已有 SDK 能力 | 去哪里查 |
|---|---|---|
| 读项目、文件、产物、知识库 | `projects`、`artifacts`、`knowledge` | `references/sdk-api.md` §5 |
| 展示行情或外部业务数据 | `connectors.list` / `listTools` / `callTool` | `references/data-access.md`；参数与权限见 `references/sdk-api.md` §5 |
| 读会话、准备或发送消息 | `conversations` | `references/sdk-api.md` §5 |
| 执行后台计算、读运行结果 | `automations` | `references/sdk-api.md` §5；清单见 `references/manifest.md` |
| 保存插件数据、发通知 | `storage`、`notifications` | `references/sdk-api.md` §5 |

当前账号已经安装并授权的连接器可以直接复用；内置连接器与用户添加的连接器走同一 SDK。**不开放 edition 专属 API，不代表不能经 MCP 连接器读取行情等数据。** 只有实际检查后确认缺少连接器或授权，才引导用户配置。

## 1. 先问清楚再动手

动手前用一两句话确认（缺哪个问哪个，别一次问一堆）：

1. **放在哪**：项目页标签页（`project.detail.tabs`）？会话标题栏按钮？某个工具的结果卡片（`conversation.tool-card.<tool>`）？独立页面（`pageRoute`）？设置分区？见 `references/slots.md`。
2. **数据从哪来**：先按 `references/data-access.md` 检查 Valuz 的已有能力和当前连接器，复用可用数据源。需要用户选择的是业务范围或数据来源偏好，不是接口定义。确实没有匹配能力时再询问数据来源；外部系统通过 MCP 连接器接入，插件用 `ctx.valuz.connectors.callTool` 展示，不直接写网络请求。后端计算用清单里声明的**代码自动化**（`automations`）。
3. **插件 id**：`<发布者>.<名字>`，小写字母 / 数字 / 连字符，恰好一个 `.`，如 `acme.dashboard`。不能以 `oss-`、`commercial.`、`commercial-`、`finance.`、`finance-`、`team.`、`team-`、`edition.`、`valuz.` 开头。
4. **要哪些权限**：只声明真正用到的（见 `references/sdk-api.md` 的权限表）。多声明一个权限，用户安装时就多看到一行。

## 2. 流程（每一步都要做）

1. **创建**：`valuz-plugin create <目录> --id <发布者.名字>`（目录建在会话工作区内）。先 `ls` 看生成了什么、读生成目录里的 `AGENTS.md`。若提示找不到 `valuz-plugin` 命令，如实告诉用户当前环境没有插件 SDK，不要手写一个替代品。
2. **写代码**：`definePlugin({ id, apply(ctx) { … } })`，id 必须与清单一致。所有用户可见的文字走 `locales/<lang>.json` + `useTranslation()`，至少 `en-US` 和 `zh-CN`。样式用设计令牌（`@valuz/plugin-sdk/ui` 的 `tokens`）和 UI 组件，不要写死颜色。
3. **构建**：`valuz-plugin build`。产物是清单 `frontend.entry`（默认 `frontend/index.js`）。
4. **测试**：`valuz-plugin test`。至少断言：插件加载成功、注册了你要的插槽 / 页面、关键组件能渲染（`createTestHost` + `mockValuz`，见 `references/examples.md`）。**测试没过不要往下走。**
5. **校验**：`valuz-plugin validate`（或 `app_plugin_manager validate`）。errors 必须为 0；warnings 读一遍，能修就修。
6. **装进 Valuz**（需要用户确认，**你不能绕过卡片**）：
   - 开发中用 `app_plugin_manager dev_link {path}`：弹出确认卡片（插件 id、目录、权限、sha256…）。**调用一次后停下等用户点确认**，不要重复调用，不要用 shell 执行 `valuz plugin app install`。`path` 必须在当前会话工作区内。确认之后，这个目录以后每次重新构建都会自动重载，**不再弹卡片**。
   - 要固定版本时：`app_plugin_manager pack {path}` 得到 zip，再 `app_plugin_manager install {source_path}`（同样是卡片，对这个固定的 zip 确认一次）。
   - 安装类动作只在本地会话（桌面端）可用；云端会话里只能写、校验、打包、发布。
7. **看结果**：用户确认后 `app_plugin_manager status {id}` 看状态（`enabled` / `broken` / `incompatible` / `requires-unmet` / `blocked`）和原因，`app_plugin_manager logs {id}` 看加载与运行日志。**你看不到界面**：界面好不好看、位置对不对由用户判断，请他描述或截图；你负责读日志排错。
8. **迭代**：改代码 → `valuz-plugin build` →（界面没变就）`app_plugin_manager reload {id}` → 再看 `logs`。
9. **分享**（用户要时才做）：`valuz-plugin pack` → `app_plugin_manager publish {path 或 id, scope}`。`scope`：`personal`（只有本人）/ `org`（本组织，需审核）/ `global`（选发行版，需平台审核）。同样是卡片，需要登录 Valuz 账号；用 `app_plugin_manager submissions` 查审核进度。

## 3. 铁律

- **只调用清单里声明了权限的接口。** 代码里用了 `ctx.valuz.knowledge.search` 就必须声明 `knowledge:read`。不要为了省事声明没用的权限。
- **不绕开 `ctx.valuz`**：不要在插件里直接 `fetch("/v1/…")`、不要读取用户的登录令牌、不要访问别的用户的数据。
- **只 import 宿主共享的模块**：`react`、`react/jsx-runtime`、`react-dom`、`react-dom/client`、`@valuz/plugin-sdk`、`@valuz/plugin-sdk/ui`。不能 import `@valuz/core`、`@valuz/app`、`@valuz/ui` 等内部包；其它依赖由构建打进产物。
- **只新增，不改第一方**：不删除、替换、重排第一方的页面、设置分区、侧栏项，不抑制宿主界面。条目 id 宿主自动加 `x:<插件 id>:` 前缀；路由必须在 `/x/<插件 id>/` 下；越界会抛错。
- **写操作要克制**：`connectors.callTool` 调非只读工具需要 `{ write: true }` 和 `connectors:write` 权限，宿主会让用户确认；`conversations.send` 会让 agent 立刻开始工作（消耗点数），默认优先用 `conversations.draft`（填好输入框，由用户点发送）。
- **用户可见文字不写死**：走 `t()`；清单的 `name` / `description` 用 `{ "en-US": "…", "zh-CN": "…" }`。
- **别改第一方代码来实现需求**；也别在工作区之外建插件目录。
- **只读到的东西如实汇报**：日志里没有错误不等于界面对了；没让用户看过之前不要说"已经好了"。

## 4. 常见失败

| 现象 | 处理 |
|---|---|
| `validate` 报 `id` 不合法 / 保留前缀 | 换 id，同步清单、`definePlugin({ id })`、目录名 |
| `validate` 报 `frontend.entry` 不存在 | 先 `valuz-plugin build`；检查清单里的相对路径（不能有 `..`） |
| `status` = `broken` | `logs` 看加载错误：多半是 import 了非共享模块、`apply` 里抛错、或注册越界（路由不在 `/x/<id>/` 下、用了未公开的插槽） |
| `status` = `incompatible` | 清单 `engines.valuz-plugin-api` 范围与宿主的插件 API（`1.0.0`）不符，改成 `^1.0.0` |
| `status` = `requires-unmet` | 清单 `requires` 里有未满足的项，如缺连接器 `connector:acme-data`：让用户先添加该连接器，满足后自动加载 |
| `status` = `blocked` | 组织策略禁止，告诉用户，不要试图绕过 |
| 日志里 `plugin_permission_denied` / 403 | 用了没声明的权限，补进清单 `permissions`、重新 build 与 `reload`（权限变化要用户再确认一次安装） |
| 改了代码界面没变 | 是否已 `valuz-plugin build`？再 `app_plugin_manager reload {id}`；还不行看 `logs` |
