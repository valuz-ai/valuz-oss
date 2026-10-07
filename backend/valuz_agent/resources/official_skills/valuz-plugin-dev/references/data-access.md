# 数据能力发现与接入

用户描述业务需求即可；数据源、SDK 方法、工具参数和权限由 Agent 自己查。本文件说明发现流程，[sdk-api.md](sdk-api.md) §5 是已实现 SDK 接口与权限的清单。

## 1. 在开发会话里发现数据能力

1. 先从 `sdk-api.md` §5 判断是否已有项目、产物、知识库、会话或自动化接口可用。
2. 行情、搜索及外部系统数据优先检查**当前账号已连接的 MCP**。使用当前会话提供的连接器发现 / 工具查询能力；若工具延迟加载，先按能力搜索再读工具说明。查看实际工具名、输入 schema、描述与只读标记，再做符合用户需求的只读调用，验证返回数据。
3. 开发会话的 MCP 工具用于发现和验证；插件运行时的代码必须使用 `ctx.valuz` / `useValuz()`。不要把会话工具调用名、会话临时凭据或内部接口直接写进插件。
4. 如果当前会话无法发现连接器，只能说明“尚未验证数据源”。让插件通过 SDK 做发现或报告具体缺少的能力，不能据此断言产品不支持。只有确认缺少连接器、授权或相应工具后，才让用户处理这一项。

内置与用户添加的 MCP 连接器使用同一通用 SDK。例如，部署已提供并授权 `valuz-data` / `valuz-search` 时可以复用，无需再添加一份。不同账号与部署的可用连接器、工具和数据覆盖不同，不能只凭名称承诺可用。

## 2. 在插件里使用已实现接口

| SDK 方法 | 返回 / 用途 | 清单权限 |
|---|---|---|
| `valuz.connectors.list()` | `{ id, slug, name, description, enabled, status, toolCount }[]`；发现当前账号的连接器 | `connectors:read` |
| `valuz.connectors.listTools(idOrSlug)` | `{ name, description, inputSchema, readOnly }[]`；查工具及参数 | `connectors:read` |
| `valuz.connectors.callTool(idOrSlug, toolName, args)` | `{ content, structuredContent, isError }`；调用明确声明只读的工具 | `connectors:call` |
| `valuz.connectors.callTool(idOrSlug, toolName, args, { write: true })` | 有副作用的调用，宿主先请求用户确认 | `connectors:call` + `connectors:write` |

`apply(ctx)` 中用 `ctx.valuz`，React 组件中用 `useValuz()`。连接器参数接受实际 id 或 slug；优先保留可移植的 slug，不把当前账号的 id 写死。

- 写数据代码前检查工具 schema，不猜工具名、证券代码格式、参数或返回字段。最新价格、买卖盘口、历史价格可能是不同工具，应按描述选择。
- 只读调用要求工具显式声明 `readOnlyHint: true`（SDK 映射为 `readOnly`）。未标只读时，不把它当作普通查询，也不为了绕过校验自动启用写权限。
- 读取工具结果时检查 `isError`，优先解析实际返回的 `structuredContent`；只有返回的是文本 JSON 时才按其实际格式解析 `content`。记录样本、来源和时间后再实现字段映射。
- 用到 `list` / `listTools` / `callTool` 的读取插件通常需要清单 `permissions: ["connectors:read", "connectors:call"]`。声明权限不等于用户已授权；已有插件扩大权限时走正常安装确认流程，不能绕过。
- 通过宿主处理鉴权；不读取令牌、不直接 `fetch` 后端、不在插件配置中保存连接器密钥。

## 3. 行情插件的判断示例

“显示全球大盘行情”属于数据需求，不能因为 SDK 没有 Finance 专属 API 就拒绝。应先查已有行情连接器及工具 schema，再验证候选指数的真实返回。覆盖不完整时明确哪些指数无数据；真实返回缺失时显示暂无数据或错误，不能用固定示例数冒充真实行情。

区分接口支持、账号授权、工具可用和数据覆盖：

- `plugin_permission_denied` / 403：检查清单和授权，不等于 SDK 未实现。
- 连接器缺失、禁用或未授权：说明具体前提，再引导配置。
- 工具不存在、输入不匹配或 `isError`：回到工具 schema 与错误信息排查。
- 工具正常但某指数无数据：如实呈现数据覆盖限制。

若用户明确要求示例数据可以使用，但界面和汇报都要标为示例，不能把该实现选择描述为产品接不了真实数据。
