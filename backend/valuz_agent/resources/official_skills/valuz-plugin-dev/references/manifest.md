# 清单 `valuz-plugin.json`（manifestVersion 1）

权威来源是 SDK 包里的 `valuz-plugin.schema.json`（编辑器补全、`valuz-plugin validate`、后端与控制面共用同一份规则）。下面是字段与规则的摘要。

## 最小清单

```json
{
  "$schema": "./node_modules/@valuz/plugin-sdk/valuz-plugin.schema.json",
  "manifestVersion": 1,
  "id": "acme.dashboard",
  "version": "0.1.0",
  "name": { "en-US": "Acme Dashboard", "zh-CN": "Acme 看板" },
  "description": { "en-US": "Shows Acme data in the project page", "zh-CN": "在项目页展示 Acme 数据" },
  "publisher": { "name": "Acme" },
  "engines": { "valuz-plugin-api": "^1.0.0" },
  "frontend": { "entry": "frontend/index.js", "styles": ["frontend/index.css"] },
  "permissions": ["projects:read", "storage"]
}
```

## 必填

| 字段 | 规则 |
|---|---|
| `manifestVersion` | 固定 `1` |
| `id` | `<发布者>.<名字>`：小写字母 / 数字 / 连字符，恰好一个 `.`，≤100 字符；不能以保留前缀开头（`oss-`、`commercial.`、`commercial-`、`finance.`、`finance-`、`team.`、`team-`、`edition.`、`valuz.`） |
| `version` | SemVer 2.0（`1.2.3`、`1.0.0-beta.1`）；更新要比已装版本高 |
| `name` | 字符串，或 `{ "en-US": "…", "zh-CN": "…" }`（语言键形如 `en-US` / `zh`），每条 ≤500 字符 |
| `publisher` | `{ "name": 必填, "url"?, "email"? }` |
| `engines` | `{ "valuz-plugin-api": "^1.0.0" }`（npm 风格 SemVer 范围；当前插件 API 版本 `1.0.0`） |
| `frontend` | `{ "entry": 相对路径, "styles"?: [相对路径…] }`；`entry` 必须是 ES 模块且存在于包内 |

## 可选

| 字段 | 说明 |
|---|---|
| `description` | 同 `name` 的写法 |
| `permissions` | 权限数组（不能重复）：`projects:read` `artifacts:read` `knowledge:read` `conversations:read` `conversations:write` `connectors:read` `connectors:call` `connectors:write` `automations:run` `storage` `notifications`。安装与更新时展示给用户；更新新增权限会单独提示 |
| `requires` | 依赖的环境，数组，写法 `edition:<id>` / `deployment:local` / `deployment:cloud` / `connector:<slug>` / `capability:<name>`。`connector:` 不满足时可安装但不加载（状态 `requires-unmet`），添加连接器后自动加载；其余不满足时安装被拒 |
| `config` | JSON Schema（`type: "object"`），宿主据此在「插件」页生成设置表单并校验；插件用 `ctx.config` / `usePluginConfig()` 读 |
| `automations` | 随插件声明的代码自动化，≤20 个，见下 |
| `locales` | 文案目录，相对路径，默认 `locales`（里面 `en-US.json`、`zh-CN.json`…） |
| `icon` | 图标相对路径 |
| `homepage` `repository` `license` `keywords` | 展示信息；`keywords` ≤20 个、每个 ≤40 字符 |
| `backend` | **保留**：插件 API 1.x 不支持，写了就是校验错误 |

`additionalProperties` 为 false：清单里不认识的字段会被校验拒绝。

## 其它规则（JSON Schema 表达不了的，所有校验方都会检查）

- 每个相对路径（`frontend.entry`、`frontend.styles[]`、`automations[].entry`、`icon`、`locales`）不能含 `..`，不能出包；不能是符号链接、绝对路径。
- `frontend.entry` 和每个 `frontend.styles[]` 必须真实存在。
- `automations[].name` 在清单内唯一。
- `config`、`automations[].input` 若有，必须是 `type: "object"` 的 JSON Schema。

## `automations`（随插件声明的代码自动化）

```json
"automations": [
  {
    "name": "risk-summary",
    "title": { "en-US": "Risk summary", "zh-CN": "风险汇总" },
    "runtime": "python",
    "entry": "automations/risk_summary.py",
    "input": { "type": "object", "properties": { "portfolioId": { "type": "string" } }, "required": ["portfolioId"] },
    "result": "artifact",
    "timeoutSec": 300,
    "trigger": { "cron": "0 8 * * 1-5", "timezone": "Asia/Shanghai" }
  }
]
```

- `name`：`^[a-z0-9][a-z0-9-]{0,49}$`；`runtime`：`python` | `shell`；`entry`：包内相对路径。
- `result`：`artifact`（每次 run 产出一个 JSON 对象，默认）| `conversation`。`timeoutSec`：1–3600。
- `trigger`：`"manual"`（只由插件按名字触发）| `{ "cron": "…", "timezone"?: "…" }` | `{ "intervalSec": ≥60 }`。
- 安装时 Valuz 以当前用户的身份创建这些自动化，标记"由插件 <id> 创建"，卸载时删除。插件用 `ctx.valuz.automations.run(name, input)` 按名字触发（需要 `automations:run`）。
- `python` 用随包的 `valuz-python`，依赖是固定的；脚本的 `run(ctx)` 契约、输入输出写法见技能 `automation` 的 `references/python-runtime.md`。
- 先想清楚是不是真要自动化：只是把已有数据展示出来，用 `ctx.valuz` 的读取接口就够了。

## 包结构

```
acme.dashboard/
├── valuz-plugin.json
├── frontend/index.js          ← 构建产物（ESM，默认导出 definePlugin(...)）
├── frontend/index.css         ← 可选
├── locales/en-US.json         ← 可选；zh-CN.json…
└── automations/risk_summary.py ← 可选
```

zip 内不允许符号链接、`..`、绝对路径，有大小与文件数上限；`valuz-plugin pack` 生成合规的 zip。
