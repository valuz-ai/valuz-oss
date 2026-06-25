## [2026-06-25] PRD 第1轮 — [结论：待修改]
**审查人**：产品经理 Reviewer
**意见**：
- P0: 「正在运行」判定没有收口，当前写法会导致详情页、菜单、动态三处表现不一致。PRD 写“最新 run 的 status 为 queued/running，或 task_status 为 active/paused”，但没有说明 latest run 如何取、queued 超时/进程重启后的残留如何处理、task_status=paused 到底展示为“正在运行”还是“已暂停/等待继续”。这是核心卖点和置顶排序依据，必须补成可执行规则：明确唯一数据源、状态映射表、冲突优先级、超时/恢复边界，以及三处 UI 是否完全复用同一判断。
- P0: 菜单/动态的「总是显示一条」与“点击进入任一自动化详情页”互相冲突。PRD 在动态里写“自动化总是显示一条代表条目”，待确认又写可能是“单条聚合入口”或“每条自动化各占一行”；但验收标准要求用户能从菜单/动态进入任一自动化详情页。如果只有聚合入口，无法直达任一自动化；如果每条自动化一行，“总是显示一条”这个表述就是错的。必须拍板列表粒度：每条自动化一行、每个项目一行，还是全局聚合一行，并同步点击目标、筛选 Tab 行为、Running 区展示和空历史展示。
- P0: 范围声明自相矛盾，阻断技术方案。PRD 把“动态/菜单新增自动化类型 + 运行中置顶”列为 P0，又声明“不改后端自动化调度/执行逻辑与 API 形状”；但现有 Activity 运行列表的数据类型只有 chat/task，自动化列表项也没有 is_running 字段，待确认里还提出可能要后端回填 is_running。必须明确本版是否允许改 OpenAPI/backend `/v1/runs` 或自动化 list/detail 响应；若不允许，就要把菜单/动态运行态降级或移出本版。
- P1: 「全局 vs 项目」产品语义没有收口。PRD 把 `project_kind="chat"` / `agent_kind="library_agent"` 定义为“全局自动化”，但现有自动化模型里 chat-kind 也有 `project_id/project_name`，且 `project_kind` 描述的是执行项目类型，不等同于用户可理解的“全局”。还缺少其他组合的显示规则，例如 chat + project_member、project + library_agent、action_kind=chat/task。请补一张组合映射表，明确徽章文案、是否展示项目名、是否展示“回到项目”、点击目标，以及哪些组合不存在或应被后端拒绝。
- P1: 权限模型没有覆盖用户故事。PRD 只写“用户/项目用户”能看、能跑、能暂停/删除，但没有说明谁有权查看项目自动化、谁能执行 runNow/pause/resume/delete/update、非 owner 访问详情页应显示 404/无权限/只读，项目被删除或成员被移除后如何处理。后端当前自动化读写是 owner-scoped，若产品预期“项目用户”可见或可操作，就必须补权限矩阵；若只支持创建者本人，也要在用户故事和验收里写清。
- P1: “完整历史运行记录”和“不做分页/加载更多，首版用 listRuns 默认条数”冲突。用户故事承诺看“完整运行历史”，但范围实际只展示默认条数，默认条数也没有在 PRD 中写明。请改成“最近 N 条运行记录”并给出 N，或把分页/加载更多纳入本版；否则 QA 和用户都会按“完整历史”验收。
- P1: 置顶排序规则不可验证。PRD 只写“正在执行的对话/任务/自动化一律排到最前，其余按时间倒序”，但没有定义自动化非运行态的排序时间取 `updated_at`、`last_run_at`、`next_run_at` 还是 `created_at`；也没有定义同为 running 时三类对象之间如何排序。请补排序 key、tie-breaker、空时间字段规则，以及菜单和动态是否共用同一排序函数。
- P1: 验收标准过于口语化，QA 不能直接落用例。比如“点马上运行/暂停/恢复/删除并看到结果”没有说明成功 toast、状态字段变化、按钮禁用态、错误态、刷新时限；“自动化总有一条出现在列表中”没有限定列表粒度和数据范围；“正在运行三处可见”没有指定 mock 数据和轮询最大延迟。请把每条验收改成 Given/When/Then 或至少补充数据条件、预期 UI 文案、可观测字段和时间阈值。
- P2: 异常状态覆盖不完整。PRD 提到 `agent_name=null` 显示“绑定的 Agent 已删除”，但没有说明编辑、马上运行、暂停/恢复按钮是否禁用，runNow 返回失败时如何提示；删除自动化后从详情页跳到哪里也未定义。建议补充这些边界，避免实现时各页面各自猜。
**待拍板项**：
- 「正在运行」的唯一真相源和状态映射表，尤其 `queued` 超时、`task_status=paused`、run 状态与 task 状态冲突时的优先级。
- 菜单/动态里自动化条目的粒度：每条自动化、每项目聚合，还是全局聚合。
- 本版是否允许改 OpenAPI/backend 响应来支持 Activity/菜单自动化运行态；若不允许，菜单/动态 P0 范围如何降级。
- “全局/项目”徽章按 `project_kind`、`agent_kind`、`action_kind` 的组合如何映射。
- 项目自动化的查看和操作权限矩阵。
- 历史列表到底是“完整历史”还是“最近 N 条”。
**处理**（产品负责人逐条拍板，已写入 prd.md）：
- P0「运行态收口」：定唯一真相源=该自动化「最新一次 run」状态，统一判定 `isAutomationRunning()` 详情/菜单/动态三处复用。冲突优先级 `run.status` 优先（`queued/running` 直接判运行中；run 成功结束后再看 `task_status`，`paused`=已暂停不算运行中）。新增「运行态映射表」(`run.status` × `task_status` → 运行中/已暂停/成功/失败/跳过/中断)。`queued` 超时/进程重启残留**不做超时重判**，以后端 run 状态为准（known limitation）。→ prd.md「运行态判定」节。
- P0「列表粒度」：拍板**每条自动化各占一行、恒显示**（即使从未运行），点行进该自动化详情页；**否决"聚合入口"**。动态新增「自动化」Tab 列出全部自动化各一行，运行中进顶部 Running 组，空历史行仍显示（「未运行」）。→ prd.md「菜单与动态新增自动化类型 + 置顶排序」节。
- P0「范围/是否改后端」：纠正原"不改 API 形状"→ **不改调度/执行核心逻辑，但允许最小只读契约新增（契约先行，先改 OpenAPI）**：自动化 list/detail 回填 `is_running`/`latest_run` 只读字段、`/v1/runs` 新增 `automation` 来源类型，避免前端 N×`listRuns`。→ prd.md「范围与 API 决策」节。
- P1「全局 vs 项目」：以 **`project_kind` 为唯一判别基准**（`chat`=全局即使带 `project_id`；`project`=项目展项目名+回项目导航）；`agent_kind` 仅作绑定来源标注，不参与判定。补 `project_kind` × `action_kind` 组合映射表（chat+chat=全局、project+chat/project+task=项目、chat+task=后端拒绝不存在），逐行给徽章文案/是否展项目名/是否展回项目导航/点击目标。→ prd.md「全局 vs 项目归属」节。
- P1「权限矩阵」：本版 **owner-scoped**，查看与全部操作（runNow/pause/resume/delete/update）均限创建者本人；非 owner 访问详情页返回 403/404；"项目其他成员可见/可操作"**不做**。用户故事改为「作为创建者」。→ prd.md「权限矩阵」节。
- P1「历史条数」：把"完整历史"改为「**最近 N 条运行记录**」，N 取后端 `listRuns` 默认值=**20**（`backend/valuz_agent/api/routes/automations.py` 已核实）；分页/加载更多**不做**。用户故事/验收同步改"最近的运行历史"。→ prd.md 用户故事 + 验收。
- P1「置顶排序可验证」：先按 `isAutomationRunning()` 分两组（运行中置顶），组内按活跃时间戳倒序（对话=最后活跃/消息时间、任务=`last_active`、自动化=`last_run_at`→`next_run_at`→`created_at`）；同为 running 三类混排按该时间戳倒序；**tie-breaker** 时间相同按 `id` 升序稳定排序；菜单与动态共用同一比较器。→ prd.md「置顶排序」。
- P1「验收 Given/When/Then」：全部验收改成 Given/When/Then，补数据条件、预期 UI 文案、可观测字段与时间阈值（运行态轮询最大延迟 ≤5s）。→ prd.md「验收标准」节。
- P2「异常边界」：`agent_name==null` → 编辑/马上运行禁用、保留删除；`runNow` 失败 → 错误 toast 展 `error_code`/`message`；删除成功 → 跳回来源（全局→`/automations`，项目→项目详情）。→ prd.md「异常边界」节。
- 6 项待拍板项已全部给出明确结论（见上）。

---

## [2026-06-25] PLAN 第2轮 — [结论：待修改]
**审查人**：全栈开发 Reviewer
**意见**：
- P0: 已解决｜task 型运行态漏判已按 P0 收口，且写清契约先行顺序。PLAN 要求先补 `api/openapi.yaml` 的 `/v1/automations` 最小只读片段和 `AutomationItem.is_running`，再改 `AutomationItemResponse` / `_row_to_item`，最后 `make generate-types`；`is_running` 语义也明确为 `last_run.status in {queued,running}` 或 task 型解析 `task_status == active`，不再靠 `last_run_status` 单独判断。回代码核实：当前 `AutomationItemResponse` 只有 `last_run_status`、无 `is_running`（`backend/valuz_agent/modules/automations/schemas.py:145`、`backend/valuz_agent/modules/automations/schemas.py:173`），`_row_to_item` 只取 `last_run.status` 回填（`backend/valuz_agent/modules/automations/service.py:244`、`backend/valuz_agent/modules/automations/service.py:262`），`list_runs` 才批量解析 task status（`backend/valuz_agent/modules/automations/service.py:927`、`backend/valuz_agent/modules/automations/service.py:938`、`backend/valuz_agent/modules/automations/service.py:951`），而 task kickoff 成功后 run 行写 `success` 并把真实活态留在 task/session 侧（`backend/valuz_agent/modules/automations/in_process_runner.py:486`、`backend/valuz_agent/modules/automations/in_process_runner.py:502`、`backend/valuz_agent/modules/automations/in_process_runner.py:552`）。期望改法：按 PLAN §3.2 / §7 顺序实现即可。
- P1: 未解决｜Activity 行模型仍没有落到当前类型和动作分支可实现。PLAN 选择扩展 `RunSummary` 单管道，并要求从未运行自动化合成 `session_id = null` 的 `RunSummary` 行，但当前 `RunSummary.session_id` 在前端和 OpenAPI 都是必填非空，PLAN 的 OpenAPI 改动点只写了 `source_kind="automation"` 与 `automation_id`，没有写 `session_id` nullable；同时 `ActivityPage` 的现有 history/render/action 分支会把 automation 历史行当 chat 行处理，暴露 rename/delete，并调用 `sessionsApi.delete(session_id)`。回代码核实：PLAN 合成行写 `session_id = null`（`docs/features/automation-detail-page/plan.md:185`、`docs/features/automation-detail-page/plan.md:189`、`docs/features/automation-detail-page/plan.md:191`），但契约改动未包含 `session_id` nullable（`docs/features/automation-detail-page/plan.md:331`、`docs/features/automation-detail-page/plan.md:333`），当前 TS 类型要求 `session_id: string`（`frontend/packages/core/src/api/runs-api.ts:21`、`frontend/packages/core/src/api/runs-api.ts:22`），OpenAPI required 也要求 `session_id` 且类型 string（`api/openapi.yaml:2666`、`api/openapi.yaml:2668`、`api/openapi.yaml:2670`）；页面直接用 `run.session_id` 订 SSE、做 key、rename、delete（`frontend/packages/app/src/pages/ActivityPage.tsx:199`、`frontend/packages/app/src/pages/ActivityPage.tsx:429`、`frontend/packages/app/src/pages/ActivityPage.tsx:431`、`frontend/packages/app/src/pages/ActivityPage.tsx:451`、`frontend/packages/app/src/pages/ActivityPage.tsx:485`、`frontend/packages/app/src/pages/ActivityPage.tsx:487`、`frontend/packages/app/src/pages/ActivityPage.tsx:511`）。为什么：三 source 的 open 分支已写清，但 render/action/type guard 未闭环，未运行自动化合成行会在类型层编不过或在行为层误走 chat 的 session 操作。期望改法：二选一明确落地；要么改为 `ActivityEntry` 判别联合并把 automation 合成行从 `RunSummary` 的 SSE/rename/delete 分支隔离；要么把 `RunSummary.session_id` 契约改为 nullable，并逐处加 `source_kind === "automation"` / `automation_id` / `session_id` guard，automation 历史行用 `automation_id` 做 key，只跳 `/automations/:automation_id`，不暴露 chat 的 rename/delete。
- P1: 已解决｜排序 key 已定义为两数据源归一比较器，并标注 `created_at` 契约债。PLAN 明确 `RunSummary` 用 `updated_at` + `session_id`，`AutomationItem` 用 `last_run_at ?? next_run_at ?? 0` + `automation_id`，统一比较顺序为 `isRunning desc -> activeTs desc -> id asc`；同时说明 PRD 的 `created_at` 兜底在 list item 不可得，本版不补 list 字段，列为契约债。回代码核实：当前 `AutomationItem` 确实只有 `next_run_at` / `last_run_at` / `last_run_status`，无 `created_at`（`frontend/packages/core/src/api/automations-api.ts:70`、`frontend/packages/core/src/api/automations-api.ts:88`、`frontend/packages/core/src/api/automations-api.ts:91`），`created_at` 只在 `AutomationDetail`（`frontend/packages/core/src/api/automations-api.ts:101`、`frontend/packages/core/src/api/automations-api.ts:105`）。期望改法：按 PLAN §4.3 实现共享比较器，避免各页自行排序。
- P1: 已解决｜CreateAutomationDialog 破坏 manual trigger 的风险已选定方案 (a) 无损 round-trip。PLAN 明确 `triggerKind` 增 `"manual"`，manual 编辑 seeding 保持 manual，`buildTrigger()` 返回 `{kind:"manual"}`，且 manual 在编辑模式作为只读保留态，不扩新建入口。回代码核实：前后端类型本来支持 manual（`frontend/packages/core/src/api/automations-api.ts:41`、`frontend/packages/core/src/api/automations-api.ts:45`、`backend/valuz_agent/modules/automations/schemas.py:42`、`backend/valuz_agent/modules/automations/models.py:44`），当前弹窗真实 bug 是 `triggerKind` 仅 cron/interval（`frontend/packages/app/src/components/CreateAutomationDialog.tsx:216`）、manual initial 落回默认 cron（`frontend/packages/app/src/components/CreateAutomationDialog.tsx:343`、`frontend/packages/app/src/components/CreateAutomationDialog.tsx:346`）、`buildTrigger()` 只输出 cron/interval（`frontend/packages/app/src/components/CreateAutomationDialog.tsx:398`、`frontend/packages/app/src/components/CreateAutomationDialog.tsx:408`）。期望改法：按 PLAN §2.1 实现，不要用详情页绕过式禁用替代。
**处理**：<留待开发响应>

---

## [2026-06-25] PLAN 第1轮 — [结论：待修改]
**审查人**：全栈开发 Reviewer
**意见**：
- P0 需修改: 菜单/动态的「运行中」P0 方案不能用 `AutomationItem.last_run_status in {queued,running}` 作为验收口径；这只能覆盖 chat 型，task 型 kickoff 后 `run.status` 会立即写成 `success`，真实活态只在 `listRuns` 返回的 `task_status`。继续按 PLAN 的 P0 上线会导致详情页可显示 task 正在运行，但菜单/动态漏置顶。期望改法：要么把 `listRuns(id, 1)` 扇出作为 P0 权威方案，要么把 `AutomationItemResponse.is_running` + OpenAPI 契约提升到 P0；若接受降级，需从 P0 验收里明确移除 task 型运行态正确性。回代码核实：`backend/valuz_agent/modules/automations/service.py:_row_to_item` 只回填 `last_run_status`（244-263），`backend/valuz_agent/modules/automations/in_process_runner.py:_execute_task_kickoff` 把 task run 写成 `success`（496-562），`backend/valuz_agent/modules/automations/service.py:list_runs/_resolve_task_statuses` 仅在 runs 列表解析 `task_status`（918-953），`frontend/packages/core/src/api/automations-api.ts:AutomationItem/AutomationRunItem` 分别缺少/包含 `task_status`（70-136）。
- P1 需修改: Activity 的自动化行模型没有定义到可实现。当前 `ActivityPage` 的运行卡片和历史行都吃 `RunSummary`，`RunningCard` 会用 `run.session_id` 订阅 SSE，`openRun` 只知道跳 task 或 conversation；而 `automationsApi.listGroups()` 的 `AutomationItem` 没有 `session_id/task_id/source_kind`。PLAN 只写“映射为自动化行并入 Running 区”，没有给 `ActivityEntry` 这类 discriminated union、自动化 Running 卡片渲染、历史行渲染和点击目标。期望改法：补清楚 Activity 装配后的类型、三种 source 的 render/open 分支，以及 automation row 点击到 `/automations/:automationId` 的实现边界。回代码核实：`frontend/packages/app/src/pages/ActivityPage.tsx:SourceFilter/matchesFilter/openRun/RunningCard` 只支持 chat/task `RunSummary`（43、174-181、389-414），`frontend/packages/core/src/api/runs-api.ts:RunSummary` 无 `automation_id`（21-38），`frontend/packages/core/src/api/automations-api.ts:AutomationItem` 无 `session_id/task_id/source_kind`（70-92）。
- P1 需修改: 菜单/动态非运行自动化的“按时间倒序”缺少真实排序 key。现有 Activity 历史按 `RunSummary.updated_at` 分桶/排序语义工作，Home 最近列表直接取 chat sessions 前 5 条；`AutomationItem` 只有 `last_run_at/next_run_at/last_run_status`，没有 `updated_at/created_at`。期望改法：明确自动化排序使用 `last_run_at ?? next_run_at ?? 0`、是否需要 detail/list 增补 `updated_at`，以及 running 同级时 chat/task/automation 的 tie-breaker。回代码核实：`frontend/packages/app/src/pages/ActivityPage.tsx:groupedHistory` 用 `updated_at`（549-553），`frontend/packages/app/src/pages/ConversationsHomePage.tsx:bootstrap` 取 `chatSessions.slice(0,5)`（175-190），`frontend/packages/core/src/api/automations-api.ts:AutomationItem` 字段只有 `next_run_at/last_run_at/last_run_status`（88-92）。
- P1 需修改: 直接复用 `CreateAutomationDialog` 做详情编辑会破坏 manual trigger。后端和前端类型都支持 `trigger.kind="manual"`，但弹窗编辑 manual 时会落到默认 cron UI，提交时 `buildTrigger()` 只能输出 cron/interval，等于一次编辑可把 manual 自动化改成默认 cron。期望改法：PLAN 要么要求弹窗支持 manual 且可无损 round-trip，要么在详情页检测 manual 后禁用编辑/只允许非 trigger 字段编辑。回代码核实：`backend/valuz_agent/modules/automations/schemas.py:ManualTrigger`（42-53），`backend/valuz_agent/modules/automations/models.py:ck_automation_trigger_kind` 允许 manual（43-45），`frontend/packages/app/src/components/CreateAutomationDialog.tsx` manual initial 被强制落到 cron（343-350），`buildTrigger()` 只返回 cron/interval（398-412）。
- P2 approve: PLAN 的 automations 调用链符号基本真实，未发现 `get/listRuns/runNow/update/delete/pause/resume`、路由处理器或 service 入口写到不存在符号；PRD 里的 `automationsApi.detail/.runs` 确实不是当前符号，PLAN 已纠正为 `get/listRuns`。回代码核实：`frontend/packages/core/src/api/automations-api.ts:automationsApi`（259-333），`backend/valuz_agent/api/routes/automations.py:get_automation/list_automation_runs/run_automation_now/update_automation/delete_automation/pause_automation/resume_automation`（176-251），`backend/valuz_agent/modules/automations/service.py:get_automation_detail/update/delete/pause/resume/run_now/list_runs`（427-431、770-936）。
- P2 approve: 路由落点方向成立：`desktop-routes.ts` 已有 `id: "automation"`，app route registry 用 `COMPONENT_MAP` 解析，desktop renderer 只是 re-export app routes；新增 detail route + app page export 是真实入口，不是死路由。回代码核实：`frontend/packages/core/src/edition/registries/desktop-routes.ts:personalDesktopRoutes`（167-174），`frontend/packages/app/src/routes/route-registry.ts:COMPONENT_MAP/resolvedDesktopRoutes`（26-74），`frontend/apps/desktop/src/renderer/routes/route-registry.ts` re-export（1-12），`frontend/packages/app/src/routes/router.tsx:createAppRouteObjects` 按 `layout="project"` 挂子路由（153-168）。
- P2 approve: OpenAPI 现状核实通过：`/v1/automations/*` 当前确实不在 `api/openapi.yaml`，而后端 FastAPI 路由已注册，前端类型为手写且 `AutomationRunItem.error_message` 与后端响应模型漂移。若做 `is_running`，必须先补 OpenAPI 再改后端/前端。回代码核实：`api/openapi.yaml:paths` 当前从 `/v1/runs` 开始且 `rg '/v1/automations' api/openapi.yaml` 命中 0（11-39），`backend/valuz_agent/api/app.py:create_app` include `automations_router`（19、103），`frontend/packages/core/src/api/automations-api.ts:AutomationRunItem.error_message`（125-129），`backend/valuz_agent/modules/automations/schemas.py:AutomationRunItemResponse` 未返回 `error_message`（191-211）。
**处理**（全栈开发逐条响应，已写入 plan.md；PM 决策 + 对齐 PRD v2）：
- P0「task 型运行态漏判」：**采纳并升 P0**。`AutomationItem.last_run_status` 对 task 型漏判（kickoff 后 run 冻结 `success`、活态在 `task_status`）是运行态正确性 bug，而 PRD 把菜单/动态运行态正确性列为 P0，故 `is_running` 由原 P1 升 **P0** 且**契约先行**：先补 `api/openapi.yaml` 的 `/v1/automations` 最小只读片段（`AutomationItem.is_running`）→ 再后端 `AutomationItemResponse`/`_row_to_item` 回填 `is_running`（=`last_run.status ∈ {queued,running}` 或解析后 `task_status==active`，复用 `_resolve_task_statuses` 对单条 last_run）→ 再 `make generate-types` 改前端。详情页仍用 `listRuns[0]` 判定（已正确，不变）。三处共用同一 `isAutomationRunning()` 语义，数据源不同（详情=`listRuns[0]`、菜单=`is_running`、动态=`/v1/runs` automation source）。→ plan §3.2 / §3.1。
- P1「Activity 行模型落地」：**采纳 PRD v2 的「`/v1/runs` 新增 automation 来源」**。装配后类型 = 扩展 `RunSummary`（`source_kind` 增 `"automation"`、增 `automation_id: string|null`），动态单管道仍吃 `RunSummary`；「从未运行」自动化由 `listGroups` 合成 `session_id=null`、状态「未运行」的列表行（不订阅 SSE）。三 source render/open 分支已列表化：task→`/tasks/:task_id`、chat(`project_chat`/`assistant`)→`/conversation/:session_id`、automation→`/automations/:automation_id`（`openRun` 前置 automation 分支，靠 `automation_id` 跳转，不依赖 session_id）；运行中的 automation run 复用 `RunningCard` 订 `run.session_id` SSE。判别联合 `ActivityEntry` 作为 fallback 记录。→ plan §4.1。
- P1「排序 key」：**明确两数据源 + 共用归一比较器**。`RunSummary`（动态/侧边 liveRuns）按 `updated_at`（与现有 `groupedHistory` 一致）、tie-breaker `session_id` 升序；`AutomationItem`（菜单/未运行合成行）按 **`last_run_at ?? next_run_at ?? 0`**、tie-breaker **`automation_id` 升序**。归一为 `{isRunning, activeTs, id}`，比较 `isRunning desc → activeTs desc → id asc`，菜单/动态/侧边共用。并标注：`AutomationItem` 无 `created_at`（PRD v2 排序梯度第三段 `created_at` 在 list 不可得），省该段，严格需要则补 list 字段（契约债，本版不补）。→ plan §4.3。
- P1「CreateAutomationDialog 破坏 manual trigger」：**确认为真实数据丢失 bug 并选定方案 (a)**。已核实 `CreateAutomationDialog.tsx:343-350` manual seeding 落默认 cron、`buildTrigger()`（398-412）只输出 cron/interval → 编辑 manual 会静默转 cron。方案 (a) 弹窗 manual 无损 round-trip：`triggerKind` 增 `"manual"`，seeding 保持 manual，`buildTrigger` 能输出 `{kind:"manual"}`，manual 在编辑模式为只读保留态。选 (a) 不选 (b) 的理由：`CreateAutomationDialog` 是总览+详情唯一共享编辑面，(a) 一次修复同时惠及 `AutomationPage`；(b) 仅在详情页绕过、连改 prompt 都受限。→ plan §2.1。
- 对齐 PRD v2「列表粒度」：原 §4「待设计/倾向聚合」**已删**，改为**每条自动化各占一行、恒显示、点行进详情**，否决聚合入口。→ plan §4.1/§4.2。
- 补决策「runNow 并发」：`isAutomationRunning()==true` 时「马上运行」disabled（tooltip「正在运行中」）；兜底 409（`AutomationAlreadyQueued/Running`）→ 错误 toast 展 `error_code`/`message`。→ plan §2.2。
- 补决策「删除跳转」：全局→`/automations`（`id:"automation"`）；项目→`/projects/:id`（`id:"project-detail"`，传 `project_id`）。→ plan §5.1。
- 补决策「项目已删降级」（P2 known edge）：后端仍返回详情则正常展+隐藏「回到项目」；无法解析则详情显「找不到/不可用」、列表跳过；本版纯前端降级、不改后端 DTO。→ plan §5.2。
- 契约债标注：`/v1/automations/*` 不在 OpenAPI（手写类型）、`AutomationRunItem.error_message` 后端不返回（`schemas.py:191-211`）——本版「先补 OpenAPI」纳入 `is_running` + `/v1/runs` automation 来源，并顺带登记/修正 `error_message` 漂移；list `created_at` 兜底仍记未还契约债。→ plan §7 / §8。
- P2 approve（调用链符号、路由落点、OpenAPI 现状）：维持，plan 符号均真实存在或明确将新增（`is_running`/`automation_id`/`source_kind="automation"`）。
- 结论：PLAN 第1轮 4 项 P0/P1 已逐条收口（is_running 升 P0+契约先行、Activity 行模型、排序 key、manual round-trip），可进入实现阶段。

---

## [2026-06-25] PRD 第2轮 — [结论：待修改]
**审查人**：产品经理 Reviewer

**第1轮 P0/P1 逐条复核**：
- P0「正在运行」判定：已解决。以当前 `prd.md` 为准，本版已收口为仅详情页判定实时运行态，唯一数据源为 `listRuns(id, 20)` 最新 run，并补了状态映射、`task_status` 优先级、`queued/running` 超时降级和 5s 轮询边界。
- P0「菜单/动态总是显示一条」粒度冲突：已解决。菜单逐条自动化移出本版；动态拍板为「自动化」Tab 内每条自动化一行，不进 Running 区。
- P0 范围与 API 决策自相矛盾：已解决。当前 PRD 明确本版不改后端/不改 `/v1/runs`，并把菜单/动态实时运行态和跨类型置顶降级到 v2 依赖。
- P1「全局 vs 项目」语义：基本解决。PRD 已补组合映射表，并声明徽章由 `project_kind` 单一驱动；但项目删除降级仍有实现矛盾，见本轮 P1。
- P1 权限模型：部分解决。owner-scoped 权限矩阵、非 owner 统一无权/找不到态已补；但「所属项目被删除后仍 owner 可见」与当前不改后端前提冲突，见本轮 P1。
- P1 历史记录范围：已解决。PRD 已从「完整历史」收口为「最近 20 次」，并在验收中指定 `listRuns(id, 20)` 与 `triggered_at` 倒序。
- P1 排序规则：已解决。PRD 已明确自动化 Tab 使用独立排序：`next_run_at` 升序、空值置后、`last_run_at/created_at/automation_id` tie-breaker；chat/task 现有排序不变。
- P1 验收标准：部分解决。大部分已改为 Given/When/Then，但 `runNow` 后展示文案、删除后跳转和 Activity 自动化数据口径仍不能直接落 QA 用例，见本轮 P1。

**第2轮意见**：
- P1: 「所属项目被删除」的产品结论与“不改后端”冲突。PRD 写“自动化仍 owner 可见，但隐藏回到项目链接”；但现有详情 DTO 生成依赖项目解析，项目不存在时无法返回完整详情，列表侧也会跳过这类自动化。按当前 API 形状，前端拿不到 `project_name/project_kind` 的可降级详情。期望改法二选一：要么本版改成“所属项目被删除后详情显示找不到/不可用，列表不展示”，并补验收；要么承认需要后端返回项目缺失降级字段，再把“不改后端/API”范围改掉。
- P1: `runNow` 后的展示验收和运行态映射表冲突。映射表写 `status=queued` 展示「排队中」，只是在逻辑上计入运行中；但验收写 `runNow→最新 run 出现且标「正在运行」`，而现有 `runNow` 接口返回初始状态就是 `queued`。同时 PRD 没说明已有最新 run 为 `queued/running` 时按钮是否禁用，或后端 409 应 toast 什么。期望补成可测规则：`queued` 到底显示「排队中」还是统一脉冲「正在运行」，`runNow` 对已排队/运行中的自动化是禁用、允许重试还是展示 409 文案。
- P1: Activity「自动化」Tab 的数据源和 Tab 口径未收口。PRD 写数据源 `automationsApi.list`，但当前前端只有 `automationsApi.listGroups()`；若坚持零后端改动，需要明确用 `listGroups()` 拉取并展平成自动化行。还要拍板 `all` Tab 是否包含自动化：新增 `automation` Tab 后，如果 `all` 仍只含 chat/task，需要写清；如果包含自动化，又要定义自动化在 History/分组里的排序和空态。否则实现和 QA 会各自猜。
- P1: 删除成功后的跳转目标自相矛盾。Agent 删除态一节写“成功后回总览 `/automations`（项目自动化若项目仍在，则回该项目自动化列表）”，但验收标准写 delete 后跳回 `/automations`。项目自动化列表的具体路由也没有给出。期望统一为一个绝对规则：全部回 `/automations`，或项目自动化回明确路由（例如项目页某个 Tab），并同步验收标准。
- P2: `ExecutionLog` 复用与新状态文案存在落地缺口。PRD 要求 `task_status=paused` 显示灰色「已暂停·等待继续」，`skipped/interrupted_by_shutdown` 也要中性区分；但当前 `ExecutionLogRow.status` 只有 `ok/err/skip/pending`，默认文案会合并到现有 skip/pending 语义。建议明确是扩展 `ExecutionLog` 状态/label，还是仅顶部徽章自定义、列表行沿用现有文案。
- P2: 自动化 API 契约债需要显式标注。项目规则写 OpenAPI 是 API 单一真相源，但当前 `/v1/automations/*` 不在 `api/openapi.yaml`，前端类型是手写。PRD 说本版不改 OpenAPI 可以接受为范围选择，但应在「复用与依赖」里明确这是沿用现有手写契约，不要求 `make generate-types` 覆盖自动化接口，避免开发按项目通用 API 流程误判。

**待拍板项**：
- 项目被删除后的自动化详情：本版按不可见/404 处理，还是允许后端返回降级详情。
- `runNow` 后 `queued` 的 UI 文案，以及已有 `queued/running` 时按钮和 409 错误处理。
- Activity `automation` Tab 是否用 `listGroups()` 展平；`all` Tab 是否包含自动化。
- 删除自动化后的唯一跳转目标，尤其项目自动化是否存在明确项目列表路由。

**处理**：退回产品负责人修改；P1 清零后再进入下一阶段。

---

## [2026-06-25] PRD 第2轮 — [结论：通过]
**审查人**：产品经理 Reviewer
**意见**：
- P0: 已解决｜运行态收口。PRD 已明确唯一真相源为该自动化「最新一次 run」；取后端 `is_running` / `latest_run` 摘要，退化时取 `listRuns(id)` 按 `triggered_at` 倒序 [0]；`isAutomationRunning(latestRun)` 由详情、菜单、动态三处复用；映射表覆盖 `queued/running/success+active/success+paused/failed/skipped/interrupted_by_shutdown`，且 `run.status` 优先、`task_status=paused` 不算运行中、stale queued 本版不做前端超时重判。
- P0: 已解决｜列表粒度。PRD 已拍板「每条自动化各占一行、恒显示」，否决聚合入口；菜单和动态点击自动化行均进入 `/automations/:id`；Activity 自动化 Tab 列出全部自动化，未运行自动化仍显示「未运行」；验收也用「2 条自动化各占一行」对齐。
- P0: 已解决｜范围自相矛盾。PRD 已去掉“不改 API 形状”的冲突口径，明确本版不改调度/执行核心逻辑，但允许最小只读契约新增，且契约先行：自动化 list/detail 回填运行态摘要，`/v1/runs` 新增 `automation` 来源类型。
- P1: 已解决｜全局 vs 项目。PRD 已收口为只按 `project_kind` 判定归属，`agent_kind` 仅作绑定来源标注；映射表覆盖 `chat+chat`、`project+chat`、`project+task`、`chat+task` 后端拒绝，并同步徽章、项目名、回项目导航和点击目标。
- P1: 已解决｜权限模型。用户故事已改为「创建者」；权限矩阵明确 owner 才能查看和操作 `runNow/pause/resume/delete/update`，非 owner 统一 403/404 无权限态且无操作按钮；项目其他成员可见/可操作不进本版。
- P1: 已解决｜历史条数。PRD 已从「完整历史」改为「最近 N 条运行记录」，验收明确 N=20、按 `triggered_at` 倒序，分页/加载更多不进本版。
- P1: 已解决｜置顶排序。PRD 已定义菜单与动态共用同一比较器：先按 `isAutomationRunning()` 分组，运行中整体置顶；组内按活跃时间戳倒序，自动化取 `last_run_at` → `next_run_at` → `created_at`；同时间戳按 `id` 升序稳定排序。
- P1: 已解决｜验收标准。验收已改为 Given/When/Then 结构，覆盖入口、详情内容、归属、编辑、操作反馈、运行态、失败/跳过/中断、动态恒显示、置顶排序、权限和 Agent 删除态；关键 UI 文案、可观测字段和 ≤5s 刷新阈值均可落 QA 用例。
- P2: 已解决｜异常边界。`agent_name=null` 的编辑/马上运行禁用、暂停/恢复/删除保留，`runNow` 失败 toast，删除成功跳回来源均已补入 PRD 和验收。
- P2: 建议｜只读契约字段在 PRD 层允许 `is_running` / `latest_run` 摘要二选一，不再阻断 PRD；但进入 OpenAPI 时必须定死字段形态，否则「三处复用同一判断」容易被实现成后端 boolean 与前端映射两套口径。
**待拍板项**：
- 无 P0/P1 待拍板项；仅保留上述 P2 的 OpenAPI 字段形态在技术方案阶段收口。
**处理**：第1轮 8 条 P0/P1 已逐条收口，无新增 P0/P1；PRD 第2轮通过，可进入下一阶段。

---

## [2026-06-25] PRD 裁决（PM 收口）— [结论：通过，采用设计 #2]

**审查人**：产品经理（Lead，最终拍板）
**背景**：本轮评审过程中出现两套互相矛盾的设计 lineage，导致 review.md 同时记录了「PRD 第2轮 待修改」与「PRD 第2轮 通过」两个相反结论：
- **设计 #1「不改后端」**：为守住"零后端改动"，砍掉菜单逐条自动化、把动态实时运行态与跨类型置顶降级到 v2。对应那条「待修改」是在审设计 #1，其 4 条 P1（项目删除降级、runNow 文案、Activity 数据源、删除跳转）均源于"不改后端"带来的能力缺口。
- **设计 #2「允许最小只读契约新增（契约先行）」**：`is_running`/`latest_run` 只读字段 + `/v1/runs` 新增 `automation` 来源类型，不动调度/执行核心。对应「通过」结论。

**裁决理由**：原始需求**明确要求**——"菜单和动态新加一种自动化任务类型、总是显示一条；有任务执行时和对话/任务一样显示正在运行；正在执行的对话/任务/自动化排到最前"。设计 #1 把这三点砍到 v2 = **未交付核心需求**；设计 #2 以最小只读、契约先行的字段在不碰调度/执行核心的前提下满足需求。故 **采用设计 #2**，当前 `prd.md` 即设计 #2 版本，为 PRD 最终稿。

**对那条「待修改」的处理**：其 4 条 P1 是针对已被否决的设计 #1，**随设计 #1 一并作废**；在设计 #2 下已被结构性解决（详情/菜单/动态共用 `is_running` 契约即可一致判定运行态、可降级项目缺失字段、排序共用比较器、删除按来源跳转已在 PRD 拍板）。

**PRD 线结论**：以设计 #2 收口，PRD 通过，进入 PLAN 收尾。

---

## [2026-06-25] PLAN 第2轮 — [结论：待修改]
**审查人**：全栈开发 Reviewer
**意见**：
- P0: 已解决｜`is_running` 方案已按真实 task 漏判链路收口，并明确契约先行。PLAN 已把 `last_run_status` 漏判 task 型的问题升 P0，要求先补 `api/openapi.yaml` 的 `/v1/automations` 只读片段和 `AutomationItem.is_running`，再让后端 `AutomationItemResponse` / `_row_to_item` 用 `last_run.status in {queued,running}` 或解析后的 `task_status == active` 回填，前端再读 `item.is_running`。这能覆盖真实缺口：当前 `_execute_task_kickoff` 会把 task run 写成 `success`，`_row_to_item` 只回 `last_run_status`，而 `task_status` 只在 `list_runs/_resolve_task_statuses` 中解析。依据：`docs/features/automation-detail-page/plan.md:151`、`docs/features/automation-detail-page/plan.md:160`、`backend/valuz_agent/modules/automations/in_process_runner.py:552`、`backend/valuz_agent/modules/automations/service.py:244`、`backend/valuz_agent/modules/automations/service.py:927`、`backend/valuz_agent/modules/automations/service.py:938`。
- P1: 未解决｜Activity 行模型仍有类型级落地缺口。PLAN 选择“扩展 `RunSummary`，单管道仍吃 `RunSummary`”，但又要求从未运行自动化合成 `session_id = null` 的 `RunSummary` 行；当前 `RunSummary.session_id` 是必填 `string`，OpenAPI `RunSummary.required` 也要求 `session_id`，且 `ActivityPage` 多处按非空 session 使用：`RunningCard` 直接 `useSessionEvents(run.session_id)`，history row 用 `run.session_id` 做 key/rename/delete，`canDelete = run.source_kind !== "task"` 会让 automation 行误走 chat 的 `sessionsApi.delete` 分支。三 source 的 open 分支已写清，但 render/action/type guard 尚未闭环。期望改法：要么改成明确的 `ActivityEntry` 判别联合并把 automation 合成行从 rename/delete/SSE 分支隔离；要么把 `RunSummary.session_id` 契约改为 nullable 并逐处加 `source_kind === "automation"` / `session_id` guard，同时补 `automation_id` required/nullable 规则。依据：`docs/features/automation-detail-page/plan.md:185`、`docs/features/automation-detail-page/plan.md:189`、`docs/features/automation-detail-page/plan.md:197`、`frontend/packages/core/src/api/runs-api.ts:21`、`api/openapi.yaml:2666`、`frontend/packages/app/src/pages/ActivityPage.tsx:199`、`frontend/packages/app/src/pages/ActivityPage.tsx:429`、`frontend/packages/app/src/pages/ActivityPage.tsx:451`、`frontend/packages/app/src/pages/ActivityPage.tsx:485`、`frontend/packages/app/src/pages/ActivityPage.tsx:511`。
- P1: 已解决｜排序 key 已改用真实存在字段并显式标注 `created_at` 契约债。PLAN 将 `AutomationItem` 排序落为 `last_run_at ?? next_run_at ?? 0`，tie-breaker 用 `automation_id`，并明确 `AutomationItem` 当前没有 `created_at`，若严格需要只能补 list 字段且本版不补。代码现状吻合：`AutomationItem` 只有 `next_run_at/last_run_at/last_run_status`，`AutomationDetail` 才有 `created_at/updated_at`。依据：`docs/features/automation-detail-page/plan.md:227`、`docs/features/automation-detail-page/plan.md:240`、`frontend/packages/core/src/api/automations-api.ts:70`、`frontend/packages/core/src/api/automations-api.ts:101`。
- P1: 已解决｜manual trigger 编辑不被破坏的方案成立。PLAN 已明确选方案 (a)：`triggerKind` 增 `"manual"`，编辑 seeding 保持 manual，`buildTrigger()` 输出 `{kind:"manual"}`，manual 编辑模式只读保留态。这个方案正对当前真实 bug：前端类型和后端都支持 manual，但现有弹窗 manual initial 会落回 cron，`buildTrigger()` 只能输出 cron/interval。依据：`docs/features/automation-detail-page/plan.md:84`、`docs/features/automation-detail-page/plan.md:102`、`frontend/packages/core/src/api/automations-api.ts:41`、`backend/valuz_agent/modules/automations/schemas.py:42`、`backend/valuz_agent/modules/automations/models.py:43`、`frontend/packages/app/src/components/CreateAutomationDialog.tsx:343`、`frontend/packages/app/src/components/CreateAutomationDialog.tsx:398`。
- P2: 已解决｜`runNow` 409 已有产品/技术处理，但实现时需按当前错误体读取。PLAN 写了运行中禁用、409 兜底 toast 展 `error_code/message`；后端确实在 latest run 为 `queued/running` 时分别抛 `AutomationAlreadyQueued/AutomationAlreadyRunning`，中间件返回 `{error:{code,message}}`。注意当前 `fetch-json` 只自动解析 `detail.*`，不自动暴露 `error.code`，实现 toast 时需解析 `ApiError.body` 或同步扩展 `fetch-json`。依据：`docs/features/automation-detail-page/plan.md:114`、`backend/valuz_agent/modules/automations/service.py:890`、`backend/valuz_agent/modules/automations/errors.py:113`、`backend/valuz_agent/api/middleware.py:70`、`frontend/packages/core/src/api/fetch-json.ts:58`。
- P2: 已解决｜删除跳转目标已对齐真实路由常量。PLAN 明确全局删除后回 `/automations`，项目自动化回 `/projects/:id`；当前 `desktop-routes.ts` 分别已有 `id:"automation"` / `id:"project-detail"`。依据：`docs/features/automation-detail-page/plan.md:267`、`frontend/packages/core/src/edition/registries/desktop-routes.ts:32`、`frontend/packages/core/src/edition/registries/desktop-routes.ts:167`。
- P2: 已解决｜契约债已显式标注。PLAN 已标明 `/v1/automations/*` 当前不在 OpenAPI、前端类型手写、`AutomationRunItem.error_message` 与后端响应漂移，并把 `is_running` 与 `/v1/runs` automation source 纳入先补 OpenAPI；list `created_at` 仍作为未还债记录。代码核实：`api/openapi.yaml` 当前无 `/v1/automations`，`RunSummary.source_kind` 仍仅 `assistant/project_chat/task`，后端 `AutomationRunItemResponse` 无 `error_message`。依据：`docs/features/automation-detail-page/plan.md:26`、`docs/features/automation-detail-page/plan.md:324`、`docs/features/automation-detail-page/plan.md:348`、`api/openapi.yaml:2666`、`backend/valuz_agent/modules/automations/schemas.py:191`、`frontend/packages/core/src/api/automations-api.ts:109`。
**处理**：退回修订 PLAN；未收口项为 P1「Activity 行模型与 render/action/type guard」，需补到可按当前 `ActivityPage.tsx` / `runs-api.ts` 类型实现后再复审。未重跑 test-all/typecheck/lint（doc-only）。

**处理（全栈开发本轮收口，已写入 plan §4.1，并同步 §技术摘要/§3/§3.1/§4.3/§6/§7/§8/验收映射）**：唯一未收口 P1「Activity 行模型 `session_id=null` 不兼容」按 PM 拍板**拆两条互不相交渲染路径，从根上免除 `session_id=null`**；回 `ActivityPage.tsx` / `runs-api.ts` 真实类型/分支（`runs-api.ts:11/22`、`ActivityPage.tsx:199/431/498/511`）核实后落地：
- **根因确认**：未运行自动化无 run、无 session，旧 PLAN 把它合成 `session_id=null` 的 `RunSummary`，与 `RunSummary.session_id`（必填 `string`，`runs-api.ts:22`）及 `RunningCard`(`useSessionEvents(run.session_id)`)、`historyRow`(key/`renameSession`/`sessionsApi.delete`)、`canDelete = source_kind !== "task"` 全部必填-session 分支冲突——即 reviewer 指出的缺口。
- **路径 A「自动化」Tab（独立渲染，不走 `RunSummary`、不需 `session_id`）**：数据源 `automationsApi.listGroups()` 的 `AutomationItem`，每条自动化恒一行（含未运行 / idle / 运行中），运行态读 `is_running`，点行→`/automations/:automationId`。行用**独立组件**，**不复用** `RunningCard` 的 SSE、`historyRow` 的 `renameSession`/`sessionsApi.delete` 分支（那些仅适用于有 session 的运行/历史卡）；automation 编辑/删除走详情页。
- **路径 B 跨类型 Running 组 + 置顶（仍走 `RunSummary`）**：仅「真有一次运行（有 `session_id`）且正在跑」的自动化以 `source_kind="automation"` + `automation_id` 经 `/v1/runs` 进入；该场景 session 必然存在，`session_id` 始终为真实 `string`。未运行自动化不属运行组、不产 `RunSummary`，此路根本不会 null。运行中 automation `RunSummary` 因必有 session 可安全复用 `RunningCard` SSE。
- **采纳 reviewer 二选一中的「隔离」而非「改 nullable」**：`RunSummary.session_id` **保持必填 `string`、不改 nullable**（OpenAPI `RunSummary.required` 不放宽，避免把「无 session」语义渗进通用运行管道 + 逐处加 guard）；契约仅新增 `RunSourceKind += "automation"` 与 `automation_id: string|null`。`canDelete`/`matchesFilter` 的 chat 分支收紧排除 automation（收紧为「仅 chat」），automation 与 task 同样不暴露行内 rename/delete。
- 原「扩展 `RunSummary` 合成 `session_id=null` 行」与「`ActivityEntry` 判别联合」之争被两路分离一并化解，均不再需要。
- 未重跑 test-all/typecheck/lint（doc-only）。结论：PLAN 第2轮唯一未收口 P1 已闭环（其余 P0/P1/P2 维持已解决），可复审。

---

## [2026-06-25] PLAN 第3轮 — [结论：通过]
**审查人**：全栈开发 Reviewer
**意见**：
- P1: 已解决｜Activity 行模型已收口为两条互不相交路径，不再把未运行自动化硬塞进 `RunSummary`。PLAN 现在明确路径 A「自动化」Tab 使用 `automationsApi.listGroups()` 的 `AutomationItem` 独立渲染，含未运行行，不走 `RunSummary`、不需要 `session_id`，也不复用 `RunningCard` / `historyRow` / `useSessionEvents` / `renameSession` / `sessionsApi.delete`；路径 B 仅让「有一次真实运行且有 `session_id` 的运行中自动化」以 `source_kind="automation"` + `automation_id` 的 `RunSummary` 进入 Running 组。依据：`docs/features/automation-detail-page/plan.md:206`、`docs/features/automation-detail-page/plan.md:210`、`docs/features/automation-detail-page/plan.md:220`、`docs/features/automation-detail-page/plan.md:223`、`docs/features/automation-detail-page/plan.md:226`。
- P1: 已解决｜`RunSummary.session_id` 不改 nullable 的决策与当前代码契约一致。当前 `RunSummary.session_id` 是必填 `string`，`ActivityPage` 也在 RunningCard SSE、running/history key、rename、delete 中直接按非空使用；PLAN 明确 `RunSummary.session_id` 保持必填 `string`，OpenAPI required 不放宽，未运行自动化不产 `RunSummary`，只新增 `RunSourceKind += "automation"` 与 `automation_id: string | null`。依据：`frontend/packages/core/src/api/runs-api.ts:11`、`frontend/packages/core/src/api/runs-api.ts:21`、`frontend/packages/app/src/pages/ActivityPage.tsx:199`、`frontend/packages/app/src/pages/ActivityPage.tsx:431`、`frontend/packages/app/src/pages/ActivityPage.tsx:487`、`frontend/packages/app/src/pages/ActivityPage.tsx:511`、`docs/features/automation-detail-page/plan.md:235`、`docs/features/automation-detail-page/plan.md:238`、`docs/features/automation-detail-page/plan.md:241`。
- P2: 已确认｜为避免 automation 运行中 `RunSummary` 误入 chat 分支，PLAN 已补 `openRun` automation 前置跳转、`canDelete` 收紧为仅 chat、`matchesFilter` chat 分支排除 automation；这些修改点正对当前 `ActivityPage` 的现有分支：`openRun` 非 task 默认进 `/conversation/:session_id`，`canDelete = source_kind !== "task"` 会暴露 chat rename/delete，`matchesFilter` chat 分支当前用 `source_kind !== "task"`。依据：`frontend/packages/app/src/pages/ActivityPage.tsx:389`、`frontend/packages/app/src/pages/ActivityPage.tsx:408`、`frontend/packages/app/src/pages/ActivityPage.tsx:431`、`docs/features/automation-detail-page/plan.md:229`、`docs/features/automation-detail-page/plan.md:231`、`docs/features/automation-detail-page/plan.md:261`。
**处理**：第2轮唯一未收口 P1「Activity 行模型与 render/action/type guard」已闭环，无新增 P0/P1；PLAN 第3轮通过。未运行 `test-all/typecheck/lint`（按本轮要求）。

---

## [2026-06-25] 收口说明（产品经理 / Lead 校正）

- **PRD**：第1轮 8 条 P0/P1 由 PM 逐条拍板决策，第2轮复审**通过**收口（共 2 轮，未超上限）。
- **PLAN**：第1轮 4 条 P0/P1 + 第2轮 1 条 P1（Activity 行模型）逐轮收口，第3轮复审**通过**（共 3 轮，恰在上限内，无人工介入）。
- **测试用例**：QA 先行 42 条 E2E（正常/运行态与排序/边界/异常）+ 开发补充 40 条单元/契约（后端 service 18 + API 契约 11 含错误码 + 前端纯函数 11），均回代码核验被测符号。
- **并发噪声澄清**：评审过程中 `prd.md` 一度被并发写入覆盖为一个「本版不改后端」的保守变体，故本文件存在一节针对该已被覆盖变体的「PRD 第2轮 — 待修改」记录（其结论与最终 PRD 不符）。**最终权威记录以针对当前对齐版 prd.md 的「PRD 第2轮 — 通过」为准**；现行 `prd.md` 为 PM 拍板的对齐版（允许只读契约新增、契约先行；每自动化一行；owner-scoped）。
- **结论**：PRD / PLAN 均评审通过，测试用例完成，P2 收敛达标，进入「待开发」。

---
