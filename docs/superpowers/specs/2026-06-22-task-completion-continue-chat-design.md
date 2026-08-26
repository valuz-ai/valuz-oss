# 任务完成后继续对话(收尾微调)设计

> **HISTORICAL (executed 2026-06; APIs renamed since).** 本文引用的
> `register_dispatch_tools()` 闭包机制已在 2026-07 重构中退役——现为
> `tools/handlers.build_task_tool_defs`(模块级 handler + 声明表 zip),
> orchestrator 方法经 `task_orchestrator.<service>.<method>` 访问。
> 本文仅作实施记录保留,不再反映当前代码。

> 状态:设计已确认,待落实现计划
> 日期:2026-06-22
> 范围:任务详情页 — 任务 `completed` 后,用户可继续与 lead 对话,基于交付结果做二次微调

---

## 1. 背景与目标

任务跑完后,lead 产出的交付结果有时不完全满意——措辞、格式、结论需要小幅调整。当前任务详情页是**只读时间线**,完成后底部操作栏整个隐藏,用户只能通过事件里"查看会话"链接跳到独立对话页才能接着聊,路径隐蔽、且脱离任务上下文。

目标:**在任务详情页内,让用户基于交付结果直接与 lead 继续对话、二次微调,改动落回交付物本身。**

这是"收尾微调",不是"重做任务"。重做有独立入口(`resume_task` / 修改目标 / 重试),不在本设计范围。

---

## 2. 核心洞察(技术验证结论)

向已完成的 lead session 直接发消息,**完全可行且后端几乎零改动**:

- `finish_task` 只把 lead session 的 `mode` 重置为 `default`、`status` 留在 `idle`,并广播 shutdown 让 actor 循环退出。**没有任何状态会阻止 `send_message`**(只挡 `cancelled`/`archived`)。
- lead actor 退出后**不会被重新唤起**;新消息走 kernel 标准 `SessionOrchestrator.run_turn()`,与 task 的 ActorRunner 是两条独立路径,不冲突。
- lead session 的 `agent_config`、`cwd`、上下文**完整保留**。`cwd` 是整个任务共享的 project 目录,**所有 member 产出的文件 lead 都能读能改**。

因此"继续对话"本身复用现有 `send_message(lead_session_id)` 即可,无需新后端逻辑。唯一的后端新增是让 lead 能把交付物更新反映回卡片的 `update_deliverable` 工具。

---

## 3. 设计决策总览

| 维度 | 决策 | 理由 |
|------|------|------|
| 对话性质 | 轻量对话式微调,lead 单兵作战,**不**重新唤醒团队、**不**改 task 状态 | 用户场景 99% 是基于现有产物的微调;重做有独立入口 |
| 位置 | 内嵌任务详情页,复用 `ConversationPage` 对话流组件 | 始终在"任务"上下文内,不让用户跳转 |
| 布局 | 完成态**翻转**:执行过程折叠上移、交付结果升主角并衔接对话 | 完成后用户关心"结果好不好 + 改一下",执行过程降为历史 |
| 后端·继续对话 | **零改动**,复用 `send_message(lead_session_id)` | 见 §2 |
| 后端·卡片刷新 | 新增 `update_deliverable`(lead-only MCP 工具),**追加** `deliverable_updated` 事件,前端取最新 | 交付卡片是 event-sourced,append-only 不破坏架构,更新有迹可循 |
| 输入框 | 精简版(文本 + 发送/停止 + 附件),固定 lead | 是"延续 lead 收尾",换 agent/model 无意义 |
| 开放范围 | **仅 `completed`** | `failed`/`blocked`/`stopped` 有各自的重试/resume 入口,不混入 |

---

## 4. 信息架构:完成态布局翻转

详情页是**状态驱动的两套布局**:

```
进行中(active/paused)            完成后(completed)
┌─────────────────────┐          ┌─────────────────────┐
│ 标题 / 状态 / Lead    │          │ 标题 / 已完成 / 时长  │
│                      │          │ ▸ 执行过程(5步全完成) │ ← 折叠成一行,点击展开
│ ┌─ 编排时间线 ─────┐ │          │ ┌─ 交付结果 ────────┐ │ ← 主角,上移
│ │ plan / subtask  │ │   翻转    │ │ artifacts + 总结  │ │
│ │ 实时进度…        │ │  ───────▶│ └───────────────────┘ │
│ └─────────────────┘ │          │ ──── 基于结果继续 ──── │
│                      │          │ ┌─ 收尾对话区 ──────┐ │
│ [修改目标/暂停/停止]  │          │ │ 你:… / Lead:…    │ │
└─────────────────────┘          │ └───────────────────┘ │
                                  │ [输入框 跟Lead说调整 ↑]│
                                  └─────────────────────┘
```

- **进行中**:编排时间线为主体,底部操作栏(修改目标/暂停/停止)——保持现状。
- **完成后**:执行过程(plan + subtask 时间线)折叠为一行摘要、默认收起;交付结果卡片上移为主角;下方分隔线 + 收尾对话区 + 常驻输入框。

---

## 5. 后端设计

### 5.1 继续对话 — 零改动

前端拿 `lead_session_id`(已在 task detail 响应中)调现有 `sessionsApi.send_message`。kernel 走标准 turn,lead 以 `mode=default` 作为普通 agent 回复,可读写共享 cwd 下所有文件。

并发由 `send_message` 现有门禁保证:lead 正在 `running` 时再发会抛 `SessionConflict`,前端据此把输入框转为"停止"态(与普通对话一致)。

### 5.2 `update_deliverable` 工具(唯一后端新增)

让 lead 在收尾改完交付物后,主动把最新的 summary/artifacts 反映回交付卡片。

**数据源洞察**:交付卡片读的是 `task_completed` 事件 payload 里的 `{summary, artifacts}`,不是 `result_manifest`。事件是 append-only。因此**不覆盖历史事件**,而是追加一条 `deliverable_updated` 事件;前端取"最后一条交付事件"(`task_completed` 或 `deliverable_updated`)。

**实现锚点**:

- `modules/tasks/tools/declarations.py`
  - 加 `UPDATE_DELIVERABLE_TOOL_NAME = "update_deliverable"`
  - 加参数 schema:`summary: str`、`artifacts: list[str]`
  - 加 `ToolDef` 声明,纳入 `DISPATCH_TOOL_DECLARATIONS` + lead-only 名单
- `modules/tasks/tools/handlers.py`
  - 加 `_update_deliverable_handler(args, ctx)`,先 `_check_lead_gate(ctx)` 校验调用者是 lead
  - 调 `orchestrator.update_deliverable(task_id, project_id, lead_session_id, summary, artifacts)`
  - 在 `register_dispatch_tools()` 注册
- `modules/tasks/orchestrator.py`
  - 新增 `update_deliverable(...)`:通过 `TaskEventDatastore.append_event()` 追加 `deliverable_updated` 事件,payload `{summary, artifacts}`
  - **不**改 `task.status`、**不**动 plan、**不**触碰 lead run 状态

**约束**:仅 task 处于 `completed` 时允许(handler 内校验);其余状态返回 error。

### 5.3 lead 引导(prompt)

在 completed 收尾对话语境下,引导 lead:"若你修改了交付物,调用 `update_deliverable` 用最新 summary 与产物清单刷新交付卡片。"挂在 lead 工具描述或收尾 system prompt 注入处。仅作引导,不强制——用户纯追问时 lead 不必调用。

---

## 6. 前端设计

### 6.1 详情页布局翻转(`TaskDetailPage.tsx`)

- 完成态(`status === "completed"`):
  - 执行过程时间线包进可折叠容器,默认收起,头部摘要"N 步全部完成 · 展开查看过程"
  - 交付结果卡片(现 853–957 行)上移到执行过程之后、对话之前
  - 新增分隔 + 收尾对话区 + 常驻输入框
- 进行中保持现状(时间线主体 + 底部操作栏)。

### 6.2 收尾对话区(复用)

复用 `ConversationPage` 既有能力:

- `buildTurns()` + `useStableTurns()`(@valuz/core)组织 turn
- `ConversationTurnList`(@valuz/ui)渲染
- 订阅 **kernel session SSE**(`sessionsApi`,针对 `lead_session_id`)驱动流式回复
- 精简输入框调 `sessionsApi.send_message(lead_session_id, …)`

注意:这与详情页现有的 **task event SSE**(`use-task-events`)是**两条独立流**,完成态同时挂载:
- task event 流 → 驱动执行过程时间线 + 交付卡片(含 `deliverable_updated`)
- lead session 流 → 驱动收尾对话区

### 6.3 交付卡片刷新

`completionInfo`(现 513–528 行)由"找 `task_completed`"改为"取最后一条 `task_completed` 或 `deliverable_updated` 事件"的 payload。lead 调 `update_deliverable` 后,task event SSE 推来新事件,卡片自动刷新。artifact 文件内容本就实时(点开读磁盘),summary 经此对齐到最新。

### 6.4 数据流

```
用户输入 ──▶ sessionsApi.send_message(lead_session_id)
            └─▶ kernel SessionOrchestrator.run_turn (mode=default)
                  └─▶ lead 流式回复 ──(session SSE)──▶ 收尾对话区渲染
                  └─▶ lead 改交付文件(共享 cwd)
                  └─▶ lead 调 update_deliverable
                        └─▶ append deliverable_updated 事件
                              └─(task event SSE)─▶ completionInfo 取最新 ─▶ 交付卡片刷新
```

完成态可移除 active 时的 3s 轮询,改由两条 SSE 驱动。

---

## 7. 边界与状态语义

- **仅 `completed` 开放**收尾对话;`failed`/`blocked`/`stopped` 维持现有入口。
- 收尾对话期间 **task 恒为 `completed`**:不重新计时、不回到 active、不重启 actor、不需要显式"结束调整"——随时想到随时再聊。
- lead 始终单兵:`update_deliverable` 只更新交付展示,**绝不**触发 plan/dispatch/member。
- 任务很久后回来仍可继续:kernel session 持久化,lead session 始终在。

---

## 8. 错误处理与并发

| 场景 | 处理 |
|------|------|
| lead 回复中用户再发 | `send_message` 抛 `SessionConflict`;输入框前置转"停止"态,屏蔽重复提交 |
| lead session 异常被归档/取消 | `send_message` 抛 `SessionNotRunnable`;详情页提示并隐藏输入框 |
| 非 lead 调 `update_deliverable` | `_check_lead_gate` 拦截,返回 error |
| 非 completed 调 `update_deliverable` | handler 校验拒绝 |
| `update_deliverable` 失败 | 工具返回 error,lead 在对话里如实反馈;卡片维持上一版 |

---

## 9. 测试策略

- **后端**:`update_deliverable` 单测——lead gate、completed 校验、追加 `deliverable_updated` 事件且不改 task 状态;`send_message` 到 completed lead session 正常跑 turn 的集成测试。
- **前端**:`completionInfo` 取最新交付事件的单测(`task_completed` → `deliverable_updated` 覆盖顺序);完成态布局翻转与折叠的组件测试。
- **端到端**:完成任务 → 详情页发消息 → lead 改文件 + 刷新卡片 → 卡片显示新 summary。
- 全程通过 `make test-all`、`make typecheck`、`make lint`。

---

## 10. 实现锚点(文件:行号)

| 功能 | 文件 | 行号 |
|------|------|------|
| 任务详情 API 组装 | `backend/valuz_agent/api/routes/tasks.py` | 227–242 |
| `send_message` 门禁(无 task 检查) | `backend/valuz_agent/modules/sessions/service.py` | 1009–1091 |
| kernel 标准 turn 入口 | `backend/kernel/src/core/orchestrator.py` | 394–555 |
| `mode=default` 不包装消息 | `backend/kernel/src/core/prompt_builder.py` | 83–127 |
| `finish_task`(mode 重置 + shutdown) | `backend/valuz_agent/modules/tasks/orchestrator.py` | 1288–1412 |
| MCP 工具声明 | `backend/valuz_agent/modules/tasks/tools/declarations.py` | 587–752 |
| MCP 工具 handler + lead gate | `backend/valuz_agent/modules/tasks/tools/handlers.py` | 99–123, 756–784, 990+ |
| `result_manifest` 定义 | `backend/valuz_agent/modules/tasks/models.py` | 120–155 |
| 详情页数据获取 + 轮询 | `frontend/packages/app/src/pages/TaskDetailPage.tsx` | 338–371 |
| `completionInfo`(交付卡片数据源) | `frontend/packages/app/src/pages/TaskDetailPage.tsx` | 513–528 |
| 交付卡片渲染 | `frontend/packages/app/src/pages/TaskDetailPage.tsx` | 853–957 |
| 完成态操作栏(待替换为输入框) | `frontend/packages/app/src/pages/TaskDetailPage.tsx` | 1163–1244 |
| task event SSE hook | `frontend/packages/core/src/hooks/use-task-events.ts` | 23–57 |
| 对话流组件 | `frontend/packages/ui/src/components/conversation/ConversationTurnList.tsx` | 组件整体复用 |
| 对话流组织(可复用) | `frontend/packages/app/src/pages/ConversationPage.tsx` | 144–200+ |

---

## 11. 非目标(YAGNI)

- 不做"重做任务/重新 dispatch member"——已有 `resume_task` / 修改目标 / 重试。
- 不对 `failed`/`blocked`/`stopped` 开放收尾对话。
- 不在收尾对话暴露 agent/model/runtime/skill 切换。
- 不引入新的交付物存储(manifest 可变)——沿用事件溯源。
- 不做交付物版本历史/diff——超出"微调"范围,未来可在 `deliverable_updated` 事件链上叠加。
