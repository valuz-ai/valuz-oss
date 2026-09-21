---
name: automation
description: 用 `automation` MCP 工具创建 / 修改 / 运行 / 检查 / 取消自动化：cron / interval / manual 触发，agent 执行或 code（python / shell 程序）执行，结构化输入（input_contract）与结构化产出（result artifact）。Use when the user asks to create, update, run, inspect or cancel an automation, schedule recurring work (daily / weekly / every N minutes), write a code automation, or needs structured input / output for a run.
version: 1
tags: [official, automation]
---

# Automation

`automation` 是创建和管理周期性工作的**唯一**入口——不要只用自然语言答复，也不要引导用户去界面手配。
细节按需读：`references/python-runtime.md`（`run(ctx)` 全契约）· `references/agent-execution.md`
（agent run 的开场框定与 `output`）· `references/verify.md`（状态、`read_run` 字段、取消语义）。

## 1. 一个工具，十二个动作

| `action` | 必填 | 说明 |
|---|---|---|
| `create` | `name`、`trigger`；agent 执行还要 `prompt_template`；项目会话还要 `agent_slug` | **只提议，不落库**：返回 `proposal`，用户在卡片上点「创建」才真正生成 |
| `get` / `list` | `automation_id` / — | 单条完整详情 / 列表（临时对话默认全库，`scope:"this"` 收窄；项目会话只看本项目） |
| `update` | `automation_id` + 要改的字段 | 可改 `name` `prompt_template` `trigger` `agent_slug` `action_kind` `worktree` `execution` `input_contract` `result` |
| `pause` / `resume` / `remove` | `automation_id` | 暂停只停排程，显式 `run` 仍可跑 |
| `run` | `automation_id`；可选 `input`、`wait_seconds`(0–60) | 立即跑一次；`input` 只作用于这一次，不改保存的自动化 |
| `runs` | `automation_id`；可选 `limit`(1–50，默认 10) | 最近的 run，新的在前 |
| `read_run` | `automation_id` + `run_id` | 一次 run 的 `input` / `artifact` / `files` / `log_tail` / `error_message` |
| `cancel` | `automation_id` + `run_id` | 停掉排队中的 run 或正在跑的程序 |
| `output` | `artifact`；可选 `files` | **只在自动化 run 的会话里**记录本次 run 的产出 |

返回值是 JSON：`{action, ok, message, error_code?, automation?, automations[], proposal?, run?, runs[], next_runs[]}`。
`ok=false` 时看 `error_code`（多为异常类名，如 `AutomationNotFound`、`InvalidCronExpression`）。

**`create` 的铁律**：调用一次 → 用自然语言复述拟定的时间安排（`message` 里有 `trigger_human_readable`）→ **停下等用户确认**。
不要再调 `create`，不要假定它已存在，不要在没有 `automation_id` 的情况下 `run` / `update`。
`agent_slug`：临时对话可省略（默认当前对话绑定的 Agent，或系统 Agent）；项目会话必须是项目成员（先 `list_members`），不要编造。
`action_kind:"task"` 只能在项目会话里创建（`TASK_REQUIRES_PROJECT`）。

## 2. 先选 code 还是 agent

- 从**已知的结构化来源**做确定性的取数、校验、计算、阈值判断、生成文件 → `execution:{"kind":"code"}`。
  没有模型调用，**不消耗点数**。
- 每次都要发现来源、阅读非结构化内容、归纳、解释、做语义判断 → agent（默认 `{"kind":"agent","mode":"chat"}`）。
  **每次运行都消耗点数**：建之前先告诉用户「这条自动化每次运行会消耗点数」并问是否继续；code 自动化不需要这句。
- 以工作性质为先，频率为次：高频（小于 6 小时一次）的确定性数据工作，强烈优先 code。

## 3. 三份契约（`create` / `update` 都可带，全部可选）

```jsonc
input_contract: {"kind":"none"}                                   // 默认；传 input 会被拒
               | {"kind":"text","default":"…"}                     // 一段自由文本
               | {"kind":"json","schema":{JSON Schema, type object},"default":{…}}
execution:      {"kind":"agent","mode":"chat"|"task"}              // 默认；mode 对应 action_kind
               | {"kind":"code","runtime":"python"|"shell","entry":"automations/<slug>/automation.py","timeout_seconds":600}
result:         {"kind":"conversation"}                            // 默认：那次会话 / 任务就是结果
               | {"kind":"artifact","schema":{JSON Schema, type object}}   // 每次 run 一个 JSON 对象
```

- `json` 输入：每次 run 的**有效输入** = `default` 顶层浅合并本次 `input`，再按 `schema` 校验；
  校验在 run 行创建**之前**，不过 → `AutomationInputInvalid`（消息形如 `input.symbol: 'symbol' is a required property`）。
  上限 256 KiB。text 输入不传 `input` 时用 `default`。
- `entry` 是**项目相对**路径（作者自选；禁止绝对路径与 `..`），文件是否存在在**运行时**检查（`AUTOMATION_CODE_ENTRY_MISSING`）。
  `timeout_seconds` 默认 600，上限 3600。code 行没有 agent：`agent_slug` / `worktree` / playbook 被忽略。
- 规则：**code ⇒ artifact**（没声明也会自动补一个不带 schema 的 artifact result）；**task ⇒ 不能 artifact**
  （`AutomationTaskArtifactUnsupported`）。artifact 上限 1 MiB，大结果走 `files`。
  `result_summary` = `artifact.summary`（字符串时）否则 JSON 前 200 字。

## 4. 写一条 code 自动化：按这个顺序

1. **先把入口文件写到项目里**（如 `automations/daily-metric/automation.py`，定义 `run(ctx)`，见 §5）。
2. `create`，`trigger:{"kind":"manual"}` 起步，`execution:{"kind":"code","entry":"automations/daily-metric/automation.py"}`，
   按需带 `input_contract` / `result.schema`。复述后**停下等用户点卡片**。
3. 用户确认后 `list` 拿 `automation_id`，`run` 带样例 `input` 和 `wait_seconds`（≤ 60）：
   要求返回的 `run.status == "success"`，检查 `run.artifact`（和 `run.files`）；失败看 `run.error_code` / `run.log_tail`。
4. 验过再 `update` 触发器（cron / interval）。程序超过 60 秒的，`run` 后用 `read_run` 轮询。

## 5. Python 运行时（速查，全契约见 references/python-runtime.md）

平台用只依赖标准库的引导脚本 import 你的入口模块并调用 `run(ctx)`（同步或 `async def` 都行）；**顶层脚本代码不是入口**。
工作目录 = 项目目录；环境是最小化的（`PATH` / `HOME` / `LANG` / `TMPDIR` + `VALUZ_AUTOMATION_*`），**没有任何凭据**。

```py
import json, os

def run(ctx):
    inp = ctx["input"] or {}
    prev = ctx["previous"]                    # 上一次成功产出 {runId, completedAt, artifact}，没有则 None
    since = prev["artifact"].get("asOf") if prev else None
    rows = [{"symbol": inp.get("symbol", "600519"), "value": 42.0}]   # 确定性的取数 / 计算
    path = os.path.join(ctx["runDir"], "files", "metric.json")        # 文件必须放在 runDir 内
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)
    return {
        "artifact": {"summary": f"{len(rows)} rows", "asOf": ctx["triggeredAt"], "since": since, "rows": rows},
        "files": [{"sourcePath": path, "name": "metric.json", "mimeType": "application/json"}],
    }
```

- `ctx` 键：`automationId` `automationName` `runId` `projectId` `projectDir` `workspaceDir` `runDir` `input`
  `trigger{type,invokedBy,invokedBySessionId}` `triggeredAt` `scheduledAt` `timezone` `locale` `previous{runId,completedAt,artifact}|null`。
- 环境变量：`VALUZ_AUTOMATION_ID` `_RUN_ID` `_PROJECT_DIR` `_RUN_DIR` `_WORKSPACE_DIR` `_CTX_FILE` `_INPUT_FILE` `_OUTPUT_FILE`
  （前缀都是 `VALUZ_AUTOMATION`）。
- 返回**包装对象** `{"artifact": <object>, "files"?: [{sourcePath, name?, mimeType?}]}`；程序自己写到
  `$VALUZ_AUTOMATION_OUTPUT_FILE` 的同形 JSON **优先于**返回值。`files` 的 `sourcePath` 必须在 `runDir` 内，
  ≤ 32 个、单个 ≤ 8 MiB、合计 ≤ 32 MiB，主机读回后登记为项目产物；**不要把路径 / 字节 / base64 塞进 artifact**。
- 目录：`<项目>/.valuz/automations/<automation_id>/workspace/`（跨 run 持久的草稿区）和 `runs/<run_id>/`
  （`ctx.json` `input.json` `output.json` `stdout.log` `stderr.log` `files/`，只保留最近 20 个）。
- 退出码：`0` 成功 · `2` 契约违规（没有 `run`、返回不是对象、缺 `artifact`）· `3` 入口抛异常（traceback 在 stderr）。
  stdout / stderr 尾部 64 KiB 进 `read_run` 的 `log_tail`。

## 6. Shell 运行时

`bash <entry>`，同一组环境变量与工作目录；脚本**必须自己**把包装 JSON 写到 `$VALUZ_AUTOMATION_OUTPUT_FILE`
（ctx 在 `$VALUZ_AUTOMATION_CTX_FILE`，输入在 `$VALUZ_AUTOMATION_INPUT_FILE`），退出码 0；stdout 只当日志。

## 7. Agent 运行（细节见 references/agent-execution.md）

- 开场消息 = `<automation-run automation_id run_id trigger name>` 前言（「这是自动化 X 的一次运行，现在完成任务；
  不要创建 / 修改 / 重排任何自动化」）+ json 输入的 `<automation-input>` JSON 块 + 渲染后的 `prompt_template`。
- 模板变量：`{{input}}`（整段 JSON / 文本）、`{{input.<key>}}`（仅标量）以及 `{{today}}` `{{now}}` `{{tz}}` `{{last_run_at}}` 等。
- `result.kind="artifact"` 的 agent run **必须**在回合结束前调 `output`（`artifact` 匹配 `result.schema`，
  `files` 为项目相对路径）**且只调一次**；没调 → run `failed` / `AUTOMATION_NO_ARTIFACT`。
- run 的会话里 `create` / `update` / `pause` / `resume` / `remove` 一律被拒（`AutomationMutationInsideRun`）；
  `run` 别的自动化仍可以。

## 8. 运行与验证（细节见 references/verify.md）

- 只有终态 **`success`** 算成功；`failed` / `timeout` / `cancelled` / `skipped` / `interrupted_by_shutdown` 是不同的事实，
  别混为一谈；`queued` / `running` 未完成——用 `read_run` 轮询，**不要再发一次 `run`**。
- `run` 不带 `wait_seconds` 时只回「已入队 + run_id」（在 `message` 里）；带了就回 `run` 详情，`ok` 在 `success` 或仍在跑时为 true。
- `AutomationAlreadyRunning` / `AutomationAlreadyQueued` → 先 `runs` 看活跃的那条，再决定等还是 `cancel`。
- `cancel`：排队中的立即变 `cancelled`；正在跑的**程序**被杀进程组后变 `cancelled`；正在跑的 agent 回合不能在这里取消
  （`AutomationCancelUnsupported`，去停会话）。
- 失败证据：`run.error_code` + `run.error_message`，code run 再看 `run.log_tail`。

## 9. 触发器

```jsonc
{"kind":"cron","cron_expr":"0 9 * * 1-5","timezone":"Asia/Shanghai"}   // 5 段 POSIX cron；整点 / 固定时刻
{"kind":"interval","seconds":3600}                                     // 每隔 N 秒，最小 30；键名是 seconds
{"kind":"manual"}                                                      // 只靠 run 触发
```

- **cron 必须带 `timezone`**：从本轮上下文的 `Current time: … (Asia/Shanghai, UTC+08:00)` 取用户的 IANA 时区原样传入；
  **永远不要编造、不要用 UTC**。用户说「每天」没给时刻时，用当前时刻并告诉用户你假定了什么。
- `{"kind":"event"}` 只在部署方注册了事件源且已配置订阅时有意义；工具没有 `event_source` 参数，不要用它建新行。
- **agent 自动化的频率地板**：没有用户明确要求，不高于每小时一次；要更密先说明点数与噪音代价并确认。code 自动化按数据源的更新节奏定。
