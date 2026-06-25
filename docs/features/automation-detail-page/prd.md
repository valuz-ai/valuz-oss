# PRD：自动化任务独立详情页

> 状态：PRD 终稿（PM 裁决采用设计 #2「允许最小只读契约新增」；review 两轮 P0/P1 全收口）· Owner：产品 · 最后更新：2026-06-25

## What / Why

**What**：给每个自动化一个独立详情页（`/automations/:automationId`），一屏看清它的标题、指令、定时、操作和**它自己**最近的运行记录，并能看到它此刻是否在跑；同时让自动化在「菜单（最近列表）」和「动态（Activity）」里成为一等公民，能像对话/任务一样显示「正在运行」并参与置顶。

**解决谁的什么问题**：重度用户（自动化的**创建者本人**）配了多条自动化后，想确认某一条"今天跑了没、成功没、上次输出了什么、现在是不是在跑"。

**现状痛点**：全局总览页 `AutomationPage` 把所有项目的自动化挤在多张 `ScheduledTaskTable` 里，执行记录是跨全部自动化聚合的 `ExecutionLog`（每条只取最近 3 条混在一起），点一行只弹 `CreateAutomationDialog` 编辑，没有"详情"这一层，更无法聚焦单条看它自己的历史与运行态；自动化也完全不出现在菜单/动态里——运行中也看不见。

**衡量标准**：用户可从总览/菜单/动态任一入口直达某条自动化详情页；运行中状态在详情/菜单/动态三处一致、最大延迟 ≤ 5s；创建者能在详情页完成查看与全部操作。

## 范围与 API 决策（P0｜先拍板，下面全部据此）

- **不改调度/执行核心逻辑**；但**允许最小只读契约新增（契约先行，先改 `api/openapi.yaml`，再后端，再前端）**：
  - 自动化 list/detail 响应回填 **`is_running`**（或 `latest_run` 摘要）只读字段，供菜单/动态判定，避免前端 N×`listRuns` 轮询。
  - Activity 数据源 `/v1/runs` 新增 **`automation`** 来源类型（与现有 `assistant`/`project_chat`/`task` 并列）。
- **直接后果**：菜单/动态可低成本拿到每条自动化的运行态与排序时间，三处复用同一判定与同一比较器，无需逐条 `listRuns`。
- **不做**：跨设备通知/推送；历史筛选/搜索/分页加载更多/导出；产物（`created_files`）在线预览；重做 `AutomationPage` 版式；新建自动化新表单；**`queued` 超时/进程重启残留的超时重判**（见运行态映射表 known limitation）。

## 用户故事

- 作为**创建者**，我点开自己的一条自动化，能看到它的指令、下次运行时间和**它自己最近的运行历史**，不用在聚合日志里大海捞针。
- 作为**创建者**，项目自动化的详情页里我能一键回到所属项目。
- 作为**创建者**，当我的某条自动化正在跑，我在详情页、菜单、动态三处都能看到它标「正在运行」，和对话/任务一个待遇。
- 作为**创建者**，菜单和动态里正在执行的对话/任务/自动化总排在最前，我一眼找到在跑的东西。

## 详情页交互

**顶部**：标题（`name`）＋归属徽章（见「全局 vs 项目」）＋自动化状态（`status` 启用/暂停）＋运行态徽章（见「运行态判定」）＋操作：编辑、马上运行（`runNow`，仅 `status=enabled` 且 `agent_name!=null` 可点）、暂停/恢复（`pause`/`resume`）、删除（`delete`，`DeleteConfirmDialog` 二次确认）。
**主体**：指令原文（`prompt_template`）｜定时（`trigger_human_readable` ＋下次 `next_run_at`、上次 `last_run_at`）｜**最近 N 条运行列表**（`ExecutionLog`，点行 → `session_id` 对应 `/conversation/:id`）。
**全状态**：加载用 `PageLoader`、列表区单独 loading；无 run → `EmptyState`「暂无执行记录」；失败行红色态展示 `error_message_key`（本地化优先）/`error_message`/`error_code`；`skipped`/`interrupted_by_shutdown` 用中性灰区分。

### 运行态判定（P0｜唯一真相源 + 映射表 + 冲突优先级）

- **唯一真相源**：该自动化「**最新一次 run**」的状态。取法：后端回填的 `is_running`/`latest_run` 摘要（首选）；退化时取 `listRuns(id)` 按 `triggered_at` 倒序的 **[0]**。
- **唯一判定函数 `isAutomationRunning(latestRun)`，详情/菜单/动态三处必须复用同一份**：
  1. `latest_run.status ∈ {queued, running}` → **运行中**（`run.status` 优先，`task_status` 不参与）。
  2. 否则若 task 型且 `latest_run.task_status == active` → **运行中**（run 行已结算但 task 仍活跃）。
  3. 否则 → 非运行中。
- **冲突优先级**：`run.status` 优先——`queued/running` 直接判运行中；**run 成功结束后**再看 `task_status`（`active`=运行中，`paused`=已暂停，不算运行中）。

**运行态映射表（`run.status` × `task_status` → 展示态）**

| run.status | task_status | isRunning() | 展示态 |
|---|---|---|---|
| queued | 任意 | 是 | 运行中（脉冲） |
| running | 任意 | 是 | 运行中（脉冲） |
| success | active | 是 | 运行中（脉冲） |
| success | paused | 否 | 已暂停 |
| success | 无（chat 型/非 task） | 否 | 成功 |
| failed | 任意 | 否 | 失败（红，展 `error_code`/`error_message`） |
| skipped | 任意 | 否 | 跳过（中性灰） |
| interrupted_by_shutdown | 任意 | 否 | 中断（中性灰） |

> **Known limitation**：`queued` 超时或进程重启后残留的 stale run，本版**不做**前端超时重判，以后端 run 状态为唯一准绳；后端清理 stale run 后状态自动回正。

- **刷新**：页面可见时 **5s 轮询**（对齐 `AutomationPage`），页面隐藏暂停轮询；`runNow`/`pause`/`resume` 后立即刷新一次。**运行态最大延迟 ≤ 5s。**
- **复用约束**：详情/菜单/动态三处共用同一 `isAutomationRunning()` 与同一映射表，禁止各页自行实现。

### 全局 vs 项目归属（P1｜以 `project_kind` 为唯一判别基准）

判定**只看 `project_kind`**：`project=="project"` → 「项目」；`=="chat"` → 「全局」（**即使带 `project_id`/`project_name` 也判全局**，用户语义即不归属具体项目）。`agent_kind` **仅作"绑定来源"小字标注**（library_agent / project_member），不参与判定；`action_kind` 不参与归属判定，仅决定运行态读取方式（task → 看 `task_status`，chat → 仅看 `status`）。

**全局 vs 项目映射表（`project_kind` × `action_kind`）**

| project_kind | action_kind | 判定 | 徽章文案 | 展项目名 | 「回到项目」导航 | 点击目标 |
|---|---|---|---|---|---|---|
| chat | chat | 全局 | 全局 | 否 | 否 | `/automations/:id`（无项目跳转） |
| project | chat | 项目 | 项目 | 是（`project_name`） | 是（跳 `project_id`） | `/automations/:id` |
| project | task | 项目 | 项目 | 是（`project_name`） | 是（跳 `project_id`） | `/automations/:id` |
| chat | task | — | 不存在 | — | — | 后端拒绝（该组合不产生数据，标注，前端无需处理） |

### 菜单与动态新增「自动化」类型 + 置顶排序（P0）

**列表粒度（拍板）**：**每条自动化各占一行，恒显示**（即使从未运行也在列，区别于对话/任务"有会话才显示"）；**点该行 → 进入该自动化详情页**。**否决"单条聚合入口"方案**（聚合入口无法直达任一自动化）。

- **动态（`ActivityPage`）**：新增「自动化」来源类型与筛选 Tab，列出全部自动化**各一行**；运行中的进入顶部「Running」组并显示「正在运行」；空历史行仍显示，状态「未运行」。
- **菜单（`ConversationsHomePage` 最近列表 / 侧边 Recents）**：列表中新增自动化条目，**每条一行恒显示**；点击进入自动化详情页。

**置顶排序（P0，菜单与动态共用同一比较器）**：
1. **先按 `isAutomationRunning()` 分两组**，运行中组整体置顶。
2. **组内按活跃时间戳倒序**，时间戳取法：对话取最后活跃/消息时间；任务取 `last_active`；自动化取 `last_run_at` →（无）`next_run_at` →（再无）`created_at`。
3. 同为 running 的三类对象**混排**，同样按该活跃时间戳倒序。
4. **tie-breaker**：时间戳相同时按 `id` 升序稳定排序。

### 权限矩阵（P1｜owner-scoped）

后端严格 **owner-scoped**，本版**不**做"项目其他成员可见/可操作"：

| 操作 | 创建者(owner) | 非 owner |
|---|---|---|
| 查看详情 / runNow / pause / resume / delete / update | ✓（runNow 另需 `status=enabled` 且 `agent_name!=null`） | ✗ |

- 非 owner 访问 `/automations/:automationId`：后端按 user_id 过滤查无 → 详情页显示统一「无权限/找不到」态（**403/404**，不区分"不存在/无权"，避免信息泄露），无任何操作按钮。

### 异常边界（P2）

- `agent_name == null`（绑定 Agent 已删除）：顶部标注「绑定的 Agent 已删除」；**编辑、马上运行 禁用**（按钮 disabled）；**暂停/恢复、删除 仍可用**（便于清理失效自动化）。
- `runNow` 失败 → **错误 toast**，展示 `error_code` / `message`。
- 删除成功 → **跳回来源**：全局自动化 → `/automations`；项目自动化 → 所属项目详情（`project_id`）。

## 验收标准（Given / When / Then，可勾验）

> 通用前提：登录用户为该自动化**创建者本人**；运行态轮询最大延迟 ≤ **5s**。

- [ ] **进入详情**：Given 一条自动化 `A`（`name="每日简报"`）在总览/菜单/动态；When 点击该行；Then 路由切到 `/automations/A`，顶部可见 `name=每日简报`、归属徽章、`status`。
- [ ] **详情内容**：Given `A` 有 ≥1 次 run；When 打开详情；Then 可见指令原文 `prompt_template`、`trigger_human_readable`＋`next_run_at`、最近 **N=20** 条运行列表（`listRuns` 默认 limit=20，按 `triggered_at` 倒序）。
- [ ] **归属徽章（依映射表）**：Given `project_kind=project` 且 `project_name="X 项目"`；Then 徽章「项目」+ 展示 `X 项目` +「回到项目」可点（→ `project_id`）。Given `project_kind=chat`（带或不带 `project_id`）；Then 徽章「全局」、不展示项目名、无「回到项目」。
- [ ] **编辑**：When 点「编辑」；Then 打开 `CreateAutomationDialog`（编辑模式），`name`/`prompt_template`/`agent_slug`/`trigger`/`action_kind` 已预填。
- [ ] **操作反馈**：When 点 pause/resume/runNow/delete；Then 成功 toast 出现，详情页 ≤5s 内反映：pause→`status=paused`、resume→`status=enabled`、runNow→最新 run 出现且标「正在运行」（`status=queued/running`，脉冲）、delete→二次确认后按来源跳回（全局→`/automations`，项目→项目详情）。runNow 失败 → 错误 toast 展 `error_code`/`message`。
- [ ] **运行态（run.status 优先）**：Given 最新 run `status=running`（task 型 `task_status=active`）；Then 详情/菜单/动态三处均脉冲「正在运行」，`isAutomationRunning()` 返回 `true`，≤5s 同步。Given `status=success` 且 `task_status=paused`；Then 三处均显示「已暂停」，`isAutomationRunning()` 返回 `false`。
- [ ] **失败/跳过/中断**：Given 最新 run `status=failed`；Then 该行红色态并展示 `error_message_key`/`error_message`/`error_code` 之一。Given `status=skipped`/`interrupted_by_shutdown`；Then 中性灰「跳过」/「中断」。Given 无任何 run；Then 列表区显示「暂无执行记录」。
- [ ] **动态 Tab 与恒显示**：Given 账户有 2 条自动化、其中 1 条从未运行；When 在 Activity 选「自动化」Tab；Then 两条**各占一行**均出现（未运行那条状态「未运行」）；点任一行进入对应详情页。
- [ ] **置顶排序**：Given 菜单/动态中存在运行中与非运行中的对话/任务/自动化各若干；When 渲染列表；Then 运行中的全部排在最前；组内按活跃时间戳倒序（自动化取 `last_run_at`→`next_run_at`→`created_at`）；时间戳相同按 `id` 升序；菜单与动态顺序一致（共用比较器）。
- [ ] **权限（owner-scoped）**：Given 非 owner（或不存在的 `automationId`）；When 访问 `/automations/:id`；Then 返回 403/404 无权限态，无任何操作按钮。
- [ ] **Agent 删除态**：Given `agent_name=null`；Then 顶部标「绑定的 Agent 已删除」，编辑/马上运行 禁用，暂停/恢复/删除 可用。

## 复用与依赖

**复用（勿重造）**：`automationsApi` 的 `get`/`listRuns`/`runNow`/`pause`/`resume`/`delete`/`update`（`frontend/packages/core/src/api/automations-api.ts`）——`listRuns` 默认 `limit=20`（后端 `automations.py` 已核实）；`ExecutionLog`（`onSessionClick`）、`StatusPill`、`EmptyState`、`PageLoader`、`DeleteConfirmDialog`、`CreateAutomationDialog`（编辑模式 `initial`）；运行态映射沿用 `AutomationPage` 的 `runToLogStatus`（按上表对齐 `paused` 语义）。
**需新增**：路由 `/automations/:automationId`（走 `desktop-routes` registry + `route-registry`，不硬编码 router）；**只读契约新增（契约先行）** `is_running`/`latest_run` 字段与 `/v1/runs` 的 `automation` 来源类型；统一 `isAutomationRunning()` 判定与统一置顶比较器（菜单/动态/详情共用）；动态/菜单「自动化」来源类型与置顶排序逻辑。
**不做（本版砍掉）**：改后端调度/执行核心；重做 `AutomationPage` 版式；新建自动化新表单；历史筛选/搜索/分页/导出；产物 `created_files` 在线预览；`queued`/进程重启 stale run 的前端超时重判；"项目其他成员可见/可操作"。
