# PLAN：自动化任务独立详情页（技术设计）

> 状态：技术方案（设计文档 + 本版含最小只读契约改动）· Owner：前后端开发 · 最后更新：2026-06-25
> 对应 PRD：[`prd.md`](./prd.md)（已收口 v2，最终版）

## 技术摘要（给 PM / QA / 设计先看风险）

本版主体是前端（详情页、菜单/动态新增「自动化」条目、运行中置顶），**但不再是纯前端**：
按 PRD v2「范围与 API 决策」，本版**允许最小只读契约新增，且契约先行**——这是为了根治
「菜单/动态运行态正确性」这条 **P0**。**两处契约新增（均只读、不碰调度/执行/create/update 核心）**：

1. **`AutomationItemResponse` 回填 3 个只读字段 `is_running` / `latest_run` / `created_at`**
   （list + detail 共享，因 `AutomationDetailResponse` extends item）：
   - `is_running: bool` —— **运行态权威布尔**（服务端对 `isAutomationRunning()` 的投影），让菜单/动态对**全量自动化**
     一次拿到正确运行态，避免 N×`listRuns` 轮询，并修掉 **task 型漏判**（见 §3）。
   - `latest_run: AutomationLatestRun | null` —— 最新一条 run 的摘要（`run_id/status/task_status/session_id/triggered_at`）。
     给：前端 `isAutomationRunning(latest_run)` 输入、展示态映射、**自动化运行卡 deep-link + SSE 订阅**（`latest_run.session_id`）、
     排序活跃时间戳。`null` = 从未运行。
   - `created_at: int` —— 从 detail 下放到 list item，给排序比较器的**最终回退键** `last_run_at ?? next_run_at ?? created_at`
     （**对齐 prd.md 比较器**，见 §4.3）。该值已在 ORM row 上（`service.py:_row_to_detail:272`）。
2. **`/v1/runs` 新增 `automation` 来源类型 + `automation_id`**：让**运行中**自动化以 `source_kind="automation"` +
   `automation_id` 的 `RunSummary` 进入跨类型 Running 组（§4.1 路径 B）——其 `session_id` 即承载该 run 的会话，**必非空**。
   这同时把自动化触发的 session 从 chat/task 桶里**正确剥离**（否则会伪装成普通 chat run 重复出现、错跳）。
   `RunSummary.session_id` 因此**保持必填 `string`、不改 nullable**；automation 来源的 `RunSummary` 只用于「有 `session_id`
   的运行中自动化」，未运行自动化走 §4.1 路径 A 的「自动化」Tab（`listGroups`），不进此管道。

**运行态唯一语义 `isAutomationRunning(latestRun)` 三处复用（`@valuz/core`，与后端 `is_running` 同口径，单测钉死一致）**：
- **详情页** = `listRuns(id)[0]`（最新 run，客户端跑完整语义）——已正确，不变；
- **菜单**（最近列表 / 侧边 Recents）= `AutomationItem.is_running`（服务端预计算）/ `isAutomationRunning(latest_run)`；
- **动态**（Activity）：「自动化」Tab 读 `AutomationItem.is_running`（路径 A，含未运行行）；跨类型 Running 组由 `/v1/runs`
  运行中 automation 的 `source_kind="automation"` `RunSummary` 进 running 池（路径 B，仅「有 `session_id` 的运行中自动化」，见 §4.1）。

语义同一（`run.status ∈ {queued,running}` 或 run 结算后 `task_status == active`）：`is_running` 就是服务端对该函数的投影，
`latest_run` 是其输入数据，客户端不再逐条 `listRuns`。

> ⚠️ **契约债前置**：`/v1/automations/*` 当前**完全不在** `api/openapi.yaml`（grep 命中 0），前端类型为
> **手写**且已有漂移（如 `AutomationRunItem.error_message` 后端不返回，详见 §7）。本版要动 `is_running`
> 与 `/v1/runs` automation 来源，**必须先把相关只读片段补进 OpenAPI**，再改后端，再 `make generate-types`。

---

## 1. 路由与页面落点

**新路由**（注册式，遵循 `frontend/CLAUDE.md`「编辑 registries，不硬编码 router」）：

| 改动点 | 文件 | 内容 |
|---|---|---|
| 路由声明 | `frontend/packages/core/src/edition/registries/desktop-routes.ts` | 在现有 `id: "automation"`（`/automations`，167-174）之后追加 `id: "automation-detail"`, `path: "/automations/:automationId"`, `layout: "project"`, `showInNav: false`, `edition: "personal"` |
| 组件映射 | `frontend/packages/app/src/routes/route-registry.ts` | `COMPONENT_MAP` 新增 `"automation-detail": AutomationDetailPage`；并在 `../pages` 导出新页面 |
| 新页面 | `frontend/packages/app/src/pages/AutomationDetailPage.tsx`（新建） | 详情页本体 |

> ✅ 回归点已核实：desktop renderer 的 `route-registry.ts` 只 re-export app 的 `resolvedDesktopRoutes`
> （review PLAN 第1轮 P2 approve），故只需改 app 一处 `COMPONENT_MAP`。

**入口（点进详情）从哪来**（PRD 明确不重做 `AutomationPage` 版式，只加跳转）：

- **全局总览页** `AutomationPage.tsx`：当前行点击 `onRowClick → openEditDialog`（开编辑弹窗）。
  详情页接管「查看」，编辑收敛到详情页内。
  - **推荐做法（最小、可加性）**：给 `ScheduledTaskTable` 增加一个可选 `onOpenDetail?: (id) => void`，
    行标题按钮优先调它（缺省回落到 `onRowClick`，保持其它消费者不变）；`AutomationPage` 传
    `onOpenDetail={(id) => navigate(\`/automations/${id}\`)}`。下拉菜单的「编辑」继续走 `onRowClick`
    → 现有弹窗，不破坏。
  - 备选：直接把 `onRowClick` 重定向到详情页（更省，但下拉「编辑」也会跳详情——详情页有编辑按钮，
    可接受，只是语义略变）。
- **菜单**（`ConversationsHomePage` 最近列表 / 侧边 `Recents`）与**动态**（`ActivityPage`）：
  新增的自动化条目点击 → `navigate(\`/automations/${automationId}\`)`。详见 §4。

---

## 2. 调用链 / 数据流（详情 / 运行记录 / run-now / edit / delete）

> 真实方法名以 `frontend/packages/core/src/api/automations-api.ts` 为准（259-333）。
> ⚠️ PRD 口径里的 `automationsApi.detail` / `.runs` 在代码中**实为** `get` / `listRuns`，下面按真实符号写。

全链路：`automations-api.ts` → `api/routes/automations.py`（176-251）→ `modules/automations/service.py`。

| 动作 | 前端 | HTTP | 路由处理器 | service |
|---|---|---|---|---|
| 详情 | `automationsApi.get(id)` | `GET /v1/automations/{id}` | `get_automation` | `get_automation_detail` → `_row_to_detail` → `AutomationDetailResponse` |
| 运行记录 | `automationsApi.listRuns(id, limit)` | `GET /v1/automations/{id}/runs` | `list_automation_runs` | `list_runs`（默认 `limit=20`）→ `_resolve_task_statuses` + `_run_to_item` → `AutomationRunItemResponse[]` |
| 马上运行 | `automationsApi.runNow(id)` | `POST /v1/automations/{id}/run-now` | `run_automation_now` | `run_now`（单飞：最新 run `queued`→`AutomationAlreadyQueued`、`running`→`AutomationAlreadyRunning`，均 **409**，`service.py:890-895`）→ `AutomationRunAccepted` |
| 编辑 | `automationsApi.update(id, payload)` | `PATCH /v1/automations/{id}` | `update_automation` | `update`（`trigger` 全量替换；`agent_slug` 仅同项目内换；跨 kind 不支持） |
| 删除 | `automationsApi.delete(id)` | `DELETE /v1/automations/{id}` | `delete_automation` | `delete`（级联 run 历史） |
| 暂停/恢复 | `automationsApi.pause/resume(id)` | `POST /v1/automations/{id}/pause`\|`/resume` | `pause_automation`\|`resume_automation` | `pause`（清 `next_run_at`）/ `resume`（重算 `next_run_at`） |

**详情页加载序**（参照 `AutomationPage` 既有写法）：
1. `automationsApi.get(automationId)` → `AutomationDetail`（含 `prompt_template / trigger_human_readable /
   next_run_at / last_run_at / project_kind / project_id / project_name / agent_kind / agent_name / action_kind /
   status / total_runs / recent_failures / created_at / updated_at`）。
2. `automationsApi.listRuns(automationId)`（不传 limit，用后端默认 20，对齐 PRD「最近 N=20 条」）→ `AutomationRunItem[]`。
3. 轻量轮询：`setInterval(refreshRuns, 5000)`，**只重拉 runs**（页面可见时轮询，隐藏暂停；复用 `AutomationPage`
   5s 节奏，避免 `PageLoader` 闪烁）。运行态最大延迟 ≤ 5s，对齐 PRD 验收。

### 2.1 编辑弹窗复用 + **manual trigger 不被破坏（P1，方案选定）**

直接复用 `CreateAutomationDialog`（编辑模式），照搬 `AutomationPage.openEditDialog` /
`handleDialogSubmit(edit 分支)`：
- 详情页已有 `AutomationDetail`，再 `agentsApi.listMembers(detail.project_id)` 取候选 → 组装 `agentChoices`；
- 传 `initial={ name, prompt_template, agent_slug, trigger, action_kind }`、`fixedTargetName={project_name}`、
  `allowTaskMode={project_kind === "project"}`、`title`（编辑命名标题）；
- `onSubmit` 走 `automationsApi.update`，成功后重拉详情 + runs。

**真实数据丢失 bug（已核实）**：`CreateAutomationDialog` 编辑 `trigger.kind === "manual"` 的自动化时，
seeding 落到默认 cron（`CreateAutomationDialog.tsx:343-350`「manual 未暴露 → fall through 到 cron」），
而 `buildTrigger()` **只能输出 cron/interval**（398-412）。结果：一次编辑提交即把 manual 自动化**静默改成默认
cron**——manual 触发器被永久覆盖。后端与前端类型**都支持** `ManualTrigger`（`schemas.py:ManualTrigger`、
`models.py:ck_automation_trigger_kind` 允许 manual、前端 `automations-api.ts:ManualTrigger`），所以这是纯前端
弹窗的丢数据缺陷。

**PM 底线**：编辑 manual 自动化**绝不能静默改变其 trigger**。

**选定方案 = (a) 弹窗支持 manual 无损 round-trip**（推荐，优于 (b)）：
- `triggerKind` 状态增 `"manual"`；seeding 时 `initial.trigger.kind === "manual"` → `setTriggerKind("manual")`，
  不再 fall through 到 cron；`buildTrigger()` 在 `triggerKind === "manual"` 时返回 `{ kind: "manual" }`。
- manual 在编辑模式作为**保留态/只读说明**呈现（「手动触发，无定时」），用户改其它字段（name/prompt/agent）时
  trigger 原样回写；**不需要**为新建流程新增「手动」创建 Tab（manual 自动化经 MCP/proposal 创建，本版不扩创建口径）。
- **选型理由**：`CreateAutomationDialog` 是**唯一共享编辑面**（总览 + 详情都用它），(a) 一次修复同时惠及
  `AutomationPage` 既有编辑，符合「单一编辑面、契约/类型先齐」；(b)「详情页检测 manual 后禁用 trigger 编辑」
  要在详情页另写门控，把缺陷绕过而非根治，且 manual 自动化连改 prompt 都受限，体验更差。

> 此处编辑接法与 `AutomationPage` **重复了 2 处**（总览 + 详情）。按「三处重复才抽」原则**先不抽**；
> 若项目详情页等出现第 3 个消费者，再提取 `useAutomationEditDialog` hook。见 §7。

### 2.2 runNow 并发与错误处理（补决策）

`run_now` 后端单飞：最新 run 已 `queued` → `AutomationAlreadyQueued`、已 `running` → `AutomationAlreadyRunning`，
均返 **409**（`service.py:890-895`）。UI 处理：
- **首选预防**：「马上运行」按钮在 `isAutomationRunning() === true` 时 **disabled**（`Tooltip`「正在运行中」），
  从源头避免重复触发（详情页天然有 `listRuns[0]` → 直接判）。
- **兜底**：仍发生 409（如轮询窗口竞态）→ `catch` 中起**错误 toast**，展 `error_code` / `message`（对齐 PRD
  异常边界）。
- 调用链：`automationsApi.runNow(id)` → 202/`AutomationRunAccepted`（成功，立即 `refreshRuns()`）｜409（catch → toast）。
- 另：`runNow` 还需 `status === "enabled"` 且 `agent_name != null` 才可点（PRD 顶部操作约束）。

---

## 3. 「运行中」状态的数据来源与判定逻辑（详情 / 菜单 / 动态三处）

**核心结论（已查证）**：
- `useRunningRuns` → `GET /v1/runs?status=running`（`runs/service.py`）由 host 的 project↔session 索引构建。
  自动化触发的 session 当前被归类为通用 `assistant`/`project_chat`/`task`，且 `RunSummary` **不含 `automation_id`**
  → 无法据此反推「哪条自动化在跑」。本版**新增 `automation` 来源类型 + `automation_id`** 解掉这一限制（§4）。
- per-automation 运行态的**唯一语义** `isAutomationRunning(latestRun)`（PRD「运行态判定」映射表）：
  1. `latest_run.status ∈ {queued, running}` → **运行中**（`run.status` 优先，`task_status` 不参与）；
  2. 否则若 task 型且 `latest_run.task_status == active` → **运行中**（run 行已结算但 task 仍活跃）；
  3. 否则非运行中（`success+paused`=已暂停、`failed`/`skipped`/`interrupted_by_shutdown` 各自展示）。

> `runToLogStatus`/`runStatusToLogStatus` 当前是 `AutomationPage.tsx` 模块内私有函数。详情页要用它把 run →
> 行状态。**提取为共享纯函数**（建议放 `@valuz/core`，如 `automation-run-status.ts`，导出 `runToLogStatus` +
> `isAutomationRunning`），`AutomationPage` 改为引用。这是本方案唯一被授权的前端抽象（详情页 `listRuns[0]` +
> 菜单/动态自动化行的 `is_running` 三处复用同一判定）。

### 3.1 三处取数（数据源不同，语义同一）

| 位置 | 数据来源 | 判定 |
|---|---|---|
| **详情页** | 已加载的 `listRuns` 结果 `[0]`（按 `triggered_at` 倒序最新一条） | `isAutomationRunning(latestRun)`；顶部用 `StatusPill`（`running` 态自动脉冲）显示「正在运行」，历史行按映射表着色 |
| **菜单** | `automationsApi.listGroups()` 的 `AutomationItem.is_running` | 直接读服务端预计算的 `is_running`（**0 额外请求**、**修掉 task 漏判**）。见 §3.2 |
| **动态** | 「自动化」Tab：`automationsApi.listGroups()` 的 `AutomationItem.is_running`（路径 A，独立列表，含未运行行，行**不订 SSE**）；跨类型 Running 组：`/v1/runs` 运行中 automation 的 `source_kind="automation"` `RunSummary`（路径 B，必有 `session_id`） | 自动化 Tab 读 `is_running` 分组；运行中 automation 经 `useRunningRuns` 进 Running 组、`RunningCard` 订 `run.session_id` SSE。两路互不相交，无 `session_id=null`（§4.1） |

### 3.2 **`is_running` 升 P0 + 契约先行（修 task 型漏判）**

**为什么升 P0**：PRD 把「菜单/动态运行态正确性」列为 **P0**。原 PLAN 用 `AutomationItem.last_run_status ∈
{queued,running}` 判运行——对 **chat 型够用，对 task 型漏判**：task run 在 kickoff 时 `run.status` 立即写为
`success`（`in_process_runner.py:_execute_task_kickoff`，496-562），真实活态只在 `task_status`，而 `task_status`
**不在 list item 上**（`_row_to_item` 只回填 `last_run_status`，`service.py:244-263`）。继续按 `last_run_status`
上线会出现「详情页显示 task 正在运行，但菜单/动态漏置顶」——这是**运行态正确性 bug**，故 `is_running`
从原 P1 **升为 P0**。

**契约先行实现顺序（三步，不可颠倒）**：
1. **OpenAPI**：在 `api/openapi.yaml` 补 `/v1/automations` 的最小只读响应片段（至少 `AutomationItem` schema），
   新增字段 `is_running: boolean`（详见 §7）。
2. **后端**：`AutomationItemResponse` 增 `is_running: bool`；`_row_to_item` 计算
   `is_running = last_run.status ∈ {queued, running}` **或**（task 型）解析后 `task_status == active`。
   实现复用 `_resolve_task_statuses`：当 `last_run` 携带 `session_id` 时，对**该单条** last_run 解析 task_status
   （与 `list_runs` 的批量解析同源，单条无 N+1），据此回填 `is_running`。
3. **前端**：`make generate-types` 后，`AutomationItem` 多出 `is_running`；菜单/动态「自动化」行的 `isRunning` 改读
   `item.is_running`，不再各自 `listRuns`。

> **详情页不变**：详情页天然有 `listRuns` 全量 runs，`isAutomationRunning(listRuns[0])` 客户端直接算，**已正确**，
> 不依赖 `is_running`。`is_running` 只服务于「列表侧一次拿全且对 task 型正确」。

---

## 4. 菜单与动态新增「自动化」类型 + 运行中置顶排序

### 4.1 动态 `ActivityPage.tsx` — Activity 行模型落地（P1 收口）

**现状（已核实，按真实类型 / 分支）**：
- 数据全部是 `RunSummary`：`useRunningRuns` 跑中 + `runsApi.list({status:"finished"})` 历史。
- `RunSummary.session_id` 是**必填 `string`**（`runs-api.ts:22`）；`RunSourceKind = "assistant" | "project_chat" | "task"`
  （`runs-api.ts:11`，**无 automation**、**无 `automation_id`**）。
- `SourceFilter = "all" | "chat" | "task"`（43）；`FILTERS` 三项（565-569）；`matchesFilter`（389-393，chat 分支为
  `return r.source_kind !== "task"`）。
- `RunningCard` 用 `run.session_id` 订 SSE（`useSessionEvents(run.session_id)`，199）；`historyRow` 用 `run.session_id`
  做 React key（435/451）、rename（`renameSession(run.session_id)`，498）、delete（`canDelete = run.source_kind !== "task"`，
  431 → `sessionsApi.delete(target.session_id)`，511）。
- `openRun`（408-414）task→`/tasks/:task_id`、否则→`/conversation/:session_id`；`groupedHistory` 按
  `RunSummary.updated_at` 分桶（549-559）。

**根因（PM 收口）**：上面每一处都把 `session_id` 当**必填非空**用。未运行自动化没有 run、没有 session，硬塑进
`RunSummary`（`session_id: string`）必然要 `session_id = null`，与 SSE / key / rename / delete 全部分支不兼容——这正是
上一轮未收口的 P1。**不**把 `RunSummary.session_id` 改 nullable（会牵动 OpenAPI `required` 与所有消费点逐处加 guard，
并把「无 session」语义渗进通用运行管道）。改为**从根上拆成两条互不相交的渲染路径**，让 `session_id=null` 根本不产生：

**路径 A —「自动化」Tab（独立渲染路径，不走 `RunSummary`、不需 `session_id`）**
- **数据源**：仅 `automationsApi.listGroups()` 的 `AutomationItem`——每条自动化恒一行，**含未运行 / idle / 运行中**
  （PRD「每条自动化各占一行、恒显示」）。
- **运行态**：读服务端预计算的 `AutomationItem.is_running`（§3.2），**不依赖 session**、不发 N×`listRuns`。
- **行组件**：**独立的 automation 行渲染**（非 `RunningCard`、非 `historyRow`）；**不复用**仅适用于有 session 的分支
  ——不订 `useSessionEvents` SSE、不挂 `renameSession` / `sessionsApi.delete`（automation 行无 session，rename/delete
  在详情页内做，§2.1 / §2）。行只用一个 `StatusPill`（`is_running` → 「正在运行」脉冲，否则映射表着色）。
- **点击**：行点击 → `navigate(\`/automations/${automationId}\`)`，跳转唯一依据是 `automation_id`（`AutomationItem` 本就
  有，无需 session）。
- **排序**：Tab 内独立排序，用 §4.3 的 `AutomationItem` 比较器（`is_running` desc → 活跃时间戳 desc → `automation_id`
  asc），不与 chat/task 的 `RunSummary` 混排。
- **渲染切换**：`filter === "automation"` 时切到这套**独立列表视图**，不经 `matchesFilter` / `groupedHistory`（那两者
  是 `RunSummary` 专用）。

**路径 B — 跨类型 Running 组 + 置顶（仍走 `RunSummary`）**
- **适用面**：`all` Tab 顶部「Running」组的跨类型置顶（对话 / 任务 / 自动化同列），及菜单/侧边最近列表的运行置顶
  （§4.2 / §4.3）。
- **入组条件**：**仅**「真有一次运行（有 `session_id`）且正在跑」的自动化，以
  `RunSummary{ source_kind:"automation", automation_id, session_id, ... }` 经 `/v1/runs`（`useRunningRuns`）进入。
  运行中的 automation run **必然**有承载它的 kernel session，故此处 `session_id` 始终是真实 `string`，**永不为 null**。
- **未运行不在此**：未运行自动化本就不属于运行组，**不**经 `/v1/runs` 产出任何 `RunSummary`（它们不在运行索引里），
  只出现在路径 A 的「自动化」Tab。
- **复用边界（因必有 session，可安全复用）**：automation `RunSummary` 进 `RunningCard`，订
  `useSessionEvents(run.session_id)`；`openRun` 前置 automation 分支
  `if (r.source_kind === "automation" && r.automation_id) { navigate(\`/automations/${encodeURIComponent(r.automation_id)}\`); return; }`。
- **不复用 rename/delete**：automation `RunSummary` 不进行内 rename/delete——把 `canDelete` 由 `source_kind !== "task"`
  收紧为「仅 chat」（`source_kind === "assistant" || source_kind === "project_chat"`），automation 与 task 一样不暴露
  行内 rename/delete（automation 删除走详情页）。

**契约改动（据两路精确化）**：
- `RunSourceKind` 增 `"automation"`；`RunSummary` 增 `automation_id: string | null`（仅 `source_kind === "automation"`
  时非空）。
- **`RunSummary.session_id` 保持必填 `string`，不改 nullable**——进入 `RunSummary` 的 automation 只可能是「运行中且有
  session」那一类，没有「无 session 的自动化」走这条管道。OpenAPI `RunSummary.required` 仍含 `session_id`，无需放宽。
- `source_kind === "automation"` 的 `RunSummary` **只**用于「有 `session_id` 的运行中自动化」——由 `/v1/runs` 对运行中
  automation run 投影产出；**不**为未运行自动化造任何 `RunSummary`（无合成、无 null 行）。

**三种 source 的 render / open 分支（路径 B / `RunSummary` 管道内）**：

| source_kind | RunningCard 渲染 | SSE | open（点击目标） |
|---|---|---|---|
| `task` | `ListChecks` 卡 | 订 `run.session_id` | `/tasks/:task_id` |
| `project_chat` \| `assistant` | `MessageSquare` 卡 | 订 `run.session_id` | `/conversation/:session_id` |
| `automation`（**仅运行中、必有 session**） | automation icon 卡 | 订 `run.session_id`（automation run 的会话 session） | `/automations/:automation_id` |

> 原「扩展 `RunSummary` + 合成 `session_id=null` 行」与「`ActivityEntry` 判别联合」之争**已被两路分离化解**：
> 「自动化」Tab 直接渲染 `AutomationItem`（不进 `RunSummary` 管道，无需合成行、无需判别联合），运行中 automation 是
> 货真价实的 `RunSummary`（有 session，无需合成）。

**Tab / 筛选落地**：
- `SourceFilter` 加 `"automation"`；`FILTERS` 加 `{ value: "automation", labelKey: "activity.automationTag" }`（新 i18n
  key，`zh-CN`/`en-US` 双写 + `gen_types.py`）。
- `automation` Tab → 路径 A 独立列表视图（渲染 `AutomationItem` 行，不走 `matchesFilter`）。
- `all` Tab：走路径 B 的 `RunSummary` 装配；其 Running 组**含运行中的 automation**（与 chat/task 同列置顶，§4.3 共用
  比较器），history 区**不含**未运行自动化（仅在「自动化」Tab）。`matchesFilter` 中 `all` 仍 `return true`。
- `chat` Tab：`matchesFilter` 的 chat 分支须**排除 automation**（现为 `source_kind !== "task"`，会误纳运行中 automation
  `RunSummary`）——收紧为 `return r.source_kind === "assistant" || r.source_kind === "project_chat"`。`task` Tab 不变。

### 4.2 菜单

PRD 的「菜单」含两个面：
1. **`ConversationsHomePage` 最近列表**：现状 `recentSessions = chatSessions.slice(0,5)`（来自 `sessionsApi.list()`
   过滤 chat 项目，review 已核实 175-190），**无运行置顶**。改动：并入 `automationsApi.listGroups()` 的自动化条目
   （**每条恒一行**，点击 → 详情页）；排序改为**运行中优先、其余按时间倒序**（用 §4.3 共用比较器）。
2. **侧边 `Recents`**（`ProjectLayoutBase.tsx:319-440`）：现状把 `useRunningRuns`(liveRuns) + finished `RunSummary`
   合并成 `DesktopSidebarRecentItem` 按项目分组，**已有运行置顶**（liveRuns 先入）。引入 **`listGroups` 第二数据源**
   补「从未运行」自动化的恒显示行（运行中的 automation run 已经由 `/v1/runs` 进 liveRuns，无需重复），合并进 recents
   ——这是 sidebar 的主要影响面。

### 4.3 排序 key（P1，两数据源 + 共用比较器）

**两种数据源、各自 key，归一后共用同一比较器**（菜单/动态/侧边一致）：

| 数据源 | 用在哪 | 运行态 | 活跃时间戳（排序 key） | tie-breaker |
|---|---|---|---|---|
| `RunSummary`（含 automation source） | 动态 Activity、侧边 liveRuns | running 池成员 | `updated_at`（与现有 `groupedHistory` 一致） | `session_id` 升序 |
| `AutomationItem`（`listGroups`） | 菜单最近列表、侧边「未运行」补流、动态「自动化」Tab（路径 A，独立列表，含未运行行） | `is_running` | **`last_run_at ?? next_run_at ?? 0`** | `automation_id` 升序 |

- **归一比较器**（`@valuz/core` 共享）：把任意行映射为 `{ isRunning, activeTs, id }`，比较顺序：
  `isRunning desc`（运行中整体置顶）→ `activeTs desc` → `id asc`（稳定）。
  - `RunSummary` → `{ isRunning: 在 running 池, activeTs: updated_at, id: session_id }`；
  - `AutomationItem` → `{ isRunning: is_running, activeTs: last_run_at ?? next_run_at ?? 0, id: automation_id }`。
- **与 PRD v2 排序梯度的差异（须知）**：PRD v2 写自动化时间戳取 `last_run_at → next_run_at → created_at`。
  但 **`AutomationItem` 无 `created_at`**（只有 `last_run_at/next_run_at/last_run_status`，`automations-api.ts:88-92`）
  ——`created_at` 仅 `AutomationDetail` 有（detail 上下文）。故列表比较器落地为 `last_run_at ?? next_run_at ?? 0`，
  **省去 created_at 段**；若严格要 created_at 兜底，需在 list 响应补 `created_at` 字段（**列入契约债，本版不补**，
  因 `last_run_at`/`next_run_at` 已能覆盖绝大多数排序需求）。

> **影响面**：`SourceFilter` / `FILTERS` / `matchesFilter` / `openRun` / Activity 数据装配（`ActivityPage`）；
> `recentSessions` 装配 + 排序（`ConversationsHomePage`）；侧边 recents 第二数据源合并（`ProjectLayoutBase`）；
> 新增 i18n key（`activity.automationTag` 等，`zh-CN`+`en-US` 同步 + 重新生成类型）；归一比较器（`@valuz/core`）。

---

## 5. 全局 vs 项目区分与项目导航

数据直接来自 `AutomationDetail`（`get_automation_detail` 已带），判定**只看 `project_kind`**（对齐 PRD v2）：
- **全局自动化**：`project_kind === "chat"`（**即使带 `project_id`/`project_name` 也判全局**）→ 徽章「全局」，
  **不显示**项目名与「回到项目」导航。`agent_kind` 仅作「绑定来源」小字标注（library_agent / project_member），
  不参与归属判定。
- **项目自动化**：`project_kind === "project"` → 徽章「项目」+ 显示 `project_name` +「回到项目」链接 →
  `navigate(\`/projects/${project_id}\`)`（对应 `desktop-routes.ts` 里 `id: "project-detail"`, `/projects/:id`，
  32-33）。
- `action_kind` 不参与归属，仅决定运行态读取方式（task → 看 `task_status`，chat → 仅看 `status`）。
- `chat + task` 组合后端拒绝、不产生数据，前端无需处理（PRD 映射表）。

`agent_name === null`（上游 Agent 被删）→ 详情页标注「绑定的 Agent 已删除」，编辑/马上运行 disabled，
暂停/恢复/删除 仍可用（PRD 异常边界）。

### 5.1 删除成功跳转（补决策）

`delete` 成功（`DeleteConfirmDialog` 二次确认后）→ **按来源跳回**：
- **全局自动化** → `navigate("/automations")`（`desktop-routes.ts` `id: "automation"`，路由常量 `/automations`）。
- **项目自动化** → `navigate(\`/projects/${project_id}\`)`（`id: "project-detail"`，路由常量 `/projects/:id`）。

### 5.2 项目自动化的项目已删 — 降级（P2 known edge）

边界：项目自动化的所属项目已被删除时——
- **后端仍能返回完整详情**（`_row_to_detail` 里 `_get_project_info` 容错返回名字/kind）→ 详情页正常展示，
  但**隐藏「回到项目」导航**（项目已不存在，跳过去是死链）。
- **无法解析详情**（DTO 依赖项目解析、项目缺失导致取不到 `project_name/project_kind`）→ 详情页显示统一
  「找不到 / 不可用」态；列表侧（菜单/动态/总览）**跳过**这类自动化不渲染。
- 标为 **P2 known edge**：本版按上述前端降级处理，**不**为此改后端 DTO 形状（不引入「项目缺失降级字段」）；
  若后端实际在项目缺失时抛错而非容错返回，则统一走「找不到/不可用」+ 列表跳过分支。

---

## 6. 影响面与回归点

**新增（低风险）**
- `AutomationDetailPage.tsx`（新页面）。
- `desktop-routes.ts` 追加路由 / `route-registry.ts` 追加映射（desktop renderer 复用 app routes，单处即可）。
- 共享 `runToLogStatus`/`isAutomationRunning` + 归一排序比较器（`@valuz/core`）。
- i18n key：详情页文案 + `activity.automationTag` 等（`zh-CN`/`en-US` 双写 + `gen_types.py`）。

**契约新增（本版 P0，契约先行）**
- `api/openapi.yaml`：补 `/v1/automations` 只读片段（`AutomationItem` + `is_running`）与 `/v1/runs` 的 `automation`
  来源类型（`RunSummary.source_kind` 增 `automation`、增 `automation_id`）。详见 §7。
- 后端：`AutomationItemResponse.is_running`（`_row_to_item` 回填）；`/v1/runs` 投影 automation-source `RunSummary`。
- 前端：`make generate-types` 重生，`RunSummary`/`AutomationItem` 类型更新。

**改动既有（需回归）**
- `CreateAutomationDialog.tsx`：manual round-trip（seeding + `buildTrigger`）——回归点：cron/interval 编辑与新建不变，
  **manual 编辑不再被静默转 cron**。
- `ScheduledTaskTable.tsx`：新增可选 `onOpenDetail`（additive）——回归点：其它消费者不传时行为不变。
- `AutomationPage.tsx`：行点击改为跳详情——回归点：编辑/暂停/删除/run-now 下拉菜单仍可用。
- `ActivityPage.tsx`：`SourceFilter`/`FILTERS`/`matchesFilter`/`openRun`/数据装配（两路：「自动化」Tab 独立列表 +
  `RunSummary` 运行/历史）——回归点：原 chat/task 筛选与 Running/History 分区不被破坏；`chat` 分支收紧排除 automation；
  `all` Tab 的 Running 组含运行中 automation，「自动化」Tab 为独立列表视图。
- `ConversationsHomePage.tsx`：最近列表数据源 + 排序——回归点：原快速对话最近条目仍正确。
- `ProjectLayoutBase.tsx`：侧边 recents 合并第二数据源——回归点：现有 liveRuns 置顶 + 按项目分组（319-440）不回退；
  运行计数 badge 不受影响。
- 后端 `runs/service.py`：新增 automation-source 投影——回归点：现有 assistant/project_chat/task 分类不变。

**不动**：后端 `automations` 调度/执行核心逻辑；`AutomationPage` 版式；新建自动化新表单。

---

## 7. 最小可行（避免过度抽象）+ OpenAPI 契约

- **复用优先**：`get/listRuns/runNow/update/delete/pause/resume` 全部现成；`ExecutionLog`/`ExecutionLogRow`/
  `StatusPill`/`EmptyState`/`PageLoader`/`DeleteConfirmDialog`/`BackLink`/`ScheduledTaskTable`/`RunningCard` 直接用。
- **被授权的抽象**：`runToLogStatus`/`isAutomationRunning`（详情页 `listRuns[0]` + 菜单/动态自动化行的服务端 `is_running`
  投影）→ 提取共享；归一排序比较器（菜单/动态「自动化」Tab/侧边）→ 提取共享。两者均满足「三处复用」。
- **暂不抽**：编辑弹窗接法目前 2 处（总览 + 详情），不提 hook；第 3 处出现再抽。
- **不造**：不新增产物预览、不做筛选/搜索/分页、不动后端调度（PRD 明确砍掉）；list 不补 `created_at`（见 §4.3）。

**OpenAPI 契约改动点（本版 P0，契约先行）**
- **现状契约债（已核实）**：`/v1/automations/*` **完全不在** `api/openapi.yaml`（grep 命中 0），前端类型为**手写**且
  已有漂移——前端 `AutomationRunItem.error_message`（`automations-api.ts:128`）在后端 `AutomationRunItemResponse`
  中**不返回**（后端只有 `error_code` + `error_message_key`，`schemas.py:191-211`）。详情页失败态的
  `error_message_key → result_summary → error_message → error_code` 回退链里，`error_message` 实际恒为空，需以
  `error_message_key`（本地化）/ `error_code` 为主——**顺带修正**这处手写类型漂移（把 `error_message` 标注/对齐为
  后端实际不返回，或从前端类型移除以消歧）。
- **本版必须补（契约先行）**：
  1. `/v1/automations` 列表（与 `AutomationItem` schema）补进 `api/openapi.yaml`，新增 `is_running: boolean`（§3.2）。
  2. `/v1/runs` 的 `RunSummary` 增 `source_kind: "automation"` 枚举值与 `automation_id: string | null`（§4.1）。
  3. 顺带把 `AutomationRunItem.error_message` 漂移登记/修正。
- **顺序**：先改 `api/openapi.yaml` → 再后端（`AutomationItemResponse`/`_row_to_item`、`/v1/runs` 投影）→
  `make generate-types` 重生前端类型 → 再改前端消费。**这是本版唯一的契约改动，且只读、最小。**

---

## 8. 已知 trade-off

1. **动态两条独立渲染路径**：「自动化」Tab 的「恒显示恒一行（含从未运行）」只走 `listGroups` 的 `AutomationItem`
   （独立列表渲染，路径 A，不走 `RunSummary`、不需 `session_id`）；跨类型 Running 组/置顶只走 `/v1/runs` 的运行中
   automation `RunSummary`（路径 B，必有 `session_id`）。两路**不合并、不互造行**——未运行只在路径 A、运行中只在
   路径 B——以此根除 `session_id=null`。代价是 `ActivityPage` 维护「automation 列表视图」与「`RunSummary` 运行/历史
   视图」两套渲染。
2. **运行态非实时（详情页）**：详情页 5s 轮询，不接 SSE（PRD 未要求详情页实时）；动态运行中的 automation run 走
   `RunningCard` SSE，与对话/任务同实时。短于一轮的运行在详情页可能不可见（PRD 已接受，≤5s 阈值）。
3. **stale queued 不重判**：`queued` 超时/进程重启残留以后端 run 状态为唯一准绳，前端不做超时重判（PRD known limitation）。
4. **排序梯度缺 created_at**：列表数据源 `AutomationItem` 无 `created_at`，比较器用 `last_run_at ?? next_run_at ?? 0`，
   省 created_at 段（§4.3）；规模化/严格排序前可在 list 补字段。
5. **契约债**：automations 全家桶原不在 OpenAPI、类型手写有漂移（`error_message`）。本版**已把** `is_running` 与
   `/v1/runs` automation 来源所需片段纳入「先补 OpenAPI」，并顺带登记/修正 `error_message` 漂移；list 的 `created_at`
   兜底字段仍记为未还的契约债。
6. **项目已删降级**（P2 known edge，§5.2）：依赖后端是否容错返回详情；本版纯前端降级，不改后端 DTO 形状。

---

## 验收映射（PRD §验收 → 落点）

| 验收项 | 落点 |
|---|---|
| 从总览/菜单/动态点进详情页 | §1 入口（`onOpenDetail` + 菜单/动态条目 `navigate`） |
| 详情看到标题/指令原文/定时/历史列表（最近 20 条） | §2 详情加载（`get` + `listRuns` 默认 20），复用 `ExecutionLog` |
| 全局标「全局」/项目标「项目」+项目名 | §5（`project_kind` / `project_name`） |
| 项目自动化「回到项目」 | §5（`navigate('/projects/:id')`，`id: "project-detail"`） |
| 编辑 = 现有弹窗预填（含 manual 不被破坏） | §2.1（复用 `CreateAutomationDialog` + `initial`，manual round-trip 方案 a） |
| 马上运行（含并发禁用/409 兜底） | §2.2（`isAutomationRunning` disabled + 409 toast） |
| 暂停/恢复/删除（删除二次确认 + 按来源跳回） | §2（`pause/resume/delete`）+ §5.1（全局→`/automations`、项目→`/projects/:id`） |
| 详情/菜单/动态三处显示「正在运行」 | §3（`isAutomationRunning`：详情=`listRuns[0]`、菜单=`is_running`、动态=「自动化」Tab `is_running` + Running 组 `source_kind=automation` `RunSummary`） |
| 动态有「自动化」筛选 + 每条恒一行 | §4.1（`SourceFilter`/`FILTERS` + 路径 A：`listGroups` 的 `AutomationItem` 独立 Tab，含未运行行） |
| 菜单/动态运行中置顶 + 同序 | §4.3（共用归一比较器：isRunning→activeTs→id） |
| 空列表「暂无执行记录」/失败原因 | §2/§7（`EmptyState` + `error_message_key`/`error_code`） |
| 权限（owner-scoped）非 owner 403/404 | 后端 owner-scoped，详情页统一无权/找不到态（PRD 权限矩阵，前端不另放操作按钮） |
| 项目被删降级 | §5.2（P2 known edge：可解析则展+隐藏回项目，不可解析则找不到/不可用、列表跳过） |
