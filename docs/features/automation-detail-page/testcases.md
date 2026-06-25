## E2E 用例（全栈测试）

### 说明

- 阶段：P2，仅设计测试用例，不执行。
- 测试范围：自动化独立详情页 `/automations/:automationId`、全局总览入口、菜单最近列表、动态 Activity 列表，以及详情页上的编辑、删除、马上运行、暂停/恢复操作。
- 主要接口：
  - `GET /v1/automations/{automation_id}`
  - `GET /v1/automations/{automation_id}/runs?limit=20`
  - `PATCH /v1/automations/{automation_id}`
  - `DELETE /v1/automations/{automation_id}`
  - `POST /v1/automations/{automation_id}/run-now`
  - `POST /v1/automations/{automation_id}/pause`
  - `POST /v1/automations/{automation_id}/resume`
  - `GET /v1/automations`
  - `GET /v1/runs?status=running`
  - `GET /v1/runs?status=finished`
- 主要数据表：`valuz_automation`、`valuz_automation_run`、项目表、项目成员表、会话/任务运行记录表。
- 错误响应约定：业务错误返回 `{ "error": { "code": number, "message": string } }`；请求体校验错误返回 HTTP 422 `detail`。

### 通用测试数据

| 数据ID | 用途 | 数据要求 |
| --- | --- | --- |
| `AUTO_G` | 全局自动化 | `project_kind="chat"`、`agent_kind="library_agent"`、`status="enabled"`、有 `prompt_template`、`trigger_human_readable`、`next_run_at`、`last_run_at`，至少 3 条历史 run。 |
| `AUTO_P` | 项目自动化 | `project_kind="project"`、`agent_kind="project_member"`、`status="enabled"`、有关联 `project_id/project_name`，至少 2 条历史 run。 |
| `AUTO_EMPTY` | 无历史运行记录 | `status="enabled"`，`total_runs=0`，`listRuns` 返回空数组。 |
| `AUTO_PAUSED` | 已暂停自动化 | `status="paused"`，`next_run_at=null`。 |
| `AUTO_RUNNING_CHAT` | 聊天型运行中自动化 | 最新 `valuz_automation_run.status` 为 `queued` 或 `running`。 |
| `AUTO_RUNNING_TASK` | 任务型运行中自动化 | 最新 run 可为 `success`，但 `task_status` 为 `active` 或 `paused`。 |
| `AUTO_FAILED` | 有失败运行记录 | 最新或历史 run 为 `failed`，带 `error_message_key`、`error_message` 或 `error_code`。 |
| `AUTO_AGENT_DELETED` | 上游 Agent 被删除 | 自动化行保留，接口返回 `agent_name=null`。 |

### 正常流程

#### AUTO-DTL-E2E-001 打开全局自动化详情页展示核心信息

- 前置条件：
  - 使用 `AUTO_G`。
- 步骤：
  1. 从浏览器打开 `/automations/{AUTO_G.automation_id}`。
  2. 等待详情页加载完成。
- 预期结果：
  - 页面展示自动化标题、类型标识、启用状态、指令原文、定时信息、历史运行记录列表和操作按钮。
- UI 验证点：
  - 标题等于 `AUTO_G.name`。
  - 类型徽章显示“全局”。
  - 不展示项目名称和“回到项目”导航。
  - 指令内容完整展示 `prompt_template` 原文，不被截断为摘要。
  - 定时区展示 `trigger_human_readable`、下次运行时间、上次运行时间。
  - 历史列表展示最新 20 条以内 run，包含触发方式、状态、时间、耗时、摘要或错误信息。
- 数据/日志验证点：
  - `GET /v1/automations/{id}` 返回 200，响应字段包含 `name/project_kind/agent_kind/prompt_template/trigger_human_readable/next_run_at/last_run_at/total_runs/recent_failures`。
  - `GET /v1/automations/{id}/runs?limit=20` 返回 200，`runs[].automation_id` 均等于 `AUTO_G.automation_id`。
  - 后端 access log 有上述两个 GET 请求，状态码均为 200。

#### AUTO-DTL-E2E-002 打开项目自动化详情页展示项目信息和回到项目

- 前置条件：
  - 使用 `AUTO_P`，所属项目存在且用户有权限访问。
- 步骤：
  1. 打开 `/automations/{AUTO_P.automation_id}`。
  2. 点击“回到项目”。
- 预期结果：
  - 详情页展示项目自动化标识和项目名称。
  - 点击导航后进入对应项目详情页。
- UI 验证点：
  - 类型徽章显示“项目”。
  - 展示 `AUTO_P.project_name`。
  - 展示“回到项目”导航。
  - 点击后 URL 跳转到该 `project_id` 对应项目路由，项目页标题与 `project_name` 一致。
- 数据/日志验证点：
  - `GET /v1/automations/{id}` 返回 `project_kind="project"`、`project_id=AUTO_P.project_id`、`project_name=AUTO_P.project_name`。
  - 跳转过程不触发自动化数据写入；`valuz_automation.updated_at` 不因导航变化。

#### AUTO-DTL-E2E-003 从全局总览进入自动化详情页

- 前置条件：
  - 全局总览 `/automations` 中存在 `AUTO_G` 和 `AUTO_P`。
- 步骤：
  1. 打开 `/automations`。
  2. 点击 `AUTO_G` 所在行。
  3. 返回 `/automations` 后点击 `AUTO_P` 所在行。
- 预期结果：
  - 点击每条自动化均进入对应独立详情页。
- UI 验证点：
  - 点击 `AUTO_G` 后 URL 为 `/automations/{AUTO_G.automation_id}`，标题为 `AUTO_G.name`。
  - 点击 `AUTO_P` 后 URL 为 `/automations/{AUTO_P.automation_id}`，标题为 `AUTO_P.name`。
  - 行内编辑入口仍可通过操作菜单进入，不与行点击详情行为冲突。
- 数据/日志验证点：
  - 总览页先请求 `GET /v1/automations`。
  - 进入详情页后请求对应 `GET /v1/automations/{id}` 和 `GET /v1/automations/{id}/runs`。

#### AUTO-DTL-E2E-004 从菜单最近列表进入自动化详情页

- 前置条件：
  - 菜单最近列表已展示自动化条目，至少包含 `AUTO_G`。
- 步骤：
  1. 打开新建对话页或侧边 Recents。
  2. 找到 `AUTO_G` 自动化条目。
  3. 点击该条目。
- 预期结果：
  - 自动化作为独立类型条目出现，点击进入详情页。
- UI 验证点：
  - 菜单条目展示自动化名称和自动化类型标识。
  - 点击后 URL 为 `/automations/{AUTO_G.automation_id}`。
  - 详情页标题和菜单条目名称一致。
- 数据/日志验证点：
  - 菜单数据源包含 `AUTO_G.automation_id`、`name`、`updated_at/last_run_at`、运行态字段或可推导运行态字段。
  - 点击只触发详情 GET，不创建会话、不写入 `valuz_automation_run`。

#### AUTO-DTL-E2E-005 从动态 Activity 进入自动化详情页

- 前置条件：
  - Activity 列表已展示自动化条目，至少包含 `AUTO_P`。
- 步骤：
  1. 打开 `/activity`。
  2. 点击筛选 Tab “自动化”。
  3. 点击 `AUTO_P` 自动化条目。
- 预期结果：
  - Activity 支持自动化筛选，自动化条目可进入独立详情页。
- UI 验证点：
  - Tab 区包含“全部 / 对话 / 任务 / 自动化”。
  - “自动化” Tab 下展示 `AUTO_P.name`。
  - 点击后 URL 为 `/automations/{AUTO_P.automation_id}`。
- 数据/日志验证点：
  - Activity 列表数据包含自动化来源类型。
  - 自动化条目为 `AUTO_P` 的代表条目，不依赖必须存在运行历史。

#### AUTO-DTL-E2E-006 编辑成功：复用 CreateAutomationDialog 且字段预填

- 前置条件：
  - 使用 `AUTO_P`，当前状态为 `enabled`。
- 步骤：
  1. 打开 `/automations/{AUTO_P.automation_id}`。
  2. 点击“编辑”。
  3. 在弹窗中修改名称、指令内容和触发时间。
  4. 保存。
- 预期结果：
  - 复用现有自动化弹窗进入编辑模式。
  - 保存成功后详情页展示最新配置。
- UI 验证点：
  - 弹窗标题为编辑态，表单预填 `name/prompt_template/trigger/agent/action_kind`。
  - 保存时按钮进入 loading，成功后弹窗关闭。
  - 页面标题、指令区、定时区更新为新值。
  - 运行记录列表不被清空。
- 数据/日志验证点：
  - `PATCH /v1/automations/{id}` 返回 200。
  - `valuz_automation.name/prompt_template/trigger_kind/cron_expr/interval_seconds/timezone/action_kind/updated_at` 按修改更新。
  - `valuz_automation_run` 历史记录数量不变。
  - access log 记录 PATCH 200。

#### AUTO-DTL-E2E-007 暂停成功：状态切换和马上运行禁用

- 前置条件：
  - 使用 `AUTO_G`，`status="enabled"`。
- 步骤：
  1. 打开详情页。
  2. 点击“暂停”。
- 预期结果：
  - 自动化切换为暂停态，后续不会按计划触发。
- UI 验证点：
  - 顶部状态显示“暂停”。
  - 操作按钮从“暂停”变为“恢复”。
  - “马上运行”按钮禁用或不可点击。
  - 定时区下次运行显示为空、无下次运行或等价暂停文案。
- 数据/日志验证点：
  - `POST /v1/automations/{id}/pause` 返回 200。
  - `valuz_automation.status="paused"`，`next_run_at=null`，`updated_at` 更新。
  - 不新增 `valuz_automation_run`。

#### AUTO-DTL-E2E-008 恢复成功：重新计算下次运行

- 前置条件：
  - 使用 `AUTO_PAUSED`。
- 步骤：
  1. 打开详情页。
  2. 点击“恢复”。
- 预期结果：
  - 自动化恢复启用，重新计算下次运行时间。
- UI 验证点：
  - 顶部状态显示“启用”。
  - 操作按钮从“恢复”变为“暂停”。
  - “马上运行”按钮可点击。
  - 若触发器是 cron/interval，定时区展示新的下次运行时间；manual 触发器允许 `next_run_at` 为空。
- 数据/日志验证点：
  - `POST /v1/automations/{id}/resume` 返回 200。
  - `valuz_automation.status="enabled"`。
  - cron/interval 自动化的 `next_run_at` 大于等于恢复时刻；manual 自动化的 `next_run_at=null`。

#### AUTO-DTL-E2E-009 马上运行成功：创建 queued 运行记录并刷新状态

- 前置条件：
  - 使用 `AUTO_G`，`status="enabled"`，最新 run 非 `queued/running`。
- 步骤：
  1. 打开详情页。
  2. 点击“马上运行”。
  3. 等待详情页自动轮询一次。
- 预期结果：
  - 系统接受立即运行请求，并在历史列表展示新运行记录。
- UI 验证点：
  - 点击后出现成功提示或按钮短暂 loading。
  - 顶部状态出现“正在运行”。
  - 历史列表第一行新增 `manual` 触发类型记录，状态为 pending/运行中样式。
  - 不需要手动刷新即可在 5s 左右看到状态变化。
- 数据/日志验证点：
  - `POST /v1/automations/{id}/run-now` 返回 202，响应包含 `run_id/automation_id/status="queued"`。
  - `valuz_automation_run` 新增一行：`automation_id=AUTO_G.id`、`trigger_type="manual"`、`status="queued"`。
  - 后续 `GET /v1/automations/{id}/runs` 返回该 `run_id` 且位于第一条。
  - 后端发布或记录 `automation.run.queued`，access log 记录 POST 202。

#### AUTO-DTL-E2E-010 删除成功：二次确认后删除自动化及运行历史

- 前置条件：
  - 准备一条可删除自动化 `AUTO_DELETE_OK`，至少有 1 条 run。
- 步骤：
  1. 打开 `/automations/{AUTO_DELETE_OK.automation_id}`。
  2. 点击“删除”。
  3. 在 `DeleteConfirmDialog` 点击取消。
  4. 再次点击“删除”并确认。
- 预期结果：
  - 取消时不删除；确认后自动化被删除并离开详情页。
- UI 验证点：
  - 删除前出现二次确认弹窗，展示自动化名称。
  - 点击取消后弹窗关闭，详情页仍可用。
  - 确认删除成功后跳转到 `/automations` 或上一层安全页面，列表中不再出现该自动化。
- 数据/日志验证点：
  - 取消阶段不触发 DELETE。
  - 确认阶段 `DELETE /v1/automations/{id}` 返回 204。
  - `valuz_automation` 中该 id 不存在。
  - `valuz_automation_run` 中该 `automation_id` 的记录被级联删除。

#### AUTO-DTL-E2E-011 历史运行记录按状态正确渲染

- 前置条件：
  - 使用一条包含多状态运行记录的自动化：`success`、`failed`、`skipped`、`interrupted_by_shutdown`、`queued`、`running`。
- 步骤：
  1. 打开详情页。
  2. 查看历史运行记录列表。
- 预期结果：
  - 不同状态的运行记录使用正确视觉样式和文案。
- UI 验证点：
  - `success` 显示成功态。
  - `failed` 显示红色错误态。
  - `queued/running` 显示 pending/运行中样式。
  - `skipped/interrupted_by_shutdown` 显示中性灰态。
  - 触发类型展示 cron/interval/manual/recovered_skip 的对应文案。
- 数据/日志验证点：
  - `GET /runs` 返回的 `status/trigger_type/duration_ms/result_summary/error_*` 与 UI 行一一对应。
  - 列表排序按 `triggered_at` 倒序。

#### AUTO-DTL-E2E-012 运行记录可跳转到对应会话

- 前置条件：
  - 使用一条 run，`session_id` 非空且会话仍存在。
- 步骤：
  1. 打开详情页。
  2. 点击该运行记录中的会话/任务名称链接。
- 预期结果：
  - 页面跳转到该 run 产生的会话页。
- UI 验证点：
  - 有 `session_id` 的行展示可点击入口。
  - 点击后进入 `/conversation/{session_id}`。
  - `session_id=null` 的行不展示可点击链接。
- 数据/日志验证点：
  - `GET /runs` 中该行 `session_id` 等于跳转目标。
  - 点击不修改 `valuz_automation_run`。

#### AUTO-DTL-E2E-013 详情页自动轮询刷新运行态

- 前置条件：
  - 使用 `AUTO_RUNNING_CHAT`，可在测试中将最新 run 从 `queued` 推进到 `running/success`。
- 步骤：
  1. 打开详情页，记录当前顶部状态和历史第一行状态。
  2. 在后端将最新 run 状态推进到 `running`，再推进到 `success`。
  3. 不刷新浏览器，等待两个 5s 轮询周期。
- 预期结果：
  - 详情页自动反映最新状态。
- UI 验证点：
  - `queued/running` 时顶部显示“正在运行”。
  - `success` 后顶部“正在运行”消失，历史行变为成功态。
- 数据/日志验证点：
  - 轮询请求为 `GET /v1/automations/{id}/runs`。
  - 每次响应中的最新 run 状态与 UI 一致。

### 运行态与排序

#### AUTO-DTL-E2E-014 聊天型自动化运行中状态三处展示

- 前置条件：
  - 使用 `AUTO_RUNNING_CHAT`，最新 run 的 `status` 为 `queued` 或 `running`。
- 步骤：
  1. 打开该自动化详情页。
  2. 打开菜单最近列表。
  3. 打开 `/activity`。
- 预期结果：
  - 详情页、菜单、动态三处均展示“正在运行”。
- UI 验证点：
  - 详情页顶部显示脉冲 `StatusPill`“正在运行”，历史第一行显示 pending/运行中样式。
  - 菜单自动化条目显示“正在运行”。
  - Activity 自动化条目进入 Running 区或列表顶部，并显示“正在运行”。
- 数据/日志验证点：
  - `GET /v1/automations/{id}/runs` 最新 run 为 `queued/running`。
  - 菜单/Activity 的运行态数据源能定位到同一个 `automation_id`。

#### AUTO-DTL-E2E-015 任务型自动化以 task_status 判定运行中

- 前置条件：
  - 使用 `AUTO_RUNNING_TASK`，最新 run 的 `status="success"`，但 `task_status="active"` 或 `task_status="paused"`。
- 步骤：
  1. 打开详情页。
  2. 打开菜单最近列表。
  3. 打开 Activity。
- 预期结果：
  - 即使 run 本身已标记 `success`，只要任务仍 active/paused，三处仍按运行中展示。
- UI 验证点：
  - 详情页顶部展示“正在运行”。
  - 历史行显示 pending/运行中样式，而不是成功态。
  - 菜单和 Activity 条目均显示“正在运行”。
- 数据/日志验证点：
  - `GET /runs` 返回该 run 的 `task_status` 为 `active` 或 `paused`。
  - UI 判定优先使用 `task_status`，不是仅依赖 `run.status`。

#### AUTO-DTL-E2E-016 菜单最近列表运行中条目置顶排序

- 前置条件：
  - 菜单最近列表包含正在运行的对话、任务、`AUTO_RUNNING_CHAT`，以及若干未运行条目。
- 步骤：
  1. 打开菜单最近列表或侧边 Recents。
  2. 记录列表顺序。
- 预期结果：
  - 所有正在执行的对话、任务、自动化排在最前；其他条目按时间倒序。
- UI 验证点：
  - 运行中的自动化不被普通最近对话压到下面。
  - 置顶区内每个运行中条目都有“正在运行”标识。
  - 非运行条目之间按 `updated_at/last_run_at` 倒序。
- 数据/日志验证点：
  - 菜单数据中运行中条目的运行态字段为 true 或可由 run 状态推导为 true。
  - 排序结果满足：所有 `is_running=true` 条目索引小于任意 `is_running=false` 条目。

#### AUTO-DTL-E2E-017 Activity 运行中条目置顶排序

- 前置条件：
  - Activity 同时存在正在运行的对话、任务、自动化，以及已完成历史条目。
- 步骤：
  1. 打开 `/activity`。
  2. 查看“全部”Tab。
  3. 切换到“自动化”Tab。
- 预期结果：
  - 正在执行条目进入顶部 Running 区；自动化 Tab 中运行中自动化排在最前。
- UI 验证点：
  - “全部”Tab 顶部 Running 区包含运行中自动化。
  - “自动化”Tab 下运行中自动化位于普通自动化之前。
  - 历史区不混入运行中条目。
- 数据/日志验证点：
  - `GET /v1/runs?status=running` 或自动化运行态数据源返回运行中自动化。
  - finished/history 数据源中的条目不重复出现在 Running 区。

#### AUTO-DTL-E2E-018 Activity 中自动化总是显示代表条目

- 前置条件：
  - 使用 `AUTO_EMPTY`，从未运行。
- 步骤：
  1. 打开 `/activity`。
  2. 切换到“自动化”Tab。
- 预期结果：
  - 自动化即使从未运行，也在 Activity 中显示一条代表条目。
- UI 验证点：
  - “自动化”Tab 下可看到 `AUTO_EMPTY.name`。
  - 条目不显示错误态，不显示“正在运行”。
  - 点击条目进入详情页，详情页显示空运行记录。
- 数据/日志验证点：
  - 自动化列表数据包含 `AUTO_EMPTY`。
  - `GET /v1/automations/{id}/runs` 返回 `runs=[]`。

#### AUTO-DTL-E2E-019 菜单最近列表中自动化总是显示代表条目

- 前置条件：
  - 使用 `AUTO_EMPTY`。
- 步骤：
  1. 打开新建对话页或侧边 Recents。
  2. 查找 `AUTO_EMPTY` 条目。
- 预期结果：
  - 从未运行的自动化也在菜单最近列表中显示一条代表条目。
- UI 验证点：
  - 条目展示自动化名称和自动化类型。
  - 不显示“正在运行”。
  - 点击后进入 `/automations/{AUTO_EMPTY.automation_id}`。
- 数据/日志验证点：
  - 菜单数据中存在 `AUTO_EMPTY.automation_id`。
  - 不因没有 run 而过滤掉该自动化。

### 边界与空/加载状态

#### AUTO-DTL-E2E-020 无运行记录空状态

- 前置条件：
  - 使用 `AUTO_EMPTY`。
- 步骤：
  1. 打开详情页。
  2. 等待运行记录区域加载完成。
- 预期结果：
  - 详情页正常展示自动化配置，历史列表显示空状态。
- UI 验证点：
  - 列表区域展示 `EmptyState` 文案“暂无执行记录”。
  - 不展示空白表格、不展示错误态。
  - 操作按钮仍可用。
- 数据/日志验证点：
  - `GET /v1/automations/{id}` 返回 200，`total_runs=0`。
  - `GET /v1/automations/{id}/runs` 返回 200，`runs=[]`。

#### AUTO-DTL-E2E-021 详情页加载态和历史列表独立加载态

- 前置条件：
  - 使用网络拦截或测试后端让 `GET detail` 延迟，让 `GET runs` 单独延迟。
- 步骤：
  1. 打开详情页，并观察首屏。
  2. detail 返回后继续延迟 runs。
  3. runs 返回后观察页面。
- 预期结果：
  - 页面加载和历史列表加载互不误导。
- UI 验证点：
  - detail 未返回时显示 `PageLoader`。
  - detail 已返回但 runs 未返回时，标题、指令、定时先展示，历史区域显示单独 loading。
  - runs 返回后 loading 消失并展示列表或空状态。
- 数据/日志验证点：
  - detail 和 runs 是两个独立 GET 请求。
  - 任一请求延迟期间不触发写接口。

#### AUTO-DTL-E2E-022 自动化被暂停态

- 前置条件：
  - 使用 `AUTO_PAUSED`。
- 步骤：
  1. 打开详情页。
  2. 尝试点击“马上运行”。
  3. 点击“恢复”。
- 预期结果：
  - 暂停态清楚展示，并阻止马上运行。
- UI 验证点：
  - 顶部状态为“暂停”。
  - “马上运行”按钮禁用。
  - “恢复”可用。
  - 恢复成功后“马上运行”变为可用。
- 数据/日志验证点：
  - 初始 `GET detail` 返回 `status="paused"`、`next_run_at=null`。
  - 若前端禁用生效，不应发起 `POST /run-now`。
  - 恢复时 `POST /resume` 返回 200。

#### AUTO-DTL-E2E-023 上游 Agent 已删除

- 前置条件：
  - 使用 `AUTO_AGENT_DELETED`，接口返回 `agent_name=null`。
- 步骤：
  1. 打开详情页。
  2. 查看顶部或指令/执行信息区域。
  3. 尝试点击“编辑”。
- 预期结果：
  - 页面可打开，明确标注绑定的 Agent 已删除，用户可进入编辑修复。
- UI 验证点：
  - 展示“绑定的 Agent 已删除”。
  - 不因 `agent_name=null` 崩溃或显示 `null` 字符串。
  - 编辑弹窗可打开，并提示用户重新选择可用 Agent。
- 数据/日志验证点：
  - `GET detail` 返回 200，`agent_name=null`。
  - 若用户未保存，不修改 `valuz_automation.agent_slug`。

#### AUTO-DTL-E2E-024 manual 触发器边界展示

- 前置条件：
  - 准备一条 `trigger.kind="manual"` 的自动化，状态为 enabled。
- 步骤：
  1. 打开详情页。
  2. 查看定时区和操作按钮。
  3. 点击“马上运行”。
- 预期结果：
  - manual 自动化无计划下次运行，但可手动运行。
- UI 验证点：
  - 定时区展示 manual 或仅手动触发的清晰文案。
  - `next_run_at=null` 不显示为错误。
  - “马上运行”按钮可用。
- 数据/日志验证点：
  - `GET detail` 返回 `trigger.kind="manual"`、`next_run_at=null`。
  - `POST /run-now` 返回 202，并新增 `trigger_type="manual"` 的 run。

#### AUTO-DTL-E2E-025 长标题和长指令内容布局不溢出

- 前置条件：
  - 准备一条自动化：标题接近接口上限，`prompt_template` 包含多段长文本和长单词。
- 步骤：
  1. 分别在桌面宽屏、窄屏、移动宽度打开详情页。
  2. 查看标题区、操作区、指令区和历史列表。
- 预期结果：
  - 文本可读，页面布局不重叠。
- UI 验证点：
  - 标题可换行或截断，有完整 title/tooltip 或可见方式。
  - 操作按钮不遮挡标题和状态。
  - 指令原文区域可滚动或自动换行，长单词不撑破容器。
  - 历史列表的错误信息或摘要不与时间/状态重叠。
- 数据/日志验证点：
  - `GET detail` 返回完整字符串。
  - 页面展示不应依赖前端对原文做破坏性截断。

#### AUTO-DTL-E2E-026 运行记录默认条数与排序边界

- 前置条件：
  - 准备一条自动化，拥有超过 20 条 run，`triggered_at` 覆盖不同时间。
- 步骤：
  1. 打开详情页。
  2. 查看历史运行记录数量和顺序。
- 预期结果：
  - 首版按默认条数展示，不做筛选/搜索/加载更多。
- UI 验证点：
  - 列表最多展示默认返回条数。
  - 第一条为最新 run。
  - 页面不展示本版明确不做的筛选、搜索、分页加载更多和日志导出。
- 数据/日志验证点：
  - `GET /runs?limit=20` 返回最多 20 条。
  - 返回数据按 `triggered_at desc` 排序。

### 异常与错误码

#### AUTO-DTL-E2E-027 详情页自动化不存在

- 前置条件：
  - 使用不存在或已删除的 `automation_id`。
- 步骤：
  1. 打开 `/automations/not-exist-id`。
- 预期结果：
  - 页面展示可理解的不存在/加载失败状态，不白屏。
- UI 验证点：
  - 不展示空详情壳。
  - 提供返回总览或重试入口。
- 数据/日志验证点：
  - `GET /v1/automations/not-exist-id` 返回 404，`error.code=404711`，message 为 `Automation not found`。
  - 不继续发起无意义的 mutation 请求。

#### AUTO-DTL-E2E-028 运行记录加载失败

- 前置条件：
  - detail 请求成功，`GET /runs` 被模拟为 500 或网络失败。
- 步骤：
  1. 打开详情页。
  2. 等待 detail 成功展示。
  3. 观察历史列表区域。
- 预期结果：
  - 配置信息可用，历史列表单独展示失败态。
- UI 验证点：
  - 标题、指令、定时、操作按钮正常展示。
  - 历史列表区域显示加载失败提示和重试入口。
  - 不把整个详情页替换成全页错误。
- 数据/日志验证点：
  - `GET detail` 返回 200。
  - `GET runs` 返回 500 或网络错误。
  - 后端 error log 有对应异常；前端不发起写接口。

#### AUTO-DTL-E2E-029 编辑失败：空名称或空指令

- 前置条件：
  - 使用 `AUTO_G`。
- 步骤：
  1. 打开编辑弹窗。
  2. 将名称改为空白并保存。
  3. 恢复名称，将指令改为空白并保存。
- 预期结果：
  - 表单阻止无效保存，或后端返回明确 422 错误。
- UI 验证点：
  - 显示字段级错误或 toast。
  - 弹窗保持打开，用户输入不丢失。
  - 详情页原数据不被乐观覆盖。
- 数据/日志验证点：
  - 若请求到后端：空名称返回 422，`error.code=422713`；空指令返回 422，`error.code=422714`。
  - `valuz_automation.name/prompt_template/updated_at` 不变。

#### AUTO-DTL-E2E-030 编辑失败：非法 cron 或 interval 小于 30 秒

- 前置条件：
  - 使用 `AUTO_G`。
- 步骤：
  1. 打开编辑弹窗。
  2. 将 cron 改为非法表达式并保存。
  3. 将 interval 改为 29 秒并保存。
- 预期结果：
  - 后端拒绝非法触发器，前端展示失败原因。
- UI 验证点：
  - 显示 cron/interval 校验错误。
  - 弹窗保持打开。
  - 定时区仍显示保存前的触发器。
- 数据/日志验证点：
  - 非法 cron 返回 422，`error.code=422711`。
  - interval 小于 30 秒返回 422，可能为 FastAPI `detail` 校验错误或业务错误 `error.code=422712`。
  - `valuz_automation.trigger_kind/cron_expr/interval_seconds/next_run_at` 不变。

#### AUTO-DTL-E2E-031 编辑失败：任务模式绑定到全局自动化

- 前置条件：
  - 使用 `AUTO_G`，`project_kind="chat"`。
- 步骤：
  1. 打开编辑弹窗。
  2. 尝试将 `action_kind` 改为 `task` 并保存。
- 预期结果：
  - 系统拒绝聊天项目上的 task 模式。
- UI 验证点：
  - 若 UI 已禁用该选项，不能选择 task。
  - 若请求发出，显示“Task mode is only available for projects”或本地化等价文案。
  - 弹窗保持打开。
- 数据/日志验证点：
  - `PATCH /v1/automations/{id}` 返回 422，`error.code=422717`。
  - `valuz_automation.action_kind` 不变。

#### AUTO-DTL-E2E-032 编辑失败：项目成员 Agent 不存在

- 前置条件：
  - 使用 `AUTO_P`，准备一个不属于该项目的 `agent_slug`。
- 步骤：
  1. 打开编辑弹窗。
  2. 通过测试工具提交不属于项目的 `agent_slug`。
- 预期结果：
  - 后端拒绝跨项目或不存在成员绑定。
- UI 验证点：
  - 展示“Agent is not a member of this project”或本地化等价错误。
  - 弹窗保持打开。
- 数据/日志验证点：
  - `PATCH /v1/automations/{id}` 返回 404，`error.code=404714`。
  - `valuz_automation.agent_slug` 不变。

#### AUTO-DTL-E2E-033 删除失败：目标不存在或已被其他端删除

- 前置条件：
  - 打开一条自动化详情页后，在另一个客户端删除该自动化。
- 步骤：
  1. 当前页面点击“删除”。
  2. 在二次确认弹窗中确认。
- 预期结果：
  - 前端提示删除失败或目标已不存在，并提供返回总览。
- UI 验证点：
  - 删除 loading 结束。
  - 显示错误提示。
  - 不停留在不可恢复 loading 状态。
- 数据/日志验证点：
  - `DELETE /v1/automations/{id}` 返回 404，`error.code=404711`。
  - DB 中该自动化已经不存在；无重复删除副作用。

#### AUTO-DTL-E2E-034 马上运行失败：自动化已暂停

- 前置条件：
  - 使用 `AUTO_PAUSED`。
- 步骤：
  1. 通过 UI 或测试工具触发 `run-now`。
- 预期结果：
  - 暂停态不能立即运行。
- UI 验证点：
  - 正常 UI 下“马上运行”应禁用。
  - 若绕过 UI 发起请求，页面展示运行失败提示。
- 数据/日志验证点：
  - `POST /v1/automations/{id}/run-now` 返回 409，`error.code=409711`。
  - 不新增 `valuz_automation_run`。

#### AUTO-DTL-E2E-035 马上运行失败：已有 queued 运行

- 前置条件：
  - 使用自动化 `AUTO_ALREADY_QUEUED`，最新 run `status="queued"`。
- 步骤：
  1. 打开详情页。
  2. 连续点击“马上运行”或通过接口再次触发。
- 预期结果：
  - 系统单飞保护生效，不重复入队。
- UI 验证点：
  - 显示已有运行排队中的错误提示。
  - 顶部继续显示“正在运行”。
  - 历史列表不新增第二条 queued run。
- 数据/日志验证点：
  - 第二次 `POST /run-now` 返回 409，`error.code=409713`。
  - `valuz_automation_run` 中 `status="queued"` 的同自动化记录数量不增加。

#### AUTO-DTL-E2E-036 马上运行失败：已有 running 运行

- 前置条件：
  - 使用 `AUTO_RUNNING_CHAT`，最新 run `status="running"`。
- 步骤：
  1. 打开详情页。
  2. 点击“马上运行”。
- 预期结果：
  - 系统拒绝重复运行。
- UI 验证点：
  - 显示已有运行中的错误提示。
  - 顶部和列表继续保持“正在运行”。
- 数据/日志验证点：
  - `POST /run-now` 返回 409，`error.code=409712`。
  - 不新增新的 `manual` run。

#### AUTO-DTL-E2E-037 马上运行失败：自动化不存在

- 前置条件：
  - 页面加载后，在另一个客户端删除当前自动化。
- 步骤：
  1. 当前页面点击“马上运行”。
- 预期结果：
  - 前端提示目标不存在，并允许返回总览。
- UI 验证点：
  - 不展示成功入队。
  - 不出现永久“正在运行”假状态。
- 数据/日志验证点：
  - `POST /run-now` 返回 404，`error.code=404711`。
  - `valuz_automation_run` 不新增记录。

#### AUTO-DTL-E2E-038 暂停/恢复失败：目标不存在

- 前置条件：
  - 页面加载后，在另一个客户端删除当前自动化。
- 步骤：
  1. 点击“暂停”或“恢复”。
- 预期结果：
  - 前端展示失败提示并允许离开详情页。
- UI 验证点：
  - 按钮 loading 结束。
  - 状态不被错误地乐观切换。
  - 提供返回总览或自动刷新为不存在状态。
- 数据/日志验证点：
  - `POST /pause` 或 `POST /resume` 返回 404，`error.code=404711`。
  - DB 不产生新 run，不恢复已删除自动化。

#### AUTO-DTL-E2E-039 历史运行失败态错误信息优先级

- 前置条件：
  - 使用 `AUTO_FAILED`，准备三条 failed run：
    - run A 有 `error_message_key`。
    - run B 无 key，有 `error_message`。
    - run C 只有 `error_code`。
- 步骤：
  1. 打开详情页。
  2. 查看三条失败记录。
- 预期结果：
  - 失败原因按优先级展示，用户能看懂错误来源。
- UI 验证点：
  - run A 展示本地化后的 `error_message_key`。
  - run B 展示 `error_message`。
  - run C 展示 `error_code`。
  - 三条均为红色错误态。
- 数据/日志验证点：
  - `GET /runs` 返回三条 failed run 的 `error_message_key/error_message/error_code`。
  - UI 展示优先级为 `error_message_key` > `error_message` > `error_code`。

#### AUTO-DTL-E2E-040 运行失败态不影响其他操作

- 前置条件：
  - 使用 `AUTO_FAILED`，状态为 `enabled`。
- 步骤：
  1. 打开详情页。
  2. 查看失败历史。
  3. 点击“编辑”并取消。
  4. 点击“马上运行”。
- 预期结果：
  - 历史失败只作为记录展示，不阻塞后续编辑和手动运行。
- UI 验证点：
  - 顶部不因历史失败显示运行中。
  - 编辑弹窗可正常打开。
  - 马上运行成功后新增 pending 行在失败行上方。
- 数据/日志验证点：
  - 初始最新 run 非 `queued/running`。
  - `POST /run-now` 返回 202。
  - 新 run `triggered_at` 大于历史 failed run，列表第一条为新 run。

#### AUTO-DTL-E2E-041 Activity 运行态接口失败

- 前置条件：
  - 模拟 `GET /v1/runs?status=running` 返回 500 或网络失败；自动化列表数据正常。
- 步骤：
  1. 打开 `/activity`。
  2. 切换各筛选 Tab。
- 预期结果：
  - Activity 不白屏，自动化代表条目仍可展示；运行态区域展示空或失败降级。
- UI 验证点：
  - 页面框架、筛选 Tab 正常。
  - 自动化条目可点击进入详情。
  - 不错误展示“正在运行”假状态。
- 数据/日志验证点：
  - `GET /v1/runs?status=running` 失败被前端捕获。
  - 无 mutation 请求。
  - 后端 error/access log 有失败记录。

#### AUTO-DTL-E2E-042 未认证访问详情页

- 前置条件：
  - 清空或失效用户认证上下文。
- 步骤：
  1. 打开 `/automations/{AUTO_G.automation_id}`。
- 预期结果：
  - 用户被引导到登录/认证恢复流程，或页面展示未认证错误。
- UI 验证点：
  - 不展示其他用户的自动化信息。
  - 不白屏。
- 数据/日志验证点：
  - `GET /v1/automations/{id}` 返回 401，响应为 `error.code=401`。
  - 不发起任何写接口。

### 覆盖矩阵

| 必须覆盖项 | 对应用例 |
| --- | --- |
| 打开详情页看到标题、指令、定时、运行记录 | AUTO-DTL-E2E-001、002、020、021 |
| 编辑成功与失败 | AUTO-DTL-E2E-006、029、030、031、032 |
| 删除成功与失败 | AUTO-DTL-E2E-010、033 |
| 马上运行成功与失败 | AUTO-DTL-E2E-009、034、035、036、037 |
| 全局 vs 项目标识、项目名、回到项目 | AUTO-DTL-E2E-001、002 |
| 详情页/菜单/动态三处正在运行 | AUTO-DTL-E2E-014、015 |
| 菜单和动态运行中置顶排序 | AUTO-DTL-E2E-016、017 |
| 自动化在菜单/动态总是显示条目 | AUTO-DTL-E2E-018、019 |
| 空状态、加载态、运行失败态、暂停态 | AUTO-DTL-E2E-020、021、022、039、040 |
| 异常/错误码 | AUTO-DTL-E2E-027 至 038、041、042 |

---

## 单元 / 契约用例（开发补充）

### 说明

- 阶段：P2，**只设计用例、不写实现**（与上方 E2E 同纪律）。本节为「开发补充」，覆盖 QA E2E 未触达的**纯逻辑层**：后端 service/schemas 单元、API 契约字段与错误码、前端纯函数/数据映射。
- 被测符号标注规则：
  - `【现存】` —— 仓库中真实存在的符号，已核对文件:符号:行号。
  - `【PLAN新增】` —— `plan.md` 明确提出本版要新增/改动的符号（含被授权的前端抽象、契约先行的 OpenAPI/后端字段、manual round-trip 修复）。未实现前用例先行，对齐「契约/类型先齐」。
- 主要被测文件：
  - 后端 service：`backend/valuz_agent/modules/automations/service.py`
  - 后端 schemas：`backend/valuz_agent/modules/automations/schemas.py`
  - 后端 errors：`backend/valuz_agent/modules/automations/errors.py`
  - 后端 triggers：`backend/valuz_agent/modules/automations/triggers.py`
  - 后端 routes：`backend/valuz_agent/api/routes/automations.py`
  - 契约：`api/openapi.yaml`
  - 前端纯函数：`frontend/packages/app/src/pages/AutomationPage.tsx`
  - 前端类型/API：`frontend/packages/core/src/api/automations-api.ts`
  - 前端编辑弹窗：`frontend/packages/app/src/components/CreateAutomationDialog.tsx`
  - 被授权抽象（PLAN §3/§4.3，将落到 `@valuz/core`）：`automation-run-status.ts`（`runToLogStatus` + `isAutomationRunning`）、归一排序比较器。

### 被测符号索引

| 符号 | 文件:符号 | 状态 |
| --- | --- | --- |
| run-now 单飞 | `service.py:AutomationService.run_now`（872-916） | 【现存】 |
| 组装详情 | `service.py:AutomationService._row_to_detail`（265-273） | 【现存】 |
| 组装列表项 | `service.py:AutomationService._row_to_item`（244-263） | 【现存】 |
| 组装 run 行 | `service.py:AutomationService._run_to_item`（277-302） | 【现存】 |
| 组装 runs 列表 | `service.py:AutomationService.list_runs`（918-936） | 【现存】 |
| task_status 批量解析 | `service.py:AutomationService._resolve_task_statuses`（938-948） | 【现存】 |
| 暂停/恢复 | `service.py:AutomationService.pause/resume`（830-858） | 【现存】 |
| 到期判定 | `triggers.py:TriggerEvaluator.is_due`（84-99） | 【现存】 |
| 错误码 | `errors.py:Automation*`（18-133） | 【现存】 |
| run 响应模型 | `schemas.py:AutomationRunItemResponse`（191-211） | 【现存】 |
| 详情响应模型 | `schemas.py:AutomationDetailResponse`（183-188） | 【现存】 |
| run-now 响应模型 | `schemas.py:AutomationRunAcceptedResponse`（236-239） | 【现存】 |
| run 状态→展示状态 | `AutomationPage.tsx:runStatusToLogStatus`（54-61） | 【现存】 |
| run→行状态（task 优先） | `AutomationPage.tsx:runToLogStatus`（66-73） | 【现存】 |
| 失败信息优先级 | `AutomationPage.tsx:executionRows`（587-598） | 【现存】 |
| 触发列 | `AutomationPage.tsx:triggerColumn`（112-116） | 【现存】 |
| 列表行映射 | `AutomationPage.tsx:automationToTableRow`（127-140） | 【现存】 |
| 耗时格式化 | `AutomationPage.tsx:formatDuration`（81-89） | 【现存】 |
| 工具输出解析 | `AutomationToolCard.tsx:parseAutomationToolOutput`（231-249） | 【现存】 |
| 列表项运行态 | `schemas.py:AutomationItemResponse.is_running` + `service.py:_row_to_item` 回填 | 【PLAN新增】§3.2 |
| 运行态唯一语义 | `@valuz/core/automation-run-status.ts:isAutomationRunning` | 【PLAN新增】§3 |
| 归一排序比较器 | `@valuz/core` 排序比较器（`{isRunning,activeTs,id}`） | 【PLAN新增】§4.3 |
| manual round-trip | `CreateAutomationDialog.tsx:buildTrigger`（398-412） | 【PLAN新增】§2.1 |
| `/v1/automations` 契约 | `api/openapi.yaml`（当前 grep 命中 0） | 【PLAN新增】§7 |
| `/v1/runs` automation 来源 | `api/openapi.yaml:RunSummary`（`source_kind`+`automation_id`） | 【PLAN新增】§4.1 |

---

### 一、后端 service / schemas 单元用例

#### AUTO-DTL-UT-SVC-001 run-now：enabled 且无在途运行 → 入队 queued 并返回 202 形

- 被测符号：`service.py:AutomationService.run_now`【现存】（890-916）
- 输入：`row.status="enabled"`；`_ds.last_run` 返回 `None`（或最新 run 为 `success/failed/skipped`）。
- 预期/断言：
  - `_ds.create_run` 被调用一次，新 run `trigger_type="manual"`、`status="queued"`、`triggered_at` 取 `now_ms()`。
  - 发布事件 `automation.run.queued`，带 `automation_id` + `run_id`。
  - `automation_runner.enqueue_threadsafe(automation_id, run.id, user_id)` 被调用。
  - 返回 `AutomationRunAcceptedResponse(run_id, automation_id, status="queued")`。

#### AUTO-DTL-UT-SVC-002 run-now：最新 run 为 queued → 抛 AutomationAlreadyQueued(409713)

- 被测符号：`service.py:run_now`【现存】（892-893）+ `errors.py:AutomationAlreadyQueued`【现存】（118-120）
- 输入：`row.status="enabled"`；`_ds.last_run` 返回 `status="queued"` 的 run。
- 预期/断言：抛 `AutomationAlreadyQueued`，`error_code=409713`；**不**调用 `create_run`、**不**发布事件、**不** enqueue。

#### AUTO-DTL-UT-SVC-003 run-now：最新 run 为 running → 抛 AutomationAlreadyRunning(409712)

- 被测符号：`service.py:run_now`【现存】（894-895）+ `errors.py:AutomationAlreadyRunning`【现存】（113-115）
- 输入：`row.status="enabled"`；`_ds.last_run` 返回 `status="running"`。
- 预期/断言：抛 `AutomationAlreadyRunning`，`error_code=409712`；无写副作用（不 create_run / 不发事件 / 不 enqueue）。

#### AUTO-DTL-UT-SVC-004 run-now：自动化已暂停 → 抛 AutomationPaused(409711)

- 被测符号：`service.py:run_now`【现存】（887-888）+ `errors.py:AutomationPaused`【现存】（108-110）
- 输入：`row.status="paused"`。
- 预期/断言：在读 `last_run` 之前即抛 `AutomationPaused`，`error_code=409711`；不新增 run。

#### AUTO-DTL-UT-SVC-005 run-now：自动化不存在 → 抛 AutomationNotFound(404711)

- 被测符号：`service.py:run_now`【现存】（884-886）+ `errors.py:AutomationNotFound`【现存】（18-20）
- 输入：`_ds.get_automation` 返回 `None`。
- 预期/断言：抛 `AutomationNotFound`，`error_code=404711`；无任何写副作用。

#### AUTO-DTL-UT-SVC-006 详情组装：_row_to_detail 含统计与时间戳

- 被测符号：`service.py:AutomationService._row_to_detail`【现存】（265-273）
- 输入：一条 `AutomationRow`，`_ds.count_runs` 返回 `7`、`_ds.count_recent_failures` 返回 `2`，`row.created_at=1000`、`row.updated_at=2000`。
- 预期/断言：返回 `AutomationDetailResponse`，在 `_row_to_item` 全字段基础上追加 `prompt_template=row.prompt_template`、`total_runs=7`、`recent_failures=2`、`created_at=1000`、`updated_at=2000`。

#### AUTO-DTL-UT-SVC-007 详情组装：created_at/updated_at 为空回退 now_ms

- 被测符号：`service.py:_row_to_detail`【现存】（272-273）
- 输入：`row.created_at=None`、`row.updated_at=None`。
- 预期/断言：`created_at` 与 `updated_at` 均回退为 `now_ms()`（非 `None`，schema 字段为 `int` 不可空）。

#### AUTO-DTL-UT-SVC-008 列表项组装：last_run_status 取最近一条 run 状态

- 被测符号：`service.py:AutomationService._row_to_item`【现存】（244-263）
- 输入：(a) `_ds.last_run` 返回 `status="failed"`；(b) `_ds.last_run` 返回 `None`。
- 预期/断言：(a) `last_run_status="failed"`；(b) `last_run_status=None`。其余字段 `project_kind/agent_kind/agent_name/action_kind/trigger/status/next_run_at/last_run_at` 按 row 投影；`agent_name` 取 `_resolve_agent_name(row)`（agent 删除时为 `None`）。

#### AUTO-DTL-UT-SVC-009 run 行组装：error_message 不在响应（契约对齐）

- 被测符号：`service.py:AutomationService._run_to_item`【现存】（286-302）+ `schemas.py:AutomationRunItemResponse`【现存】（191-211）
- 输入：`AutomationRunRow`，含 `error_code`、`error_message_key`，且 `row.error_message` 有值。
- 预期/断言：响应包含 `error_code`、`error_message_key`，**不含** `error_message` 字段（schema 未声明）。佐证 PLAN §7 前端类型 `AutomationRunItem.error_message`（`automations-api.ts:128`）为漂移，需修正。

#### AUTO-DTL-UT-SVC-010 run 行组装：created_files JSON 解析容错

- 被测符号：`service.py:_run_to_item`【现存】（280-285）
- 输入：(a) `row.created_files='["a.md","b.csv"]'`；(b) `row.created_files='{not json'`；(c) `row.created_files=None`。
- 预期/断言：(a) `created_files=["a.md","b.csv"]`；(b) 解析失败 → `created_files=[]`（吞 `JSONDecodeError/TypeError`，不抛）；(c) `created_files=[]`。

#### AUTO-DTL-UT-SVC-011 runs 列表：默认 limit=20 且 task 自动化回填 live task_status

- 被测符号：`service.py:AutomationService.list_runs`【现存】（918-936）+ `_resolve_task_statuses`【现存】（938-948）
- 输入：自动化存在；`_ds.list_runs` 返回 3 条 run，其中一条 `status="success"` 且带 `session_id`，对应 task 实时 `task_status="active"`。
- 预期/断言：调 `_ds.list_runs(..., limit=20)`（未传 limit 用默认）；带 `session_id` 的 run 经 `_resolve_task_statuses` 回填 `task_status="active"`；无 `session_id` 的 run `task_status=None`。

#### AUTO-DTL-UT-SVC-012 runs 列表：无 session_id 时跳过 task 解析（无 N+1）

- 被测符号：`service.py:_resolve_task_statuses`【现存】（946-948）
- 输入：`_ds.list_runs` 返回的 run 全部 `session_id=None`（chat 型自动化）。
- 预期/断言：`_resolve_task_statuses` 返回 `{}`，**不**触达 tasks datastore；每条 run `task_status=None`。

#### AUTO-DTL-UT-SVC-013 runs 列表：自动化不存在 → 抛 AutomationNotFound(404711)

- 被测符号：`service.py:list_runs`【现存】（921-923）
- 输入：`_ds.get_automation` 返回 `None`。
- 预期/断言：抛 `AutomationNotFound`，`error_code=404711`；不查询 runs。

#### AUTO-DTL-UT-SVC-014 暂停：状态置 paused 并清空 next_run_at

- 被测符号：`service.py:AutomationService.pause`【现存】（830-843）
- 输入：`row.status="enabled"`、`row.next_run_at=12345`。
- 预期/断言：`row.status="paused"`、`row.next_run_at=None`、`row.updated_at=now_ms()`；调用 `update_automation`；发布 `automation.changed`；返回 `AutomationDetailResponse`（`status="paused"`、`next_run_at=None`）。

#### AUTO-DTL-UT-SVC-015 恢复：状态置 enabled 并重算 next_run_at

- 被测符号：`service.py:AutomationService.resume`【现存】（845-858）+ `triggers.py:TriggerEvaluator.initial_next_fire`【现存】（103-110）
- 输入：(a) cron/interval 触发器，`row.status="paused"`、`row.next_run_at=None`；(b) manual 触发器。
- 预期/断言：`row.status="enabled"`、`row.updated_at=now_ms()`；(a) `next_run_at = initial_next_fire(row, now)`（≥ now）；(b) manual `next_run_at=None`；发布 `automation.changed`；返回详情。

#### AUTO-DTL-UT-SVC-016 暂停/恢复：目标不存在 → 抛 AutomationNotFound(404711)

- 被测符号：`service.py:pause/resume`【现存】（831-833 / 846-848）
- 输入：`_ds.get_automation` 返回 `None`。
- 预期/断言：两者均抛 `AutomationNotFound`，`error_code=404711`；不写库、不发事件。

#### AUTO-DTL-UT-SVC-017 到期判定：is_due 三条件合取

- 被测符号：`triggers.py:TriggerEvaluator.is_due`【现存】（84-99）
- 输入与预期：
  - `status="enabled"` + `trigger_kind="cron"` + `next_run_at <= now` → `True`；
  - `status="paused"` → `False`（暂停不触发）；
  - `trigger_kind="manual"` → `False`（manual 永不按 tick 触发）；
  - `next_run_at=None` 或 `next_run_at > now` → `False`。

#### AUTO-DTL-UT-SVC-018 列表项运行态：is_running 对 chat 与 task 均正确【PLAN新增】

- 被测符号：`schemas.py:AutomationItemResponse.is_running`【PLAN新增】§3.2 + `service.py:_row_to_item` 回填逻辑【PLAN新增】§3.2 步骤 2
- 输入：
  - (a) chat 型，`last_run.status="running"`；
  - (b) chat 型，`last_run.status="success"`；
  - (c) task 型，`last_run.status="success"` 但 last_run 携 `session_id` 且解析 `task_status="active"`；
  - (d) 从未运行，`last_run=None`。
- 预期/断言：(a) `is_running=true`；(b) `is_running=false`；(c) `is_running=true`（修掉 task 型漏判，复用 `_resolve_task_statuses` 解析单条 last_run）；(d) `is_running=false`。语义须与前端 `isAutomationRunning(listRuns[0])`（§3）一致——`is_running` 即服务端投影。

---

### 二、API 契约用例（含错误码）

> 错误响应统一：业务错误 `{ "error": { "code": number, "message": string } }`（`code` 即 `error_code`）；FastAPI 请求体校验返回 HTTP 422 `detail`。

#### AUTO-DTL-CT-API-001 GET /v1/automations/{id} 详情响应字段

- 被测符号：`routes/automations.py:get_automation`【现存】（177-182）→ `schemas.py:AutomationDetailResponse`【现存】（183-188 + 继承 145-173）
- 输入：存在的 `automation_id`。
- 预期/断言：HTTP 200；响应含 `automation_id/project_id/project_name/project_kind/name/agent_kind/agent_slug/agent_name/action_kind/trigger/trigger_human_readable/status/next_run_at/last_run_at/last_run_status/prompt_template/total_runs/recent_failures/created_at/updated_at`；`trigger` 为判别 union（`kind ∈ {cron,interval,manual}`）；`agent_name` 可空（agent 删除）。全局自动化 `project_kind="chat"`，项目自动化 `project_kind="project"`（前端据此判全局/项目，PLAN §5）。

#### AUTO-DTL-CT-API-002 GET /v1/automations/{id} 不存在 → 404711

- 被测符号：`routes/automations.py:get_automation`【现存】+ `errors.py:AutomationNotFound`【现存】（18-20）
- 输入：不存在的 `automation_id`。
- 预期/断言：HTTP 404；`error.code=404711`，`message="Automation not found"`。

#### AUTO-DTL-CT-API-003 GET /v1/automations/{id}/runs 响应包络与字段

- 被测符号：`routes/automations.py:list_automation_runs`【现存】（244-251）→ `schemas.py:AutomationRunItemResponse`【现存】（191-211）
- 输入：存在的自动化，`limit` 默认 20。
- 预期/断言：HTTP 200；包络 `{ "runs": [...] }`；每条含 `run_id/automation_id/project_id/trigger_type/status/triggered_at/started_at/completed_at/duration_ms/result_summary/error_code/error_message_key/session_id/created_files/task_status`；**不含** `error_message`；按 `triggered_at desc`。

#### AUTO-DTL-CT-API-004 POST /v1/automations/{id}/run-now 成功 → 202 + Accepted 形

- 被测符号：`routes/automations.py:run_automation_now`【现存】（231-240）→ `schemas.py:AutomationRunAcceptedResponse`【现存】（236-239）
- 输入：enabled 自动化，最新 run 非 queued/running。
- 预期/断言：HTTP 202；响应 `{ run_id, automation_id, status:"queued" }`。

#### AUTO-DTL-CT-API-005 POST run-now 错误码矩阵

- 被测符号：`routes/automations.py:run_automation_now`【现存】+ `errors.py`【现存】
- 输入与预期：
  - 已暂停 → 409，`error.code=409711`（`AutomationPaused`）；
  - 已有 running → 409，`error.code=409712`（`AutomationAlreadyRunning`）；
  - 已有 queued → 409，`error.code=409713`（`AutomationAlreadyQueued`）；
  - 不存在 → 404，`error.code=404711`（`AutomationNotFound`）。

#### AUTO-DTL-CT-API-006 PATCH /v1/automations/{id} 校验错误码矩阵

- 被测符号：`routes/automations.py:update_automation`【现存】（186-197）+ `errors.py`【现存】
- 输入与预期：
  - 空名称 → 422，`error.code=422713`（`AutomationNameEmpty`）；
  - 空指令 → 422，`error.code=422714`（`AutomationPromptEmpty`）；
  - 非法 cron → 422，`error.code=422711`（`InvalidCronExpression`）；
  - interval < 30s → 422，`error.code=422712`（`IntervalTooShort`，亦可能为 FastAPI `detail`，因 `IntervalTrigger.seconds` 有 `ge=30`，`schemas.py:34-39`）；
  - chat 自动化设 `action_kind="task"` → 422，`error.code=422717`（`AutomationTaskOnlyOnProject`）；
  - 绑定非本项目成员 agent → 404，`error.code=404714`（`AgentNotInProject`）。
- 成功路径：HTTP 200，返回 `AutomationDetailResponse`（更新后字段）。

#### AUTO-DTL-CT-API-007 DELETE /v1/automations/{id} → 204 / 404711

- 被测符号：`routes/automations.py:delete_automation`【现存】（201-206）+ `service.py:delete`【现存】（860-870）
- 输入与预期：
  - 存在 → HTTP 204 无包体，级联删除 run 历史；
  - 不存在 → HTTP 404，`error.code=404711`。

#### AUTO-DTL-CT-API-008 POST pause / resume → 200 / 404711

- 被测符号：`routes/automations.py:pause_automation`【现存】（210-216）、`resume_automation`【现存】（220-227）
- 输入与预期：
  - pause 成功 → 200，`status="paused"`、`next_run_at=null`；
  - resume 成功 → 200，`status="enabled"`，cron/interval `next_run_at` 重算（≥ now），manual `next_run_at=null`；
  - 目标不存在 → 404，`error.code=404711`。

#### AUTO-DTL-CT-API-009 OpenAPI 契约补全：/v1/automations 列表与 AutomationItem.is_running【PLAN新增】

- 被测符号：`api/openapi.yaml`【PLAN新增】§3.2/§7 —— 当前 `/v1/automations/*` grep 命中 0（完全缺失）。
- 输入：契约新增 `GET /v1/automations` path + `AutomationItem` schema，列表项新增 `is_running: boolean`。
- 预期/断言：
  - OpenAPI 中 `AutomationItem` 含 `is_running`（必填 boolean）；
  - 后端 `AutomationItemResponse.is_running` 与契约一致；
  - `make generate-types` 后前端 `AutomationItem` 类型出现 `is_running`；
  - 顺序遵循「契约先行」：先 OpenAPI → 后端 → 前端，不可颠倒（PLAN §3.2/§7）。

#### AUTO-DTL-CT-API-010 OpenAPI 契约补全：/v1/runs 新增 automation 来源【PLAN新增】

- 被测符号：`api/openapi.yaml:RunSummary`【PLAN新增】§4.1/§7
- 输入：`RunSummary.source_kind` 枚举增 `"automation"`；新增 `automation_id: string | null`（仅 `source_kind="automation"` 时非空，其余为 `null`）。
- 预期/断言：
  - 契约枚举含 `automation`；`automation_id` 可空；
  - automation 来源 run 投影为 `RunSummary{ source_kind:"automation", automation_id, session_id, status, updated_at }`；
  - 现有 `assistant/project_chat/task` 来源不受影响（回归点，PLAN §6）。

#### AUTO-DTL-CT-API-011 未认证访问详情 → 401

- 被测符号：`routes/automations.py:get_automation`【现存】（owner-scoped，依赖注入鉴权）
- 输入：失效/缺失认证上下文。
- 预期/断言：HTTP 401；不返回他人自动化数据；非 owner 统一走无权/找不到态（PLAN 验收映射「权限」行）。

---

### 三、前端关键纯函数 / 数据映射单元用例

#### AUTO-DTL-UT-FE-001 runStatusToLogStatus：run.status → 行展示状态

- 被测符号：`AutomationPage.tsx:runStatusToLogStatus`【现存】（54-61）
- 输入与预期（输入 → 输出）：
  - `"success"` → `"ok"`；
  - `"failed"` → `"err"`；
  - `"queued"` → `"pending"`；`"running"` → `"pending"`；
  - `"skipped"` → `"skip"`；`"interrupted_by_shutdown"` → `"skip"`（default 分支）。

#### AUTO-DTL-UT-FE-002 runToLogStatus：task_status 优先于 run.status

- 被测符号：`AutomationPage.tsx:runToLogStatus`【现存】（66-73）
- 输入与预期：
  - `{ status:"success", task_status:"active" }` → `"pending"`（任务仍在跑，run 行已冻结为 success 不采信）；
  - `{ status:"success", task_status:"paused" }` → `"pending"`；
  - `{ status:"success", task_status:"completed" }` → `"ok"`；
  - `{ status:"success", task_status:"failed" }` → `"err"`；
  - `{ status:"failed", task_status:null }` → `"err"`（回退到 `runStatusToLogStatus`）；
  - `{ status:"queued", task_status:null }` → `"pending"`。

#### AUTO-DTL-UT-FE-003 isAutomationRunning：运行态唯一语义【PLAN新增】

- 被测符号：`@valuz/core/automation-run-status.ts:isAutomationRunning`【PLAN新增】§3（当前逻辑内联于 `AutomationPage.tsx`，本版授权提取为共享纯函数，三处复用）
- 输入与预期（按 PLAN §3 判定表）：
  - `{ status:"queued" }` → `true`；`{ status:"running" }` → `true`；
  - task 型 `{ status:"success", task_status:"active" }` → `true`（run 结算后 task 仍活跃）；
  - `{ status:"success", task_status:"paused" }` → `false`（= 已暂停，PLAN §3 第 3 条；注意与 `runToLogStatus` 的 `paused→pending` 用途不同：前者判「是否运行中置顶」，后者判「行徽章着色」）；
  - `{ status:"success", task_status:null }` → `false`；
  - `{ status:"failed" }` / `{ status:"skipped" }` / `{ status:"interrupted_by_shutdown" }` → `false`。

#### AUTO-DTL-UT-FE-004 归一排序比较器：运行中置顶 → 活跃时间倒序 → id 稳定【PLAN新增】

- 被测符号：`@valuz/core` 归一排序比较器【PLAN新增】§4.3（把任意行映射为 `{ isRunning, activeTs, id }`，比较序 `isRunning desc → activeTs desc → id asc`）
- 输入：混合 `RunSummary`（automation/chat/task）与 `AutomationItem` 行，其中 2 条 `isRunning=true`、3 条 `false`，活跃时间各异。
- 预期/断言：
  - 所有 `isRunning=true` 的下标 < 任意 `isRunning=false` 的下标；
  - 同运行态内按 `activeTs` 降序（`RunSummary.activeTs=updated_at`；`AutomationItem.activeTs=last_run_at ?? next_run_at ?? 0`）；
  - `activeTs` 相等时按 `id` 升序（`RunSummary.id=session_id`；`AutomationItem.id=automation_id`）保证稳定；
  - 从未运行的 `AutomationItem`（`last_run_at=null && next_run_at=null`）`activeTs=0`，落最后。

#### AUTO-DTL-UT-FE-005 失败信息优先级：error_message_key > result_summary > error_message > error_code

- 被测符号：`AutomationPage.tsx:executionRows`（`output` 表达式）【现存】（592-598）
- 输入与预期：
  - 有 `error_message_key` → 输出 `t(error_message_key)`（本地化后）；
  - 无 key、有 `result_summary` → 输出 `result_summary`；
  - 仅 `error_code` → 输出 `String(error_code)`；
  - 全空 → 输出 `""`。
- 备注：链中 `run.error_message` 在后端实际恒空（见 AUTO-DTL-UT-SVC-009 / CT-API-003），PLAN §7 要求修正该手写类型漂移；本用例锁定回退优先级行为，修漂移后断言不变。

#### AUTO-DTL-UT-FE-006 triggerColumn：触发器 → 等宽列文案

- 被测符号：`AutomationPage.tsx:triggerColumn`【现存】（112-116）
- 输入与预期：
  - `{ kind:"cron", cron_expr:"0 9 * * *" }` → `"0 9 * * *"`；
  - `{ kind:"interval", seconds:300 }` → `"300s"`；
  - `{ kind:"manual" }` → `"—"`。

#### AUTO-DTL-UT-FE-007 automationToTableRow：AutomationItem → 表格行映射

- 被测符号：`AutomationPage.tsx:automationToTableRow`【现存】（127-140）
- 输入：`AutomationItem{ automation_id:"a1", name:"Daily", trigger_human_readable:"每天9点", trigger:{kind:"cron",cron_expr:"0 9 * * *",timezone:"Asia/Shanghai"}, last_run_at:T, status:"enabled" }`。
- 预期/断言：`{ id:"a1", name:"Daily", prompt:"每天9点", trigger:"0 9 * * *", triggerTimezone:"Asia/Shanghai", last:relativeTime(T), status:"on" }`；`status="paused"` → `"off"`；`trigger.kind` 非 cron 时 `triggerTimezone=undefined`。

#### AUTO-DTL-UT-FE-008 formatDuration：耗时分档格式化

- 被测符号：`AutomationPage.tsx:formatDuration`【现存】（81-89）
- 输入与预期：
  - `null` → `"—"`；
  - `500` → `"500ms"`；
  - `4200` → `"4s"`（floor 到秒）；
  - `65000` → `"1m5s"`；
  - `120000` → `"2m"`（整分钟无尾秒）。

#### AUTO-DTL-UT-FE-009 CreateAutomationDialog manual round-trip：编辑 manual 不被转 cron【PLAN新增】

- 被测符号：`CreateAutomationDialog.tsx:buildTrigger`【PLAN新增】§2.1（398-412，现仅输出 cron/interval，本版增 manual 分支）+ seeding（343-350，`initial.trigger.kind==="manual"` 不再 fall through 到 cron）
- 输入：编辑模式传入 `initial.trigger = { kind:"manual" }`，仅改 `name`，不动触发器。
- 预期/断言：
  - seeding 后 `triggerKind === "manual"`（不落默认 cron）；
  - `buildTrigger()` 返回 `{ kind:"manual" }`（原样回写，不静默改为默认 cron）；
  - 对照（回归）：`initial.trigger.kind==="cron"/"interval"` 时 `buildTrigger()` 行为不变。

#### AUTO-DTL-UT-FE-010 parseAutomationToolOutput：MCP 工具输出安全解析

- 被测符号：`AutomationToolCard.tsx:parseAutomationToolOutput`【现存】（231-249）
- 输入与预期：
  - 合法 JSON payload → 返回解析后的 `AutomationToolResultPayload` 对象；
  - 非法 JSON / `undefined` / `null` → 返回 `null`（不抛异常）。

#### AUTO-DTL-UT-FE-011 全局 vs 项目判定：仅看 project_kind

- 被测符号：详情页归属判定（`AutomationPage.tsx` 同源逻辑，PLAN §5：`project_kind === "chat"` → 全局）+ 契约字段 `AutomationDetailResponse.project_kind`【现存】
- 输入与预期：
  - `project_kind="chat"`（即便带 `project_id/project_name`）→ 判「全局」，不显示项目名与「回到项目」；`agent_kind` 仅作绑定来源小字；
  - `project_kind="project"` → 判「项目」，显示 `project_name` +「回到项目」→ `/projects/{project_id}`。

### 覆盖矩阵（开发补充）

| 必须覆盖项 | 对应用例 |
| --- | --- |
| 判定某自动化是否有 running/queued 执行 | AUTO-DTL-UT-SVC-002、003、004、018；AUTO-DTL-UT-FE-003 |
| detail / runs 组装 | AUTO-DTL-UT-SVC-006、007、008、009、010、011、012 |
| run-now 触发与单飞 | AUTO-DTL-UT-SVC-001~005；AUTO-DTL-CT-API-004、005 |
| 暂停 / 恢复 | AUTO-DTL-UT-SVC-014、015、016；AUTO-DTL-CT-API-008 |
| 全局 vs 项目字段 | AUTO-DTL-UT-SVC-008；AUTO-DTL-CT-API-001；AUTO-DTL-UT-FE-011 |
| API 契约字段 + 错误码 | AUTO-DTL-CT-API-001~008、011 |
| 契约先行新增（is_running / runs automation 来源） | AUTO-DTL-UT-SVC-018；AUTO-DTL-CT-API-009、010 |
| run 状态 → 展示状态映射 | AUTO-DTL-UT-FE-001、002 |
| 运行态判定 / 置顶排序比较器 | AUTO-DTL-UT-FE-003、004 |
| 失败信息优先级 / 数据映射 / 格式化 | AUTO-DTL-UT-FE-005、006、007、008、010 |
| manual round-trip 修复 | AUTO-DTL-UT-FE-009 |
