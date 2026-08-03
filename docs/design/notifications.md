# Notifications — 统一的持久化注意力账本

> **状态:N1(后端 durable 核心)+ N2(前端统一)已实现。** 原生桌面弹窗/dock
> 弹动需一轮真机走查。
>
> 取代临时拼装的 task-attention 通道（`/v1/tasks/attention` 轮询 +
> `NotificationBridge` 并行订阅）。把"需要用户注意/处理的事"抽象成一个
> **持久化的一等领域**：多来源汇入、单账本、多通道投递、可跨重启的读/已处理
> 生命周期。

## 0. 已定的方向（拍板结论）

1. **Decision Inbox（ADR-022）融入 Notification**——不是并存、不是泛化,而是
   **吸收**。`AskUserQuestion` 本身就是一种 notification（kind=`question`）;
   "提问"和"失败"是同一个账本里的两种 kind。Decision Inbox 这个独立概念消失,
   其 kernel-投影的好设计降级为 `question` 这一路 projector。
2. **一致性由后端保证,绝不甩给前端。** 后端 `NotificationService` 是唯一写者,
   对外只暴露**一个**账本 / 一条 SSE / 一个未读数。前端永远只订阅一个 store、
   渲染一个列表——**不存在"前端合并两个来源"这种事**。这是本设计的硬约束
   （否则消除了通道碎片又引入存储/读态碎片）。
3. **所有 kind 最终都能走到应用级通知 + macOS dock 图标弹动**（`app.dock.bounce`）。
   提问不再只有应用内 toast+徽章——窗口失焦时同样触发 OS 通知 / dock 弹动,与失败
   走完全相同的投递层。

## 1. 为什么必须是持久化的一等领域（而不是通道拼装）

从需求本身推：三个原始问题都归结为一句——**用户要在"没看着相关页面、甚至没开
应用"时,知道有事要他处理,并能回来处理。** 这决定了它不能是"事件发生时喷一下"
的临时通道,理由:

1. **离线丢失**：任务凌晨 3 点失败、应用没开,一次性 OS 弹窗要么没触发要么划过
   就没了。用户早上打开应用必须仍看到"你不在时任务 X 失败了"。临时 toast/通知做
   不到,持久化账本能。
2. **读状态要跨重启且一致**：徽章必须反映"真正还没处理"的数量,重启后不能全部
   归零、也不能把看过的重新算成新的。今天 decision store 的 `unreadIds` 是内存态,
   失败侧我用的 `notifiedRef` 也是内存态——重启即失忆。
3. **去重要跨重启**：我现在的失败轮询把游标 prime 到"挂载时刻",于是**应用关着
   时发生的失败永远不会被通知**(早于挂载时间)。持久化 + durable "已投递"游标才
   能补这个洞。
4. **"看过" ≠ "处理了"**：失败被"看到"和任务被"恢复"是两种状态,得能区分
   "已通知未处理" vs "已处理"。没有持久化就无法表达。
5. **历史/审计**：什么时候有什么需要我、我做了什么。

现状盘点:
- **提问**其实已经"事实持久化"——Decision Inbox 是 kernel `requires_action` 事件的
  投影,未 resolved 的问题重启后仍在(aggregator 开机 rehydrate)。缺的只是**读状态**
  的持久化。
- **失败**零持久化——`task_blocked` 是一次性事实,通知发完即忘。

## 2. 领域模型

一句话抽象:**Notification = {kind, 引用, 快照, 生命周期 unread→read→resolved,
可选 action + 自动消解触发}。** 提问和失败只是两种 kind。

durable 表 `valuz_notification`:

| 字段 | 说明 |
|---|---|
| `id` | uuid |
| `user_id` | owner，索引 |
| `kind` | `question` \| `task_failed` \| `task_stalled` \| `task_completed`(可选) … |
| `dedup_key` | 每 (user) 唯一，幂等汇入：`q:{pending_id}` / `f:{event_id}` |
| `title` / `body` | 创建时的快照——抽屉/历史无需再 join 就能渲染 |
| `route` | 点击深链（`/tasks/{id}` 等） |
| `action` | `answer` \| `resume` \| `none`（决定抽屉里渲染什么动作） |
| `task_id?` `project_id?` `session_id?` `pending_id?` `source_event_id?` | 引用，供动作 + 去重 |
| `created_at` | |
| `read_at?` | 用户看到（抽屉可见 / 点击通知） |
| `resolved_at?` | 已答复 / 已恢复 / 已忽略 / 自动消解 |
| `delivered_channels` | 已投递的通道位集（toast/os/badge），防重复投递 |

生命周期:`created → (read) → resolved`。`unread = read_at IS NULL`;
徽章 = 未 resolved 且 kind 需处理的计数(或未读计数,见 §6 待定项)。

## 3. 来源（projectors）——多源汇入同一账本

一个 `NotificationService.ingest(kind, dedup_key, …)`（幂等 upsert）被各来源调用:

- **提问源**:复用现有 `DecisionAggregator` 的 kernel-bus tap。
  - `requires_action(clarifying_questions)` 且 task-driven → `ingest(question, q:{pending_id}, action=answer, route=/tasks/{task_id})`
  - `action_resolved` → `resolve(q:{pending_id})`
  - （我已加的 `record_awaiting_user`/`record_user_answered` 任务事件保留——它是
    任务**时间线**的记录;通知账本是**注意力**的记录,两者正交。）
- **失败源**:任务失败事件的落点(`_auto_finalize_lead_task`、`TaskHealthMonitor`(2026-07-28 起在 `tasks/recovery.py`)、
  kickoff capability-gap)本就 append `task_blocked`/`kickoff_failed`。在同一处
  调 `ingest(task_failed, f:{event_id}, action=resume, route=/tasks/{task_id})`。
  - 可选:`resumed`/`abandoned` → `resolve` 对应失败通知。
- **完成源**:`finalize_task(status=completed)` 写入终态事件后调用
  `ingest(task_completed, c:{event_id}, action=none, urgency=info,
  route=/tasks/{task_id})`。摘要取终态事件 payload，幂等键绑定该完成事件。
- **未来**:automation 失败、长任务完成…… 各加一个 projector,不动投递层。

kernel 仍是"提问是否还 pending"的真相源;通知行只镜像它 + 增加读状态。开机
reconcile:对每条未 resolved 的 question 通知,校验 kernel pending 还在,否则标
resolved(和今天 aggregator 的 hydrate 同精神)。

**一致性在这一层收口**(§0.2):question 的 resolved 由监听 `action_resolved` 的
单写者(现 aggregator,迁入 NotificationService)同步;failure 的 resolved 由
`resumed`/`abandoned` 同步。前端拿到的永远是已对账的单一账本,不需要、也无权去
调和两个来源。

## 4. API

- `GET /v1/notifications?unread=&limit=&after=` — 列表（抽屉 + 开机快照）
- `GET /v1/notifications/stream` — SSE：**每帧都是完整 `snapshot`（`{entries, unread}`）**。
  投递是**DB 轮询**而非进程内扇出——stream 按间隔（`VALUZ_NOTIFICATION_POLL_SECONDS`，
  默认 2.5s）重读持久化的 `valuz_notification` 表，仅在开放集/已读态变化时推一帧新
  snapshot。前端把 snapshot 当整体 reset,无需 per-row 增量协议。**这是多 pod 正确性的
  关键**:进程内订阅表只能覆盖同一 pod 的 SSE 客户端,pod A 写入的通知到不了连在 pod B
  的流;持久账本共享(本地一份 SQLite、SaaS 一份 Postgres),DB 轮询流对每个 pod 都正确,
  无需共享总线(Redis pub/sub 是未来的 overlay 优化路径)。
- `POST /v1/notifications/{id}:read` / `:read-all`
- `POST /v1/notifications/{id}:dismiss`
- 动作本身走各自领域端点（答复 → `/sessions/{id}/actions`，恢复 →
  `/tasks/{id}:intervene action=resume`）；这些成功后经来源 `resolve` 消解通知。

扩展事件：`NotificationService.ingest()` 首次创建账本行后，以 best effort 发布
`notification.created`。稳定 payload 为 `owner_user_id` + 完整 `notification` wire
object + 当前 owner 的 `unread` 数量
对象；幂等 upsert 返回既有行时不重复发布。它只供 overlay 接外部系统通知等副作用，
不能替代持久账本或 DB-poll SSE，也不承诺跨进程重放。

`/v1/decisions/*` 与 `/v1/tasks/attention` 退役（前者的能力被 question-kind 覆盖，
后者被 stream 覆盖）。

## 5. 投递层（单一来源扇出所有通道）

一个 store + 一个 SSE 订阅驱动全部通道,不再有并行 provider。通道是**投递方式**,
与 kind 无关——每个 kind 都能走所有通道:

- **应用内**:徽章(未读数)、抽屉(列表,按 kind 渲染动作)、toast(实时 `added` 时一次)。
- **应用级 / 系统级**:OS 原生通知 + dock 角标(未读数) + **macOS dock 图标弹动**
  (`app.dock.bounce("informational")`,吸引注意但不打断)。仅在窗口隐藏/失焦时发
  OS 通知与弹动,可见时 toast 已够。
- 点击通知 → 主进程聚焦窗口 + 深链导航 → 标记 read。

投递策略由通知的**紧迫度**(而非 kind)决定:需要动作的(question/task_failed)
走全通道;纯信息的(可选的 task_completed)可只进抽屉+徽章、不弹 OS 通知。紧迫度
是 kind 的一个属性,配置在后端,前端照单渲染。

## 6. 前端整合

统一为:`notification-store`(zustand) + `use-notifications`(SSE 单例) +
`NotificationProvider`(toast + OS-notify + dock 角标) + `NotificationBadge` +
`NotificationDrawer`(按 kind:question → 复用 `DecisionEntryCard`/`AskUserQuestionCard`
内联答复;task_failed → 任务摘要 + 恢复按钮)。

**吸收现有代码**:
- `NotificationBridgeProvider` 删除 → 其 OS-notify 逻辑并入 `NotificationProvider`
  (提问不再二次订阅 store，消除 §上一轮指出的重叠)。
- `/v1/tasks/attention` 轮询删除 → 失败走通知 stream。
- Decision Inbox(store/badge/drawer/hook)**泛化**为 Notification 版;kernel 投影
  这个好设计保留为 question projector,不是推翻。
- `notification-content.ts` 纯构造器保留复用。
- `health_monitor` / `_heartbeat_pending subtask_failed` / awaiting_user 事件 / 带
  指令 resume——都不变,作为来源与动作继续用。

## 7. 分期 / 落地状态

1. **N1 durable 核心 — 已实现**:`valuz_notification` 表(migration 0020)+
   `NotificationService`(ingest/resolve/resolve_task/read/mark_all_read/dismiss,
   纯 durable 写入——无内存扇出,SSE 由 DB 轮询投递,多 pod 安全)+ 两个 projector
   (question 在 `decisions/aggregator` 的
   add/resolve 点、failure 在 `_auto_finalize`/`health_monitor`/kickoff 落点经
   `tasks/messaging.record_task_failure_notification`)+ REST/SSE
   `api/routes/notifications.py`。resume/abandon 时 `resolve_task` 清失败通知。
   测试:`tests/modules/notifications/`(service 6 + projectors 4)。
2. **N2 前端统一 — 已实现**:`core` 的 `notifications-api` / `notification-store` /
   `use-notifications`(SSE 单例);`app` 的 `NotificationInbox/`(Provider = toast +
   OS-notify + dock 角标 + **dock 弹动** + 点击导航;Badge;Drawer;按 kind 的
   `NotificationCard`:question→`AskUserQuestionCard` 内联答复,task_failed→恢复/忽略/查看)。
   ProjectLayoutBase 换用 Notification*;TaskDetailPage 的 taskPending 改读通知 store。
   **已删**:interim `NotificationBridge`、`/v1/tasks/attention`(前后端 + openapi)、
   整个 Decision 前端栈(store/hook/api/DecisionInbox 组件)。后端 `/v1/decisions/*` +
   aggregator 保留(aggregator 现为 question projector)。
3. **N3 打磨 — 待做**:原生弹窗真机走查;settings 通知开关;离线补投的更强语义;
   历史视图;可选 kind(长任务完成)。后端 `/v1/decisions/*` 端点已无前端消费者,可择机下线。

## 8. 已否决的替代方案（存档）

**"question 维持 kernel-derived、只失败落 durable 表、前端合并两个 store"——否决。**
理由即 §0.2:那样改动面看似小,但把一致性(哪些已读/已处理、离线补投、历史)甩给
前端去调和两套持久化模型,正是要消除的碎片化。**一致性必须在后端收口**,前端只面对
一个已对账的账本。统一账本是唯一选项。

## 9. 与"是否重构 task"的关系

本设计和待议的 task 重构是连着的:通知账本让"失败/等待"成为可持久化、可跨重启、
可跨设备的一等信号,是 task 可靠性叙事的对外出口。task 内部若重构(状态机、事件
词汇表、SSE 化),其对外"需要用户"的信号都应经这个账本,而不是各页面各自轮询。
留待架构讨论一并定序。
