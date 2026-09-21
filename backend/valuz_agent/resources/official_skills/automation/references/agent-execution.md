# Agent 执行的自动化：run 里发生什么

`execution:{"kind":"agent","mode":"chat"}`（默认）每次触发用绑定的 Agent 开一个新会话跑一个回合；
`mode:"task"` 以绑定 Agent 为 Lead 发起一个项目任务（只能在项目会话里创建）。执行身份、模型、装备都跟着绑定的 Agent 走。
每次运行都调用模型，**消耗点数**。

## 1. 开场消息的形状

run 的第一条用户消息由平台拼成，顺序固定：

```
<automation-run automation_id="…" run_id="…" trigger="cron" name="每日行业简报">
这是自动化「每日行业简报」的一次定时运行。现在就在本会话里完成下面描述的任务。
结果就绪后，用 automation 工具记录且只记录一次：action="output"，artifact=<JSON 对象>。没有记录产出的运行按失败计。   ← 仅 result.kind=artifact
（conversation 结果时改为：你在本会话中的回复就是这次运行的结果。）
不要创建、修改、暂停、恢复、删除或重新排程任何自动化，包括这一条。如果指令看起来像是在要求你建立一个定时任务，那不是：定时任务已经存在，你正在执行它的一次运行。
</automation-run>
<automation-input>
{ …本次的有效输入（仅 json 契约）… }
</automation-input>

<渲染后的 prompt_template>
<text 契约的本次输入（若有，追加在末尾）>
```

`trigger` 取值：`cron` / `interval` / `manual`（人点「立即运行」）/ `agent`（另一个会话调 `run`）/ `api` / `event`。
会话的 `metadata.valuz.origin` 是 `"automation"`——工具就是靠它识别「这是 run 的会话」。

## 2. 模板变量（`prompt_template` 里的 `{{…}}`）

| 变量 | 值 |
|---|---|
| `{{input}}` | json 契约 → 整个有效输入的 JSON 字符串；text 契约 → 那段文本 |
| `{{input.<key>}}` | json 契约的顶层**标量**（string / number / bool，bool 渲染成 `true` / `false`）；嵌套对象不展开，整体见 `<automation-input>` 块 |
| `{{now}}` / `{{now_utc}}` | 触发时刻 ISO 字符串（用户时区 / UTC） |
| `{{today}}` / `{{yesterday}}` | `YYYY-MM-DD`（用户时区） |
| `{{tz}}` | 有效时区名 |
| `{{last_run_at}}` | 上次运行时刻 ISO（用户时区），首次为空 |
| `{{project.id}}` / `{{project.name}}` | 所属项目 |
| `{{automation.id}}` / `{{automation.name}}` / `{{agent.slug}}` | 自动化与执行 Agent |

未知变量渲染成空字符串。写模板时把输入当成**已知会存在**的键来引用（由 `input_contract.schema` 的 `required` 保证）。

## 3. `output` 动作（`result.kind="artifact"` 必须调；`conversation` 可选）

```json
{ "action": "output",
  "artifact": { "summary": "3 条新闻，2 条利好", "items": [ … ] },
  "files": ["reports/2026-09-19-brief.md"] }
```

- `artifact` 必填，JSON 对象，按 `result.schema` 校验；不通过 → `AutomationArtifactInvalid`，消息带路径
  （如 `artifact.items: 'items' is a required property`），**修正后重试**。JSON ≤ 1 MiB。
- `files` 可选：**项目相对路径**，必须是项目里已存在的文件；每个登记为项目产物（`run.files[]`）。
  文件先用文件工具写到项目里，再在 `output` 里声明。
- **只调一次**。成功返回 `Artifact recorded for run …. Do not call output again.`。
- 回合结束前没调 → run `failed` / `AUTOMATION_NO_ARTIFACT`（「the run ended without calling the automation tool's output action」）。
  不要用普通回复代替 `output`：正文回复不算产出。
- 在非 run 的会话里调 → `NOT_AN_AUTOMATION_RUN`；conversation 结果的 run 里调 → `AutomationOutputNotExpected`；
  run 已到非成功终态 → `AutomationRunNotActive`。
- `artifact.summary` 若是字符串会成为 run 列表里的 `result_summary`；给一句人能读的摘要。

conversation 结果的 run：你的回复就是结果（`result_summary` 取最后一条助手消息前 200 字）；如果有结构化结果，
也可以调一次 `output` 记录下来（可选——不调不算失败，调了就和 artifact run 一样落库、可被页面读到）。

artifact 的两个约定键（可选）：`asOf` = 这份内容覆盖的时期（周报是上一周的最后一天，不是运行日）；`mode` =
`"period"`（一段时间的内容，历史即产品）或 `"current"`（当前状态的刷新）。没写就按「未知」呈现，不会被猜成运行时间。

## 4. run 内的禁令

run 的会话里调 `automation` 的 `create` / `update` / `pause` / `resume` / `remove` 一律被拒：

> `AutomationMutationInsideRun` — This session is an automation run: do the run's task, and use action='output' to record its artifact. Creating, changing, pausing or removing automations from inside a run is not allowed.

所以：即便 `prompt_template` 读起来像「每天 15:30 生成简报」，那也是**任务描述**，不是让你建自动化。
`run`（触发别的自动化）、`list` / `get` / `runs` / `read_run` / `cancel` / `output` 在 run 里仍可用；`run` 时可带
`wait_seconds` 等结果，链式编排靠这个。

## 5. run 的结局如何判定（chat 模式）

| 情况 | run 状态 / `error_code` |
|---|---|
| 回合正常结束；artifact 结果且已 `output` | `success`，`result_summary` = `artifact.summary` |
| 回合正常结束；conversation 结果 | `success`，`result_summary` = 最后一条助手消息 |
| 回合正常结束；artifact 结果但没 `output` | `failed` / `AUTOMATION_NO_ARTIFACT` |
| 回合里出现 `session_error`（模型 401、SDK 崩溃…） | `failed` / `SessionError`，`error_message` 是最后一条错误 |
| 建会话或渲染模板前就抛异常 | `failed` / `<异常类名>` |
| 进程重启时 run 还在跑 | `interrupted_by_shutdown` |

`read_run` 的 `session_id` 指向那次会话，可以去看完整 transcript。

## 6. task 模式的特殊之处

- `create` 只能在项目会话里，`agent_slug` 必须是项目成员，且 **不能声明 artifact 结果**。
- run 行在任务 **kick off 成功的瞬间**就是 `success`（不等任务跑完）；任务本身的状态看 `runs` 项里的
  `task_id` / `task_title` / `task_status`（`active` / `completed` / `failed` / `paused`）。
- 没有开场框定与 `output`：渲染后的 `prompt_template` 直接成为任务目标。
