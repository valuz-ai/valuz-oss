# 运行、读取与验证

## 1. run 的状态

| 状态 | 终态 | 含义 |
|---|---|---|
| `queued` | 否 | 已入队，等串行 worker 领取 |
| `running` | 否 | 会话回合 / 程序正在跑 |
| `success` | 是 | **唯一算成功的状态**：agent 回合正常结束（artifact 结果已 `output`）/ 程序退出 0 且产出校验通过 / task 已 kick off |
| `failed` | 是 | 有 `error_code` + `error_message`（见各 reference 的错误表） |
| `timeout` | 是 | 程序超过 `timeout_seconds` 被杀（`AUTOMATION_CODE_TIMEOUT`）。**不是** `failed` |
| `cancelled` | 是 | 被 `cancel`（`AUTOMATION_CANCELLED`）。**不是** `failed` |
| `skipped` | 是 | 应用离线时错过的排程点（`AUTOMATION_MISSED_WHILE_OFFLINE`，`trigger_type=recovered_skip`） |
| `interrupted_by_shutdown` | 是 | 进程重启时正在跑，被判为中断 |

同一条自动化**单飞**：最近一条 run 还是 `queued` / `running` 时，`run` 被拒（`AutomationAlreadyQueued` / `AutomationAlreadyRunning`），
排程到点也不会叠加。

## 2. `run`

```json
{ "action": "run", "automation_id": "…", "input": { "symbol": "600519" }, "wait_seconds": 60 }
```

- `input` 按 `input_contract`：text → 字符串，json → 对象（与 `default` 浅合并后校验），none → 不要传。
  不合规 → `ok=false`，`error_code="AutomationInputInvalid"`，消息带路径；**修正后重试**，别原样重发。
- 暂停中的自动化也能 `run`（暂停只停排程）。
- **不带 `wait_seconds`**：立即返回 `Queued automation for immediate execution (run_id=…)`——`run_id` 只在 `message` 里，
  `run` 字段为空；随后用 `runs` / `read_run` 跟进。
- **带 `wait_seconds`（1–60）**：阻塞到终态或超时，返回 `run` 详情。`ok` 在 `success` **或仍在 `queued` / `running`** 时都是 true，
  所以要看 `run.status`，不要只看 `ok`；未完成时 `message` 是 `Run … is still running after 60s; poll it with action='read_run'`。
  `error_code` = run 的 `error_code`（非 success 时）。
- 程序可能跑几分钟：`wait_seconds` 到顶就改用 **`read_run` 带 `wait_seconds: 60`** 连续调——服务端每半秒查一次、
  到终态立即返回，所以调用之间**不要 sleep**；**不要再发 `run`**。

## 3. `runs` 与 `read_run`

`runs`（`limit` 默认 10，最大 50）返回 `runs[]`，新的在前，每项：

`run_id` `status` `trigger_type`（`cron` / `interval` / `manual` / `agent` / `api` / `event` / `recovered_skip`）`triggered_at` `started_at`
`completed_at` `duration_ms`（epoch 毫秒）`result_summary` `error_code` `error_message` `session_id`（agent run 的会话）
`executor_ref`（code run 在哪跑：`local:<pid>` / `sandbox:<instance>`）`has_artifact` `has_input` `task_id` / `task_title` / `task_status`（task 模式）。

`read_run`（`automation_id` + `run_id`）在此之上带内容：

| 字段 | 含义 |
|---|---|
| `input` | 本次有效输入（json → 对象，text → 字符串，none → null） |
| `artifact` | 产出对象（artifact 结果且成功时） |
| `files` | `[{artifact_id, name, mime_type, size_bytes}]`，程序 `files` 或 `output` 的 `files` 登记成的产物行 |
| `log_tail` | code run 的 stdout / stderr 尾部 64 KiB（stderr 段以 `--- stderr ---` 开头）；agent run 为 null |
| `error_message` | 失败原因；契约类失败带路径（`artifact.rows: …` / `files.0.sourcePath: …`） |
| `cancel_requested_at` | 已请求取消的时刻（正在跑的程序在被杀前会短暂处于这个状态） |

## 4. 验证口径

- 报「自动化可用」之前：至少一次 **`run` 到 `success`**，且检查了 `artifact`（结构合乎 `result.schema`）与 `files`（数量、名字）。
- 对 cron / interval 触发，确认过一次**真由排程触发**的 run（`trigger_type` 是 `cron` / `interval`）再说排程通了；
  手动 `run` 只证明程序 / 提示词没问题。
- 失败证据的读法：
  - code run：`error_code` 定性（`AUTOMATION_CODE_EXIT` → 看 `log_tail` 里的 traceback / stderr；`AUTOMATION_OUTPUT_INVALID` → 包装对象形状；
    `AUTOMATION_ARTIFACT_INVALID` → schema 或 files 限制；`AUTOMATION_CODE_ENTRY_MISSING` → `entry` 路径；
    `AUTOMATION_RUNTIME_UNAVAILABLE` / `AUTOMATION_CODE_EXECUTOR_UNAVAILABLE` → 环境，不是代码）。
  - agent run：`AUTOMATION_NO_ARTIFACT` → 提示词没让它调 `output`，或 schema 让它反复失败；`SessionError` → 模型 / 凭据 / SDK；
    需要细节就用 `session_id` 去看会话。
- `AutomationAlreadyRunning` / `AutomationAlreadyQueued`：先 `runs` 看活跃的那条（`status`、`started_at`、`executor_ref`），
  判断是在正常跑还是卡住，再决定等、`cancel` 还是报告。
- `AutomationInputInvalid` / `AutomationArtifactInvalid` 的消息里有出错路径：修那个字段，不要换个方式盲试。
- 用 `get` 看单条自动化的 `status`（`enabled` / `paused`）、`next_run_at`、`last_run_status`、`total_runs`、`recent_failures` 做快速体检。

## 5. `cancel`

```json
{ "action": "cancel", "automation_id": "…", "run_id": "…" }
```

| run 当前状态 | 结果 |
|---|---|
| `queued` | 立即 `cancelled`（`AUTOMATION_CANCELLED`），runner 不会再领取 |
| `running` 且是 code 自动化 | 写入 `cancel_requested_at`，runner 每 2 秒轮询、杀整个进程组，run 变 `cancelled`；返回 `Run … asked to stop (status running)`，用 `read_run` 确认 |
| `running` 且是 agent 自动化 | `AutomationCancelUnsupported`：agent 回合是一个会话，去停那个会话（`session_id`），不在这里取消 |
| 已是终态 | `AutomationRunNotActive` |

取消的 run **不**产出 artifact、不登记文件；`previous`（下次 code run 的 `ctx.previous`）只取最近一次 **`success` 且带 artifact** 的 run，取消 / 失败不会污染它。

## 6. 修改与删除的注意

- 改触发器、`execution.entry`、`input_contract`、`result.schema` 之前，如果有 run 正在跑，先等它到终态（或 `cancel`），避免半途换契约。
- `update` 只改传了的字段；`execution` / `input_contract` / `result` 不传就保持原样。切换成 `code` 时 `agent_slug` 会被清空。
- `remove` 直接删除该自动化及其 run 记录，不可恢复；删之前用 `get` 确认名字，并告知用户。
- 项目会话只能操作本项目的自动化（`CROSS_PROJECT_DENIED`）；要动别的项目的，去那个项目的会话或临时对话。
