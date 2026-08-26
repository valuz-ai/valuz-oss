# Task → Kernel 迁移

[English](task-kernel-migration.md)

> **状态:暂缓(2026-07-20)。** Task 子系统**暂定常驻 host** —— 本迁移当前不再
> 执行,host 侧 task 模块应按**终局形态**(而非过渡态)来治理。§2 的锁定决策保留,
> 作为未来若重启迁移时的参考基线。已落地的迁移前置 seam 独立成立、无论迁移与否都
> 保留:`tasks/resolution.py`(host 知识的会话解析,按 §5.1 形状)、
> `tasks/events.finalize_task`(组合式终态写入)、`tasks/tools/gate.py`(纯工具门禁
> 策略)。**不再计划执行**的部分:`valuz_task*` 表迁移、actor 迁入 kernel 侧运行、
> task MCP 工具由 kernel serve。
>
> **2026-07-28 补记:** 本文档中的模块清单反映重构前的目录布局。此后 host 侧已演进:`queries.py` 并入 `service.py`、`health_monitor.py` 并入 `recovery.py`、新增 `launcher.py`(唯一 actor 启动原语)、`plan_commands.py`(plan 写入唯一授权入口)、`member_state.py`/`outcome.py`(纯域)。若迁移复活,以当前代码树为准。

> Task 子系统**整体迁入 kernel**。它的表变为 kernel 所有(去掉前缀、保留
> `user_id`),并像 `sessions`/`messages`/`events` 一样通过 **DataService** 持久化。
> 它的 **actor**(lead + member、mailbox、live registry、recovery、watchdog)**完全
> 在 kernel 侧**运行与恢复——进程内或沙箱内。它的**内置 MCP 工具**在 kernel 侧
> serve。所有对 host 的 agent 库 / project / provider 的耦合被切断,替换为**注入式
> 抽象**,任务内部绝不直接查询 host 表。
>
> 本次重构是商业目标的**地基**:**SaaS 的 session 粒度沙箱起停/控制**,以及 host
> 进程 **actor 膨胀**问题的**实际**解决。
>
> 配套文档:[architecture.md](../architecture.md)(系统拓扑)、
> [data-service-architecture.md](data-service-architecture.md)(本方案复用的数据层)、
> [kernel-sandbox-deployment.md](kernel-sandbox-deployment.md)(沙箱供给)、
> [task-attention-and-reliability.md](task-attention-and-reliability.md)(本迁移不得
> 回退的可靠性缺口)。

---

## 1. 目标与非目标

### 为什么

今天 Task 子系统住在 **host** 进程里(`backend/valuz_agent/modules/tasks/`)。每个
lead、每个 member 都是 **host** 单一事件循环上的一个
`asyncio.create_task(run_actor_loop(...))`,由内存单例(`LiveMemberRegistry`、
`MailboxRegistry`、`TaskHealthMonitor`)协调,并由 host 启动扫描
(`recover_active_tasks`)重新水合。

在多租户 SaaS host 里,**所有租户的 task actor 共享一个进程**。这些状态是内存驻留、
不可分片的:它把每个用户的活跃任务钉死在某个 host 实例上,阻断横向扩容,还让一个租户
的任务扇出饿死其他租户。这就是 **actor 膨胀**问题。

商业目标是 **session 粒度沙箱**:每个用户的 agent loop(及其 task actor)跑在自己可
起停的沙箱里,而 host 保持无状态的控制 + 数据面。只要 task actor 还住在 host *里*,这
就不可能。**把 Task 子系统迁进 kernel 是几何上的前提**——它把 actor 放到 seam 的沙箱
一侧。

### 在范围内

- Task 表 → kernel 所有,经 DataService 持久化(host 侧 durable store)。
- Task actor 生命周期(spawn、协调、review、finish、**恢复**、watchdog)→ kernel 侧。
- Task 内置 MCP 工具 → kernel 侧 serve。
- Task ↔ host 耦合(agent 库 / project 成员 / provider / cwd / worktree / decision
  inbox / 通知 / 记忆)→ 切断,替换为注入端口或事件驱动的 host 反应。
- 完整功能等价(§9)+ 明确的回归面(§10)。

### 不在范围内(本次重构)

- 沙箱供给机制本身(归 `kernel-sandbox-deployment.md`)——本重构*消费*它。
- 改动 plan/DAG 语义、review 循环或工具契约的*行为*(只搬*位置*)。
- 云驱动(e2b / AGS)工作——归商业 overlay。

### 诚实的告诫

在 **in-process** kernel 形态(`make dev`)下,kernel 与 host 是*同一个*进程;把 actor
搬进去在那里**不会**减少进程内 actor 数。膨胀缓解**只**在
`kernel_mode=http` / per-session-sandbox 拓扑下兑现。本重构让那个拓扑*成为可能*;是部署
拓扑*兑现*收益。两点都要写进验收标准,以免事后误判结果。

---

## 2. 支配性决策(锁定)

以下是下文每一节都遵守的约束。

| # | 决策 |
|---|------|
| **D1** | Task 表迁**入 kernel**。保留 `user_id` 列;采用 kernel 命名——**去掉 `valuz_*` 前缀**(`valuz_task` → `tasks`,`valuz_task_event` → `task_events`,`valuz_task_session` → `task_sessions`)。 |
| **D2** | Task 表遵循与 kernel 三张表**同等处理**:经 **DataService** 持久化到 host 侧 durable store(SaaS 下是 PG),*无论* kernel 是否沙箱化。**因此查询接线仍终结在 host。** |
| **D3** | **actor 必须在 kernel 侧。** 其*整个*生命周期——spawn、协调、review、finish 及**恢复**——在 kernel(或沙箱)内运行。任何 task actor 都不在 host 进程里跑。 |
| **D4** | Task 对 **agent / project** 的依赖必须处理为**抽象的入口参数**(注入端口 / 已解析 spec)。Task 代码**不得**内部查询 host 侧表。 |
| **D5** | Task 内置 **MCP 工具在 kernel 侧 serve。** 沙箱化时,沙箱**暴露访问地址**——保持"本地持久化 + DataService 写回 host"语义与 kernel 自身表一致。 |
| **D6** | 产出本设计文档:完整**功能覆盖** + 预期**坑与回归**。 |
| **D7** | 北极星是 **SaaS session 粒度沙箱起停/控制 + actor 膨胀的真正解决。** 本次 task 重构是**地基**,不是终点。 |

---

## 3. 目标架构

### 3.1 什么搬、什么留

拇指法则:**执行 + 持久化 + 工具搬进 kernel;host 拥有的*知识*(agent、project、
provider、secret)留在 host,仅通过注入抽象访问。**

| 当前文件(`backend/valuz_agent/modules/tasks/`) | 层 | 重构后归属 |
|---|---|---|
| `plan.py`、`task_state.py`、`provenance.py` | Domain(纯) | **Kernel** `src/tasks/domain/` —— 直接搬,零 host 耦合 |
| `actor_runner.py`、`mailbox.py`、`live_member_registry.py` | Runtime(actor) | **Kernel** `src/tasks/runtime/` —— **D3**,与 `SessionOrchestrator` 同一事件循环 |
| `orchestrator.py`、`dispatcher.py`、`coordination.py`、`lifecycle.py`、`recovery.py`、`planning.py`、`messaging.py`、`queries.py`、`health_monitor.py`、`_session_build.py` | Services | **Kernel** `src/tasks/` —— actor 生命周期 + recovery + watchdog 在 kernel(**D3**);host 解析部分抽到 resolver 端口(**D4**) |
| `models.py`、`datastore.py` | Persistence | **Kernel** 存储 + **DataService RPC** ops(**D1**、**D2**)—— 重命名,保留 `user_id` |
| `tools/declarations.py`、`tools/handlers.py`、`dispatch_mcp.py` | Transport(工具) | **Kernel** serve MCP(**D5**);沙箱暴露地址 |
| `../../api/routes/tasks.py` | Transport(HTTP) | **Host** 公共 API,通过新增 `KernelClient` task 方法委托给 kernel;SSE 读 durable task 表(**D2**) |
| 它调用的 host 服务(decision-inbox、通知、记忆调度) | Side effects | **Host**,以**事件优先**方式由 task 事件驱动(§8),仅在同步回调不可避免处保留极薄的外发端口 |

### 3.2 seam 图景

```
┌───────────────────────────────────────────────────────────────────────┐
│  HOST(控制 + 数据面,对 task actor 无状态)                           │
│                                                                        │
│  api/routes/tasks.py ──► KernelClient.task_* ──┐   (公共 HTTP + SSE)   │
│                                                │                       │
│  MemberResolverPort  ◄─────── callback ────────┼──┐ (D4: agent /       │
│   (agent 库 · project · provider ·             │  │  project /         │
│    system-prompt · cwd/worktree · display)     │  │  provider 留 host) │
│                                                │  │                    │
│  DataService  /rpc/{op}  ◄──── write/read ─────┼──┼──┐ (D2: durable    │
│   (host SQLite ▸ 或 SaaS 下 PG)                │  │  │  store 在 host)  │
│                                                │  │  │                 │
│  decision-inbox · 通知 · 记忆  ◄───────────────┼──┼──┼── task events    │
│   (host 对 task 事件反应;§8)                   │  │  │                 │
└────────────────────────────────────────────────┼──┼──┼─────────────────┘
        进程内调用  或  HTTP/JWT(沙箱)          │  │  │
┌────────────────────────────────────────────────▼──▼──▼─────────────────┐
│  KERNEL(进程内 或 session/task 粒度沙箱)                              │
│                                                                        │
│  SessionOrchestrator            TaskOrchestrator  (新,兄弟)           │
│   sessions/messages/events       tasks/task_events/task_sessions        │
│                                                                        │
│  src/tasks/runtime/   actor_runner · mailbox · live_member_registry     │
│    ▸ lead + member actor(asyncio,单一 loop)   ── D3 ──               │
│    ▸ recovery 扫描 + health watchdog 在 KERNEL boot                     │
│  src/tasks/            dispatcher · coordination · lifecycle · planning  │
│  kernel MCP toolkit    dispatch · plan_task · review_subtask · finish …  │  ── D5
│    (kernel 侧 serve;沙箱发布其地址)                                    │
└────────────────────────────────────────────────────────────────────────┘
```

三条 seam,全是既有范式——**不新造架构原语**:

1. **DataService**(`POST /rpc/{op}`)—— task 表沿用 kernel 三张表已用的完全相同机制
   (`data-service-architecture.md`)。两个旋钮(执行位置、后端)不变;只加 task ops。
2. **MemberResolverPort** —— 一条 kernel→host 回调,与"沙箱化 kernel 已经回调
   DataService 取存储、回调 toolkit MCP server 取工具"同构。进程内是直接 host 对象;
   沙箱下是对 host 的 HTTP/JWT 回调。
3. **KernelClient task 方法** —— host 公共 HTTP 路由通过既有 `KernelClient` 协议(双
   传输)委托,按 1:1 扩展新增 task 内核 API(`InProcessKernelClient` 路径本就*是*直接
   调用)。

---

## 4. kernel 内的数据模型(D1、D2)

### 4.1 重命名、保留 owner 的表

三张表迁为 kernel 所有。除重命名外列原样保留;`user_id` **保留**(kernel 表本就带
`user_id`——owner 在 **store/DataService 边界从验证过的 JWT** 盖章,绝不上浮进
`ExecContext`,因此这**不违反**"kernel runtime owner-agnostic"规则)。

| 原(`valuz_*`) | 现(kernel) | 保留 |
|---|---|---|
| `valuz_task` | `tasks` | `user_id`、`plan`(DAG JSON)、`plan_version`(CAS)、`status`、`trigger_*`、`metadata_` |
| `valuz_task_event` | `task_events` | `user_id`、per-`(user, task)` 单调 `sequence`、`type`/`actor`/`session_id`/`payload` |
| `valuz_task_session` | `task_sessions` | `user_id`、`session_id`(→ kernel `sessions.id`)、`kind`、`subtask_key`、`result_manifest`、`run_dir` |

Alembic 归属从 host 链移到 **kernel Alembic 链**。迁移必须**可逆**(仓库规则)且
**保数据**——复用 `boot/kernel_db_colocate.py` 先例(backup → copy → verify),而非
drop-and-recreate。

### 4.2 DataService RPC 扩展(D2)

每个新增 StorePort 方法加一个 `POST /rpc/{op}` op,镜像现有的
`save_session`/`load_session`/`append_event`/`get_events_after` 形状:

```
save_task · load_task · list_tasks · list_active_tasks(system,跨 task)
append_task_event · get_task_events_after · get_task_events_window
save_task_session · load_task_session · list_task_sessions · update_task_session_by_session
```

owner 从**验证过的 bearer token**(`_owner_dep`)取,绝不从请求 body——与 kernel 表 ops
一致。PG 上同样用 `install_rls_guc` 逐事务 `SET LOCAL app.current_user_id` 在 DB 层兜底
owner 隔离。**host durable store 始终是唯一真相源;kernel 是否沙箱化只改变*传输*(进程内
调用 vs HTTP/JWT),绝不改变写入目标**(D2)。

### 4.3 kernel 侧双写、host 终结的查询(D2)

三张 task 表**kernel 侧双写**——本地 kernel DB **加上** DataService durable store——与
`sessions`/`messages`/`events` 在 `WriteThroughStore`(`authority="durable"`)下完全一
样。这适用于 task 所有的表(`tasks` / `task_events` / `task_sessions`),**不**适用于任何
host 侧的 project↔task 关联表(它留在 host)。本地 buffer 给沙箱内 actor 低延迟读;host
上的 durable 副本是 wire 真相源。

公共读/流路径留在 host:

- `GET /v1/tasks`、`GET /v1/tasks/{id}`、`GET /v1/tasks/{id}/events`、
  `.../events/stream` 保持 **host** 路由。
- 它们经 **host durable store**(DataService 写入目标)读 task 行/事件——host **不**深入
  kernel 进程读。现有 500ms DB 轮询 SSE(`_iter_task_events_sse`)对 `task_events` 照常
  工作(仅表名变)。
- **跨库 seq 告诫按设计成立。** 与 `sessions`/`events` 一样,本地 buffer 与 durable store
  携带分叉的 id/计数(常量偏移 = 健康的双写);**wire 只暴露 durable `sequence`**,跨库
  身份用 `event_uid` 而非 `seq`。不要试图让两库计数一致。

---

## 5. 切断 host 耦合 → 注入抽象(D4)

今天 `build_member_session`(在 `adapters/agent_resolver`)是 task 代码触达 agent 库、
project 成员、provider、能力(skills/MCP)解析和 system-prompt builder 的唯一漏斗。再加上
散落的对 `ProjectMemberDatastore` / `ProjectDatastore` / `ProviderDatastore` 的直读以及
cwd/worktree 辅助。**这些全是 kernel 不该查的 host 知识。** D4 把它变成一个注入端口。

> **不是工具。** `MemberResolverPort` 是**内部管道**,LLM 看不见。它与 kernel 侧 serve 的
> Task **MCP toolkit**(§6)不同,后者是*面向 agent*的界面(`dispatch`、`plan_task`…)。
> 流程:lead agent 调 `dispatch` MCP 工具 → 该工具的 **handler** 在代码里调
> `MemberResolverPort` 拿到 `ResolvedSession` → spawn actor。工具是给 agent 看的脸,
> resolver 是 handler 用的管道。沙箱下 resolver 走它**自己专用的 host `/rpc` 风格端点**
> (DataService JWT 模型),**不**占 toolkit MCP 通道——解析是数据形状,不是工具形状。

### 5.1 `MemberResolverPort`(host 实现,kernel 调用)

```
MemberResolverPort:
  resolve_member_session(project_id, agent_slug, goal, *, run_kind) -> ResolvedSession
      # AgentConfig 快照 + skills + mcp_servers + model/provider + system prompt + cwd
  resolve_lead_session(project_id, lead_agent_slug, ...) -> ResolvedSession
  resolve_display_name(agent_slug) -> str
  resolve_role_summary(agent_slug) -> str
  resolve_project_cwd(project_id) -> Cwd            # 含 worktree healing/snapshot
  credential_gap(resolved) -> Optional[Gap]         # oauth vs keyed provider 检查
```

- **进程内 kernel:** 端口是一个薄薄的 host 对象,包住今天的 `build_member_session`——
  行为一致,只是搬到接口后面。
- **沙箱化 kernel:** 端口是**对 host 的 HTTP/JWT 回调**(与 DataService 同信任模型:沙箱
  持 token + URL,绝不持 DB DSN 或 agent 库)。这是既有"沙箱回调 host"三角的第三条腿
  (存储、工具、**解析**)。
- task 引擎**收到一个完整的 `ResolvedSession`**,永远不知道它是*如何*被解析的。
  `ProjectMemberDatastore` / `ProjectDatastore` / `ProviderDatastore` 的 import 从 task
  代码中**删除**(这也顺带修掉既有的跨模块 datastore 边界违规)。

**§13 是完整的 P3 契约**——精确 Protocol、`/rpc` wire、双传输、同步不变量时序规则、错误
分类、契约测试。本节是概览;从 §13 开工。

### 5.2 解析时序保住 actor 不变量

关键:`LiveMemberRegistry` 要求 **`create_task(spawn)` 与 `add_member(...)` 之间不得有
`await`**(否则并发的 `finish_task` 会丢掉刚 spawn 的 member)。因此解析(异步 / 可能是
HTTP 回调)必须在**同步 spawn 块之前**完成:

```
resolved = await resolver.resolve_member_session(...)   # 异步,可能调 host
# ── 同步块,无 await ──────────────────────────────────
mailbox.register(lead); registry.add_member(task, member_id)
mailbox.register(member); asyncio.create_task(run_actor_loop(resolved))
# ─────────────────────────────────────────────────────
```

这让整块 spawn 即便解析往返到 host 也**原子地留在 kernel 事件循环内**——正是*整块* actor
机器必须作为一个单元搬迁(D3)、dispatch 不可横跨 host/kernel seam 的承重原因。

### 5.3 cwd、worktree、文件系统

kernel 本就管理 `project.cwd` 下的子树,并通过 `project_cwd(...)` 收到已解析的 cwd;subrun
目录分配与 worktree healing 变成 `resolve_project_cwd`/`resolve_member_session` 输出的一
部分。沙箱路径投影(`integrations/sandbox_runtime.py` / `MountGrant.kernel_cwd`)不变——
resolver 返回 host 路径,既有投影层把它 stage 进沙箱 mount。

---

## 6. 工具在 kernel 侧 serve(D5)

lead-agent 工具面(`dispatch`、`await_members`、`send`、`list_members`、`finish_task`、
`update_deliverable`、`stop_subtask`、`plan_task`、`get_plan`、`modify_plan`、
`review_subtask`)加上 base 编排集(`create_task`、`draft_task`、`commit_task`、
`abandon_task`、`inject_into_task`、`resume_task`、`list_tasks`、`get_task`)迁到
**kernel serve 的 MCP toolkit**。

- **handler 从 session 读 owner,不从 `ExecContext`。** 每个 task 动作都跑在一个带
  `user_id` 的 kernel session 上下文里;handler 读 `session.user_id`。这在保住
  owner-agnostic `ExecContext` 的同时给了 handler 需要的 owner——替换今天 host 的
  `HostExecContext(ExecContext)` 注入。
- **lead-gate 变成 kernel 状态检查。** `_check_lead_gate` / `_check_plan_writer_gate`
  逻辑键于 task-session 角色(lead vs member),这现在是 kernel 所有的状态——比今天仅在
  handler 的 gate 更干净。
- **沙箱暴露 toolkit 地址(D5)。** kernel 沙箱化时,其 MCP toolkit 在沙箱发布的地址可达,
  与 DataService 写回、resolver 回调同样接线。"本地持久化 + DataService 写回 host"语义与
  kernel 自身表的行为一致——所有 kernel 状态一个心智模型。
- 从工具 handler 发起的解析走 `MemberResolverPort`(§5),所以即便 kernel serve 的
  `dispatch` 也绝不直接碰 host 表(D4)。

---

## 7. actor 生命周期与恢复,kernel 侧(D3)

actor 生命期内的一切都迁进 kernel,由一个与 `SessionOrchestrator` 兄弟的新
`TaskOrchestrator` 拥有(同进程、同 loop、同 runtime-cache 纪律):

- **spawn / 协调 / review / finish** —— `actor_runner`、`mailbox`、
  `live_member_registry`、`dispatcher`、`coordination`、`planning`、`lifecycle` 在
  kernel 侧跑。member 复用 kernel 的暖 runtime 缓存(per-`session_id`,idle-TTL + LRU),
  在 per-task 沙箱里天然有界。
- **kernel boot 时恢复。** `recover_active_tasks` 从 host lifespan 移到 **kernel boot**。
  沙箱化 kernel 在启动时经 DataService 恢复*它自己*的任务,从
  `tasks`/`task_sessions`/`task_events` + DeepAgents checkpoint
  (`FileCheckpointSaver` / 沙箱内 COS)重新水合 actor。它必须与
  **snapshot/resume config-gate** 微 VM 路径互操作:恢复在 config 应用**之后**跑,并与
  checkpoint 对账。
- **watchdog 在 kernel 侧。** `TaskHealthMonitor` 在 kernel 跑;当某 task 仍 `active` 而其
  lead mailbox 连续 N 次扫描未注册时标记为 `blocked`。`is_draining` 变成 *kernel* 的排空
  标志。
- **finish_task 去重。** 今天有两份 `finish_task` 实现(一份活、一份死,§10.2);搬之前
  对着调用图确认哪份是死的,**只搬活的那份**进 `LifecycleService`。

---

## 8. 事件、副作用与 decision inbox(D2 + D4)

今天三个 host 服务通过懒 import 对任务进展做出反应:**decision inbox**(AskUser)、
**通知台账**(OS 通知 / 角标)、**记忆调度器**(finish 后抽取)。搬迁后它们变成**事件优先
的 host 反应**,把新增外发端口降到最少:

- task 事件经 DataService 写入 host durable store(§4)。host **订阅 / 轮询** task 事件
  (它本就拥有查询路径,D2)并驱动自己的副作用——通知或记忆调度都不需要同步的 kernel→host
  调用。
- **AskUser 简化。** "某 member 卡在澄清问题上"是一个 **kernel 原生**事实(runtime 的
  pending-action / `AskUserQuestion` approval 状态)——kernel actor 从*它自己*的
  session/event 状态检测,而非读 host decision aggregator。host decision inbox 变成对
  `awaiting_user` / `user_answered` **task 事件**的纯**读投影**。`record_awaiting_user` /
  `record_user_answered` 写入变成 kernel 侧发出的 task 事件。
- 只有在确实需要同步 host 确认处才保留一个薄的外发端口;默认走事件驱动。
- **member 归属事件必须在 emit 时盖 `payload.agent_name`**(经 `resolve_display_name`),
  这样前端永不用对着一个 racy 的 members 列表重新解析名字。

---

## 9. 重构后的功能覆盖(D6)

每一项当前能力映射到重构后归属。**等价性是验收线**——本列不得回退。

| 能力 | 今天 | 之后 | 由什么保证 |
|---|---|---|---|
| Plan DAG 编写 / 校验 / `ready_keys` | `plan.py`(host) | kernel domain | 纯搬;单测一起搬 |
| Task 状态机 + `assert_transition` | `task_state.py` | kernel domain | 纯搬 |
| Trigger provenance | `provenance.py` | kernel domain | 纯搬 |
| Kickoff / draft / commit / abandon | `lifecycle.py` | kernel `TaskOrchestrator` | 行为一致;解析经端口 |
| Dispatch(sync + async + batch) | `dispatcher.py` | kernel runtime | 搬前**确认哪些 dispatch 路径是活的** |
| Lead↔member 协调 / heartbeat / probe | `coordination.py` | kernel runtime | AskUser probe 现为 kernel 原生(§8) |
| Actor loop / turn-to-idle / manifest 收集 | `actor_runner.py` | kernel runtime | 同 loop、同 TTL 常量 |
| Mailbox / InboxMsg / shutdown | `mailbox.py` | kernel runtime | 进程本地,不变 |
| Live member registry(spawn/drain 不变量) | `live_member_registry.py` | kernel runtime | **同步不变量保住**(§5.2) |
| Review(approve / rework) | `planning.py` | kernel | `plan_version` CAS 保住 |
| Messaging / inject / goal-revise | `messaging.py` | kernel + host 事件 | 副作用事件驱动(§8) |
| 读查询(list/get/activity) | `queries.py` | host 读路径 | 读 durable store(D2) |
| Recovery 扫描 | `recovery.py` | **kernel boot** | D3;snapshot-resume 感知 |
| Health watchdog | `health_monitor.py` | kernel | D3 |
| Task 表 | host `valuz_*` | kernel,经 DataService | D1/D2;可逆迁移 |
| Lead + base MCP 工具 | host toolkit MCP | kernel toolkit MCP | D5;owner 从 session |
| 公共 HTTP + SSE | `api/routes/tasks.py` | host,委托 | KernelClient task 方法 |
| Decision inbox / 通知 / 记忆 | host 懒 import | host,事件优先 | §8 |
| Worktree / subrun 目录 | `fs_registry`/`worktree_service` | resolver 端口输出 | 投影层不变 |

**趁此一并收拢:** 三个已知可靠性缺口——`failed` 任务状态接线、stop/pause 前端**de-spin**
(stop 只 park `in_progress` 而 `in_review`/`rework` 仍在转)、watchdog/OS 通知覆盖——应作为
搬迁的一部分闭合,而非原样搬过去。见 `task-attention-and-reliability.md`。

---

## 10. 坑与回归观察(D6)

具体故障模式,按咬人程度排序。

1. **`LiveMemberRegistry` 同步不变量。** 所有 registry 方法同步;`create_task(spawn)` 与
   `add_member` 之间不得有 `await`。解析(异步 / HTTP 回调)必须在 spawn 块*之前*完成
   (§5.2)。违反即重现"finish_task 丢掉刚 spawn 的 member"竞态。**这就是整块 actor 机器
   作为一个单元搬迁、dispatch 不可横跨 seam 的原因。**
2. **`finish_task` 有死副本。** 搬前对着真实调用图核实活/死副本;别搬死的那份。
3. **stop/pause de-spin 缺口。** `stop` 只 park `in_progress`,但 panel 把
   `in_review`/`rework` 映射成 spinning → 任务停了子任务还在转。搬迁时闭合前端 de-spin;
   加回归测试。
4. **snapshot/resume 下的恢复时序。** kernel-boot 恢复必须在 config-gate 应用之后跑,并与
   DeepAgents checkpoint(沙箱内 COS `FileCheckpointSaver`)对账。测试:任务中途杀掉沙箱
   → resume → actor 重新水合、plan 继续。
5. **数据迁移必须可逆 + 无损。** `valuz_task*` → kernel 表跨库。用 backup→copy→verify
   (`kernel_db_colocate` 先例),绝不 drop-and-recreate;绝不用 `git stash` 做基线。
6. **task-event `sequence` 权威(双写)。** task 表双写(本地 + durable),本地与 durable
   id/计数**按设计分叉**(常量偏移 = 健康)。wire **只暴露 durable seq**;跨库身份用
   `event_uid`。别"修"计数不一致——那正是 kernel `events` 表已记录的坑。
7. **resolver 回调延迟与失败。** 沙箱下 `resolve_member_session` 是网络跳;dispatch 现在有
   了外部依赖。显式处理 timeout / host 不可达(干净地失败该次 dispatch,发
   `kickoff_failed` / `subtask_failed`,别 wedge lead loop)。`credential_gap` 预检必须
   经得住往返。
8. **member 名字归属。** emit 时盖 `payload.agent_name`;被删/改名的 agent 在时间线上仍要
   显示名字。
9. **owner 泄漏。** handler 必须从 `session.user_id` 读 owner,而非重新引入一个携带 owner
   的 `ExecContext`。保持 `ExecContext` owner-agnostic;owner 只活在 DataService/store
   边界(JWT)和 session 上。
10. **两条 SSE 血统别缠在一起。** task SSE 保持 host DB 轮询 `task_events`;**别**随手把它
    并进 kernel per-session event bus(那条路有 stuck-loading / seq bug 的历史)。分开。
11. **测试沙箱围栏。** upstream 近期收紧了沙箱逃逸与 ambient-DB-url 测试(#526/#532 血统)。
    新 kernel task 表 + DataService ops + resolver 回调若不走 sanctioned store/URL 接线就
    会绊到它们。早跑沙箱测试套件,别放最后。
12. **`make dev` 看不到膨胀缓解。** 进程内 kernel = 同一进程;验收必须断言缓解只在
    `kernel_mode=http` 拓扑(§1 告诫),且两种拓扑都过完整 task 套件。

---

## 11. 分阶段迁移(依赖在括号内)

**P0 — 契约与 schema 骨架** *(阻塞全部)*
在 `KernelClient` + `api/openapi.yaml` 定义 task 方法面(create/draft/commit/dispatch_member/
await/review/finish/list/get/events);双传输(in-process + http)都实现空壳 + 契约测试。
定 kernel 侧 task 表 schema + 可逆 Alembic(建表,不迁数据)。定 `MemberResolverPort` 接口 +
host impl stub。

**P1 — 纯域搬迁** *(P0)*
`plan.py`、`task_state.py`、`provenance.py` → kernel;搬它们的单测。最低风险,热身 seam。

**P2 — actor 机器入 kernel** *(P1)* —— **核心,交付 D3**
搬 `actor_runner`、`mailbox`、`live_member_registry`、coordination 执行部分、dispatcher
(执行部分)、planning、lifecycle → kernel `TaskOrchestrator`。**守住 §5.2 同步不变量。**

**P3 — resolver seam** *(P2)* —— **交付 D4** —— 完整契约见 **§13**。
落 `MemberResolverPort`;从 task 代码删除 `ProjectMemberDatastore`/`ProjectDatastore`/
`ProviderDatastore`/`build_member_session` 的 import。先 in-process impl;后沙箱 HTTP/JWT
回调 impl。

**P4 — 表与数据迁移(经 DataService)** *(P0, P2)* —— **交付 D1/D2** —— 完整详见 **§15**。
加 task RPC ops;datastore 换到 kernel store/DataService;数据 backup→copy→verify 迁移;
owner 在边界盖章。

**P5 — 工具入 kernel** *(P2, P3)* —— **交付 D5** —— 完整详见 **§16**。
把 task toolkit 迁为 kernel 原生工具;handler 从 session 读 owner;lead-gate 走 kernel
状态;沙箱在沙箱本地地址 serve toolkit。

**P6 — 事件 / 副作用 / 公共 API / 实时中断** *(P4, P5)* —— 完整详见 **§17**。
task 事件事件优先 host 反应(decision inbox 作读投影、通知、记忆、activity 索引)。
`api/routes/tasks.py` 委托给 `KernelClient`(前端面保留);SSE 读 `task_events`。实时
stop/interrupt 作为单次 kernel 内扇出。

**P8 — session/task 沙箱起停控制** —— **本次不在范围**,是**商业版**关切(§18)。本重构只
保证 task=sandbox 语义(§14.1),供控制面在其上构建。

**P7 — 验证** *(全部)*
`make test-all` / `make typecheck` / `make lint` 全绿(硬门)。**无 task-engine feature
flag / 双跑回退**——一次性 cutover,安全网是**全链路覆盖**:端到端 plan→dispatch→review→
finish + 实时 interrupt/stop + **沙箱重启后恢复**,在 **in-process 与
`kernel_mode=http`/沙箱两种拓扑**都跑。在 http 拓扑显式断言 SaaS 膨胀缓解验收标准。早跑
沙箱围栏套件(#526/#532 血统)(§10.11)。

---

## 12. 已定决策

1. **`TaskOrchestrator` 放置** —— 一个**自足的 `src/tasks/` 包**,带自己的 orchestrator
   (非 `src/core` 兄弟)。让 sub-session 概念(kernel 今天没有)内聚且可独立测试。
2. **沙箱下 resolver 回调传输** —— 一个**专用 host `/rpc` 风格端点**,用 DataService JWT
   模型,**不**占 toolkit MCP 通道。解析是数据形状,不是工具形状(见 §5 callout)。
3. **task 表持久化** —— **kernel 侧双写**(本地 kernel DB + DataService durable),与
   `sessions`/`messages`/`events` 在 `WriteThroughStore` 下 1:1 对齐。durable 为 wire
   权威;local↔durable seq 分叉是预期(§4.3、§10.6)。

---

## 13. P3 详解 —— `MemberResolverPort` 契约

**P3** resolver seam 的完整契约(交付 **D4**)。§5 是概览;这里是你据以开工的东西。它逐字
复用 DataService 的 `POST /rpc/{op}` + 验证-token-owner 范式
([data-service-architecture.md](data-service-architecture.md))。

### 13.1 核心洞察

`build_member_session`(`valuz_agent/adapters/agent_resolver.py:853`)**本就返回一个
`CreateSessionRequest`**——kernel 自己的 wire schema。它是今天 task 代码触达以下一切的唯一
漏斗:

- **agent 库**(`_member_agent_config` → `AgentDatastore.get_agent`),
- **project 成员**(`ProjectMemberDatastore.get`、`build_member_roster`),
- **project 上下文**(`ProjectDatastore` → name + instructions),
- **provider / model channel**(`_resolve_agent_provider` → `ProviderDatastore`),
- **skills**(`resolve_skill_slugs_to_paths`、`always_on_skill_paths`),
- **host MCP**(`always_on_http_mcp_servers`),以及 **system-prompt 组装**。

所以 P3 **不是**"写一个新 resolver"。它是:**把这个函数搬到一个 Protocol 后面、剥掉传入的
datastore 参数(host impl 自开 unit of work)、并通过两个传输暴露它。** 输出类型——一个
`CreateSessionRequest`——不变。这就是 P3 是*解耦*重构、而非行为重构的原因。

### 13.2 kernel 必须停止知道什么

今天调用方(`dispatcher.py`、`lifecycle.py`)把 host 对象穿进 resolver:`members:
ProjectMemberDatastore`、`providers: ProviderDatastore`,加上预取的 `project_name` /
`project_instructions_md` / `worktree_notice` / `run_dir`。P3 之后 kernel **只传纯数据**;
host impl 自己取其余一切。

| 曾由调用方传入 | P3 之后 |
|---|---|
| `members`(`ProjectMemberDatastore`) | host impl 自开 UoW |
| `providers`(`ProviderDatastore`) | host impl 自开 UoW |
| `project_name`、`project_instructions_md` | host impl 读 `ProjectDatastore` |
| `worktree_notice`、`run_dir` | host impl 从 `isolation` 解析 cwd/worktree(`fs_registry` + `worktree_service`) |
| `user_id` | 进程内:显式参数;**沙箱:从验证过的 token 派生**(anti-spoof,§13.6) |

三个 sibling-datastore import(`ProjectMemberDatastore`、`ProjectDatastore`、
`ProviderDatastore`)与 `agent_resolver` import **从所有 task 代码删除**——清掉
`backend/CLAUDE.md` 标注的模块边界违规。

### 13.3 端口面(kernel 侧 Protocol)

刻意收窄:**一个 resolve 调用**返回 spawn 所需的一切,加两个读辅助。owner 按传输穿线
(§13.6)。

```python
# kernel/src/tasks/ports/resolver.py   (Protocol —— kernel 拥有)
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence
from app.schemas import CreateSessionRequest          # kernel wire schema = ResolvedSession

ResolvedSession = CreateSessionRequest                 # 显式别名以表意图

@dataclass(frozen=True)
class MemberResolveSpec:
    project_id: str
    agent_slug: str
    is_lead: bool
    task_id: str
    brief: str                                         # goal/md (lead) 或 scoped goal+refs (member)
    isolation: Literal["shared", "worktree"] = "shared"  # host 解析具体 cwd
    dispatch_mode: Literal["sync", "async"] = "sync"
    goal_mode: bool = False
    plan_pre_committed: bool = False
    model_override: str | None = None
    lead_session_id: str | None = None
    user_id: str | None = None                         # 仅进程内;沙箱 ⇒ token owner

@dataclass(frozen=True)
class ResolveResult:
    session: ResolvedSession | None                    # None ⇒ 孤儿 slug(无成员/库 agent)
    credential_gap: str | None                         # 无可用 model provider 时的人类可读原因
    # 不变量:session is None  XOR  调用方可 spawn 它(受 credential_gap 约束)

class MemberResolverPort(Protocol):
    async def resolve_session(self, spec: MemberResolveSpec) -> ResolveResult: ...

    async def resolve_display_names(
        self, project_id: str, slugs: Sequence[str], *, user_id: str | None = None
    ) -> dict[str, str]: ...                            # 事件 agent_name 盖章 + 查询

    async def resolve_project_cwd(
        self, project_id: str, *, task_id: str, isolation: str = "shared",
        user_id: str | None = None,
    ) -> str: ...                                       # kernel 在 dispatch 外需要 cwd 时
```

**为什么 `resolve_session` 折入凭据检查。** 今天 dispatch 是**两次** host 调用:
`build_member_session(...)` 然后 `_credential_gap(session, ...)`(它重读 `ProviderDatastore`
以区分 OAuth 订阅的 `model_provider=None` 与真正的 gap)。沙箱一跳下这是每次 dispatch 两个
往返。把检查折入并返回 `ResolveResult.credential_gap` 让 dispatch **一个往返**。kernel 决
定:`session is None` → 孤儿 → `subtask_failed`/`kickoff_failed`;`credential_gap` 有值 →
同上,带原因;否则 → spawn。

### 13.4 文件落点

| 组件 | 路径 | 侧 | 备注 |
|---|---|---|---|
| Port Protocol + dataclasses | `kernel/src/tasks/ports/resolver.py` | kernel | `MemberResolverPort`、`MemberResolveSpec`、`ResolveResult` |
| HTTP 客户端传输 | `kernel/src/tasks/adapters/http_resolver.py` | kernel | `HttpMemberResolver` —— 对 host 讲 HTTP+JWT;**不 import host** |
| 传输工厂 | `kernel/src/tasks/adapters/resolver_factory.py` | kernel | env 选 `http` 否则用注入的进程内对象(镜像 `_build_durable_store`) |
| Host impl | `valuz_agent/adapters/member_resolver.py` | host | `HostMemberResolver(MemberResolverPort)` —— 包 `build_member_session` + 自开 UoW |
| Host `/rpc` ASGI app | `valuz_agent/adapters/member_resolver_service.py` | host | 挂在 `/_internal/resolver`(+ 旧 `/internal/resolver`),形状同 `data_service.py` |
| 沙箱 env 注入器 | `valuz_agent/boot/resolver_inject.py` | host | `resolver_env(owner_user_id, host_callback_url)` —— 镜像 `data_service_inject.py` |

拆法镜像 store seam:**kernel** 拥有 Protocol + HTTP 客户端;**host** 拥有具体 impl +
端点。进程内对象在 boot 时 host→kernel 注入(允许方向);HTTP 客户端不 import 任何 host。

### 13.5 两个传输

**进程内(默认,`make dev`,OSS)。** boot 时 host 构造 `HostMemberResolver()` 并注入 kernel
的 `TaskOrchestrator`,与注入 store 同法:

```python
# host boot(valuz_agent/boot/kernel.py,与 store 接线并列)
from valuz_agent.adapters.member_resolver import HostMemberResolver
task_orchestrator.bind_resolver(HostMemberResolver())     # 直接对象,无网络
```

`HostMemberResolver.resolve_session` 开 `async_unit_of_work()`,建 datastore,调重定位后的
`build_member_session`——与今天行为一致,只是搬到接口后面。

**沙箱(HTTP + JWT,SaaS)。** kernel 只持 URL + token;`resolver_factory` 读 env 构造
`HttpMemberResolver`:

```python
# kernel/src/tasks/adapters/resolver_factory.py
def build_resolver(injected: MemberResolverPort | None) -> MemberResolverPort:
    if os.environ.get("VALUZ_RESOLVER_API_KIND") == "http":
        return HttpMemberResolver(
            base_url=os.environ["VALUZ_RESOLVER_API_URL"],
            token=os.environ["VALUZ_RESOLVER_API_TOKEN"],
        )
    assert injected is not None, "in-process resolver must be injected at boot"
    return injected
```

host 为沙箱化 kernel 注入 env(镜像 `data_service_env`):

```python
# valuz_agent/boot/resolver_inject.py
def resolver_env(*, owner_user_id: str, host_callback_url: str) -> dict[str, str]:
    if not host_callback_url:
        return {}                                   # 无沙箱 ⇒ 进程内
    secret = get_or_create_ds_secret(owner_user_id) # 复用 DataService per-owner secret
    return {
        "VALUZ_RESOLVER_API_KIND": "http",
        "VALUZ_RESOLVER_API_URL": host_callback_url.rstrip("/") + "/_internal/resolver",
        "VALUZ_RESOLVER_API_TOKEN": mint_data_service_token(secret, user_id=owner_user_id),
    }
```

**复用 DataService 的 per-owner HS256 secret + verifier**——resolver 与 DataService 共享每
owner 一条 host↔沙箱信任边界。无新 secret、无新 verifier;只加一个 URL。这是既有回调三角的
第三条腿:**存储**(`/_internal/data`)、**工具**(`/_internal/mcp/toolkit`),现在加**解析**
(`/_internal/resolver`)。

### 13.6 `/rpc` wire 契约(host 端点)

逐字节 DataService 形状:`POST /rpc/{op}`,owner 从**验证过的 bearer token**(绝不从
body),`{"data": ...}` 信封,token 缺失/无效 → 401。

```python
# valuz_agent/adapters/member_resolver_service.py  (host)
@router.post("/rpc/resolve_session")
async def resolve_session(body: JsonBody, owner_id: OwnerDep, resolver: ResolverDep):
    spec = MemberResolveSpec(**{**body["spec"], "user_id": owner_id})   # owner 从 token 强制
    result = await resolver.resolve_session(spec)
    return {"data": {
        "session": result.session.model_dump() if result.session else None,
        "credential_gap": result.credential_gap,
    }}

@router.post("/rpc/resolve_display_names")
async def resolve_display_names(body: JsonBody, owner_id: OwnerDep, resolver: ResolverDep):
    return {"data": await resolver.resolve_display_names(
        body["project_id"], body["slugs"], user_id=owner_id)}

@router.post("/rpc/resolve_project_cwd")
async def resolve_project_cwd(body: JsonBody, owner_id: OwnerDep, resolver: ResolverDep):
    return {"data": await resolver.resolve_project_cwd(
        body["project_id"], task_id=body["task_id"],
        isolation=body.get("isolation", "shared"), user_id=owner_id)}
```

| op | 请求 body | `data` 响应 |
|---|---|---|
| `resolve_session` | `{spec: MemberResolveSpec}`(body `user_id` 忽略) | `{session: CreateSessionRequest \| null, credential_gap: str \| null}` |
| `resolve_display_names` | `{project_id, slugs: [str]}` | `{[slug]: name}` |
| `resolve_project_cwd` | `{project_id, task_id, isolation?}` | `str`(绝对 cwd) |

**Anti-spoof,与 `save_session` 一致:** 端点用 token owner 覆盖 `spec.user_id`。PG 上同样用
`install_rls_guc` 盖 `app.current_user_id`,resolver 跑的任何读都在 DB 层 owner-scoped。
`HttpMemberResolver` 重水合 `CreateSessionRequest.model_validate(data["session"])`。

### 13.7 同步不变量时序规则(承重)

`LiveMemberRegistry` 要求 **`create_task(spawn)` 与 `add_member` 之间无 `await`**(否则并发
`finish_task` 丢掉刚 spawn 的 member —— keystone 不变量,§10.1)。`resolve_session` 是异步、
沙箱下是网络跳。因此**所有解析在同步 spawn 块之前完成**:

```python
# kernel/src/tasks/runtime/dispatcher.py  (P3 后)
result = await self._resolver.resolve_session(spec)        # ← spawn 前唯一的 await
if result.session is None:
    await self._emit_subtask_failed(task_id, key, "orphaned agent"); return
if result.credential_gap:
    await self._emit_subtask_failed(task_id, key, result.credential_gap); return

member = result.session
# ── 同步块:直到 create_task 返回都无 await ─────────────
self._mailbox.register(lead_session_id)
self._registry.add_member(task_id, member.id)
self._mailbox.register(member.id)
asyncio.create_task(self._actor.run_actor_loop(member))    # spawn
# ──────────────────────────────────────────────────────
await self._kernel_create_session(member)                  # spawn 注册后才持久化
```

这正是为什么整块 actor 机器在 P2 作为一个单元搬迁、dispatch 不可横跨 host/kernel seam:异步
解析往返被推到原子 spawn *之前*,即便走 HTTP 也保住不变量。

### 13.8 resolver **不**拥有什么(P5 交接)

今天 `build_member_session` 注入 `always_on_http_mcp_servers(..., toolkit="lead"|"base")`
——既有 **host** MCP(docs / schedules / connectors)**又有** task **toolkit**(dispatch /
plan / review …)。D5 下 task toolkit 变成 **kernel serve**(P5)。干净的拆分:

- **resolver(host)只注入 host 所有的 MCP** —— docs / schedules / connectors + agent 自己
  的 `mcp_servers`(它们需要 kernel 没有的 host 凭据/URL)。
- **kernel 自己注入 task toolkit** —— 建 session 时(P5)从其原生工具注册表注入,无 host
  URL。

**P3/P5 时序:** 为让 P3 在 P5 落地*之前*保持行为一致,host resolver 继续注入完整集(含
toolkit 的 host URL)。P5 把 toolkit 翻成 kernel 原生时,只删 resolver 的
`always_on_http_mcp_servers` 调用里的 `toolkit=` 那条 arm —— 唯一易手的一行。

### 13.9 错误分类与失败语义

resolver 给 dispatch 加了外部依赖;失败必须显式,绝不 wedge lead loop。

| 条件 | `resolve_session` 结果 / raise | kernel 动作 |
|---|---|---|
| 孤儿 slug(无成员 / 无库 agent) | `ResolveResult(session=None)` | 发 `subtask_failed`/`kickoff_failed("orphaned agent")`,不解锁 |
| 无可用 model provider(真 gap) | `ResolveResult(credential_gap="…")` | 发带人类原因的失败 |
| OAuth 订阅(`model_provider=None` 但有效) | `credential_gap=None`(已解析) | 正常 spawn |
| host 不可达 / timeout(沙箱) | raise `ResolverUnavailableError` | 干净失败*这次* dispatch(可重试),保 lead loop 活 |
| 坏 token / owner 不符 | HTTP 401 → `ResolverAuthError` | boot/config 错误,大声暴露 |

`HttpMemberResolver` 带有界 timeout + 类型化错误,镜像 `kernel_client` 的 `Kernel*Error` 家族
(`ResolverUnavailableError`、`ResolverAuthError`、`ResolverBadRequestError`)。一次 dispatch
失败是**节点级**失败(plan 节点 → `failed`),非任务级崩溃。

### 13.10 P3 内部迁移步骤(有序)

1. **抽取** `build_member_session` 及其辅助(`_resolve_agent_provider`、
   `_member_agent_config`、`build_member_roster`、`_credential_gap`)进 `HostMemberResolver`,
   改签名为自开 UoW、丢掉 `members` / `providers` / 预取上下文参数。
2. **定义** `kernel/src/tasks/ports/resolver.py` 里的 Protocol + dataclasses。
3. **进程内接线**:host 在 boot 把 `HostMemberResolver` 注入 `TaskOrchestrator`;
   `resolve_session` 返回折叠后的 `ResolveResult`。
4. **删除** 所有 task 代码里的 `ProjectMemberDatastore` / `ProjectDatastore` /
   `ProviderDatastore` / `agent_resolver` import;dispatcher/lifecycle 现在持一个
   `MemberResolverPort`,用 `MemberResolveSpec` 调它。验证 `scripts/check_module_boundaries.py`
   通过(违规消失)。
5. **HTTP 传输**:`HttpMemberResolver` + 挂在 `/_internal/resolver` 的
   `member_resolver_service.py`;为沙箱化 kernel 注入 `resolver_env`。
6. **契约测试**(§13.11)。

步骤 1–4 在进程内路径落地解耦(绿 `make dev`);5–6 点亮沙箱路径。每步可独立评审。

### 13.11 契约测试

像 `test_data_service_contract.py` 钉住 store seam 那样钉住 route ↔ client ↔ Protocol:

- **形状对齐**:每个 `MemberResolverPort` 方法恰好一个 `/rpc/{op}` 路由;请求/响应 body 经
  `HttpMemberResolver` 与 host 端点往返无字段漂移。
- **传输等价**:*同一个* `MemberResolveSpec` 经进程内 impl 与 HTTP 传输产出**一致的**
  `CreateSessionRequest`(golden 对比序列化 session —— id 生成在测试种子下确定,或从对比排除)。
- **Anti-spoof**:body `user_id` 与 token owner 不同则被忽略;owner 永远从 token。缺失/无效
  token → 401。
- **不变量守卫**:一个单测断言 dispatcher 在 `registry.add_member` 与 `create_task` 之间无
  `await`(静态检查或一个会 interleave 的 monkeypatched resolver)。

### 13.12 开放边(P3 构建期决定,不阻塞契约)

1. **`resolve_project_cwd` 范围。** 仅在 kernel 于 dispatch 外需要裸 cwd 时(如写 task
   `.md`)。若 task-file 写入在 P6 仍是 host 关切,这个方法在 P3 可能根本不需要——出现调用方
   再加。
2. **roster 新鲜度。** lead 的 member roster 在 `resolve_session` 时烘进其 instructions;任务
   中途成员变动在 lead 下一个 session 前是陈旧的。与今天 snapshot-at-build 行为一致;记下,
   P3 不修。
3. **resolver 结果缓存。** `rework` 重派的 member 重新解析(又一个往返)。现在可接受;
   per-`(task,slug)` 短 TTL 缓存是后续优化,非契约的一部分。

---

## 14. P2 详解 —— actor 机器入 kernel

**P2** 的完整设计,交付 **D3**(actor 的*整个*生命周期在 kernel 侧)。这是核心结构性动作;
P3(resolver)和 P4(表)随后*硬化* P2 引入的 seam,**不再搬第二次代码**。

### 14.1 沙箱单元,与过渡接线原则

**沙箱单元是 task(锁定)。** 一个 task 的 lead session 和它所有的 member(subtask)session
共驻**一个** kernel / 沙箱。member 是*该 task 沙箱内的 sub-run*,绝非自己独立的沙箱。
"session 粒度沙箱起停"适用于**顶层** session —— 独立 chat、project chat,或**一个 task(其
lead 连同其 members)** —— 每个作为一个单元起停。

这忠于今天的模型(member 本就是同进程 `asyncio` 兄弟、共享 project cwd),也正是它把协调基座
保持在**内存**:`MailboxRegistry`(`asyncio.Queue`)和 `LiveMemberRegistry`(进程内 dict)
能工作,是因为 lead 与 member 共享一个事件循环。若 member 各自沙箱化,该基座就得变成跨进程
总线——远更大的重写。**它不会。** 它还把实时 interrupt/stop 路径塌缩成单次 kernel 内扇出
(无跨沙箱协调——§17.3)。per-session/per-task 沙箱*起停编排*是后续的商业关切(§18);本重构
只保证基座依赖的 task=sandbox **语义**。

**过渡接线原则。** P2 的交付是**task actor 机器物理上跑在 kernel、在 kernel 事件循环上、在
干净的 kernel 边界后面**。但 P2 落在 resolver(P3)与表搬迁(P4)*之前*。对这个排序的解法是
**过渡接线原则**:

> P2 把每一条 host→kernel 依赖引入为一个**注入端口**。进程内(`make dev`),host 绑定一个包住
> *今天*代码的具体 impl。P3 和 P4 随后在那个端口背后换 impl(HTTP 传输 / DataService)——
> kernel task 代码不再改。

所以从 P2 起 kernel task 包**只 import kernel Protocol**;host 在 boot 注入 impl。P2 打桩的三
条 seam:

| Seam | P2 impl(进程内) | 硬化于 |
|---|---|---|
| `MemberResolverPort`(agent/project 解析) | host `HostMemberResolver` 包 `build_member_session` | **P3**(契约 + HTTP) |
| `TaskStorePort`(task 持久化) | host 包住今天的 `TaskDatastore`(`valuz_task*` 在 `valuz.db`) | **P4**(重命名 + DataService 双写) |
| kernel session ops(create/run/events) | **直接** `SessionOrchestrator` + store 调用(不再走 `kernel_client` facade) | 已终态 |

**P2 验收是绿 `make dev`(进程内)。** 沙箱路径要到 P3/P4/P5 之后才完全点亮,因为 task 工具
到 P5 前仍是 host serve(§14.5)。别用沙箱行为评判 P2——用这些评判:actor 跑在 kernel loop、
kernel 边界检查通过、完整 task 测试套件进程内绿。

### 14.2 `src/tasks/` 包(什么搬进来)

一个自足的 kernel 包(决策 §12.1),`src/core` 的兄弟:

```
kernel/src/tasks/
├── domain/            # 来自 P1:plan.py、task_state.py、provenance.py(纯)
├── runtime/
│   ├── actor_runner.py           ← 搬(run_actor_loop 引擎)
│   ├── mailbox.py                ← 搬(MailboxRegistry 单例)
│   ├── live_member_registry.py   ← 搬(keystone;同步不变量)
│   └── dispatcher.py             ← 搬(spawn 路径;解析现经端口)
├── services/
│   ├── coordination.py           ← 搬(await/heartbeat/probe/shutdown)
│   ├── planning.py               ← 搬(plan/review;CAS)
│   ├── lifecycle.py              ← 搬(kickoff/draft/commit/abandon/finish)
│   ├── recovery.py               ← 搬(boot 扫描;现为 kernel boot)
│   ├── messaging.py              ← 搬(send/inject/goal-revise)
│   ├── queries.py                ← 搬(读侧;host 读路径在 P6)
│   └── health_monitor.py         ← 搬(watchdog;kernel lifespan)
├── ports/
│   ├── resolver.py               ← P3 Protocol(P2 打桩 + 注入)
│   ├── task_store.py             ← TaskStorePort Protocol(P2 host 包)
│   └── side_effects.py           ← decision-inbox / 通知 / 记忆(可选;§8)
└── orchestrator.py               ← 新:TaskOrchestrator 组合根(§14.3)
```

**domain** 层在 P1 已搬入。P2 搬 **runtime + services**,定义 **ports**,加 **orchestrator**。
不搬的:host HTTP 路由(P6)、工具 serve(P5),以及 host 副作用服务本身(它们留 host;kernel
通过 `side_effects.py` 端口调,或按 §8 事件优先反应)。

### 14.3 `TaskOrchestrator` 骨架

镜像今天 host 的 `TaskOrchestrator` 组合根(`orchestrator.py:121`)——一个
`LiveMemberRegistry` + 一个 `MailboxRegistry` 被五个服务共享——但在 kernel 侧构造、由注入端口
驱动而非 host import。

```python
# kernel/src/tasks/orchestrator.py
class TaskOrchestrator:
    def __init__(
        self,
        *,
        sessions: SessionOrchestrator,          # kernel 自己的 —— 直接,同 loop
        task_store: TaskStorePort,              # 注入(P2 host 包 → P4 DataService)
        resolver: MemberResolverPort,           # 注入(P2 host 包 → P3 HTTP)
        side_effects: SideEffectPorts | None = None,   # 可选(§8);None ⇒ no-op
    ) -> None:
        # keystone 单例 —— 一个实例,被每个服务共享(同步不变量)
        self._registry = LiveMemberRegistry()
        self._mailbox = MailboxRegistry()
        self._actor = ActorRunner(sessions=sessions, mailbox=self._mailbox)

        # 五个剥离出的服务(保留 ADR-023 形状),全部共享那两个单例
        self._planning = PlanningService(task_store)
        self._dispatcher = DispatcherService(
            registry=self._registry, mailbox=self._mailbox, actor=self._actor,
            resolver=resolver, sessions=sessions, task_store=task_store,
        )
        self._coordination = CoordinationService(
            registry=self._registry, mailbox=self._mailbox, sessions=sessions,
            task_store=task_store, side_effects=side_effects,
        )
        self._lifecycle = LifecycleService(
            registry=self._registry, mailbox=self._mailbox, actor=self._actor,
            resolver=resolver, sessions=sessions, task_store=task_store,
        )
        self._recovery = RecoveryService(
            registry=self._registry, mailbox=self._mailbox, actor=self._actor,
            resolver=resolver, sessions=sessions, task_store=task_store,
        )

    # 薄委托(面不变 —— 调用方/工具在 self 上解析)
    async def kickoff(self, spec): ...
    async def dispatch(self, ...): return await self._dispatcher.dispatch_async(...)
    async def await_member_results(self, ...): return await self._coordination.await_member_results(...)
    async def review_subtask(self, ...): return await self._planning.review_subtask(...)
    async def finish_task(self, ...): return await self._lifecycle.finish_task(...)
    async def recover_active_tasks(self): return await self._recovery.recover_active_tasks()
    # … stop/resume/inject/plan/get/list …
```

两条从 host 版逐字带过来的不变量:

- **一个 `LiveMemberRegistry`、一个 `MailboxRegistry`,被每个服务共享** —— 在 `__init__` 里
  构造一次,按引用传。第二个实例会分裂 spawn/drain 状态、重开 finish-丢-member 竞态。
- **`finish_task` 只在一个地方。** host 有一份活拷贝和一份死拷贝(§10.2);对着调用图核实哪份
  是死的,**只把活的**搬进 `LifecycleService`。

### 14.4 把 `kernel_client` 重接为直接 orchestrator/store 调用

今天 host task 代码通过 `kernel_client` facade 触达 kernel(`create_session`、`run_turn`、
`emit_live_event`、`get_session`、`get_events`、`set_mode`、`interrupt`、`cleanup_runtime`)。
一旦 task 代码住在 kernel *里*,那些 facade 跳塌缩成**直接**调用——`InProcessKernelClient` 本
就*是*那条路,所以这是删一层、非加一层:

| 曾经(host,经 facade) | 现在(kernel 内,直接) |
|---|---|
| `kernel_client.create_session(user_id, s)` | `sessions.create_session(s)`(SessionOrchestrator/store) |
| `kernel_client.run_turn(...)` | `sessions.run_turn(...)` |
| `kernel_client.emit_live_event(...)` | `sessions.emit_live_event(...)` |
| `kernel_client.get_session / get_events` | 经 `sessions` / `TaskStorePort` 读 store |
| `kernel_client.interrupt / set_mode` | `sessions.interrupt / set_session_mode` |

`ActorRunner` 持 `SessionOrchestrator` 引用、直接调 `run_turn`;`session_error` 的
`emit_live_event` 变成直接 orchestrator 调用。再无 task 代码 import
`valuz_agent.adapters.kernel_client`。

### 14.5 共享事件循环、runtime 缓存,与临时工具桥

- **一个事件循环。** `TaskOrchestrator` 跑在与 `SessionOrchestrator` *同一个* asyncio loop
  上。member/lead actor 是那个 loop 上的 `asyncio.create_task(...)`。在 per-**task** 沙箱
  (§14.1)里,那个 loop 是沙箱自己的——这正是 actor 膨胀如何从共享 host 进程分散出去
  (§1,D7)。
- **runtime 缓存复用。** member *就是*一个 kernel session;它的回合经
  `SessionOrchestrator._ensure_runtime` —— 既有暖 runtime 缓存(per-`session_id`,idle-TTL +
  LRU)。无独立 task runtime 池;在 task 沙箱里天然有界于该 task 的 members。
- **临时工具桥(至 P5)。** P2 里 task **工具仍是 host serve**(host toolkit MCP)。它们的
  handler 现在调 *kernel* `TaskOrchestrator` —— 进程内,boot 时绑的一个直接对象引用。此桥
  **仅进程内有效**;沙箱化 agent 的工具调用无法穿过一个 host handler 回到它自己的沙箱 kernel。
  这就是沙箱 task 工具需要 P5(kernel serve 工具)、以及 P2 验收仅进程内(§14.1)的原因。

### 14.6 保住同步不变量(keystone)

spawn 块搬进 `kernel/src/tasks/runtime/dispatcher.py`,形状不变。所有异步工作(P3 的解析、
session 持久化)被推到同步 `register → add_member → create_task` 块**之外**。规范形式在 §13.7;
P2 的规则很简单:**跨 `mailbox.register` / `registry.add_member` /
`asyncio.create_task` 的那块无 `await`。** 一个单测守护它(§13.11 "不变量守卫")。这个约束*就是*
整块 actor 机器在 P2 作为一个单元搬迁、而非拆过 seam 的原因。

### 14.7 recovery 与 health monitor 在 kernel boot(D3)

启动扫描与 watchdog 都从 host lifespan 移到 **kernel boot / kernel lifespan**:

- **Recovery。** `recover_active_tasks` 在 kernel 启动时跑(沙箱下:该沙箱启动时,恢复其 owner
  的活跃任务)。它经 `TaskStorePort` 读 `tasks` / `task_sessions` / `task_events` 并重新水合
  lead + member actor,经同一 dispatcher 路径 respawn(所以恢复时同步不变量也成立)。它必须在
  config-gate 应用**之后**跑(snapshot/resume 微 VM)并与 DeepAgents checkpoint 对账。
- **Watchdog。** `TaskHealthMonitor` 随 kernel lifespan 起停;其排空检查读 **kernel** 的
  `is_draining`,而非 host 的。

### 14.8 boot 接线

kernel 组合根(`app/dependencies.py`)在 store + `SessionOrchestrator` 旁构造
`TaskOrchestrator` 单例。具体端口按执行位置不同:

```python
# kernel/app/dependencies.py  (组合根)
def build_task_orchestrator(sessions, store, *, injected_resolver=None, side_effects=None):
    resolver = build_resolver(injected_resolver)          # §13.5:env http 或 注入对象
    task_store = build_task_store(store)                  # P2:host 包;P4:DataService 双写
    return TaskOrchestrator(
        sessions=sessions, task_store=task_store, resolver=resolver, side_effects=side_effects,
    )
```

- **进程内(host 内嵌 kernel)。** boot 时(`valuz_agent/boot/kernel.py`)host 把
  `HostMemberResolver` + host 副作用 impl 注入 `build_task_orchestrator`,并把得到的
  `TaskOrchestrator` 引用绑到 host toolkit MCP handler 能触达处(临时桥,§14.5)。
- **沙箱/独立 kernel。** 无注入 —— `build_resolver` 读 `VALUZ_RESOLVER_API_*`(P3),
  `build_task_store` 读 `KERNEL_STORE`/DataService(P4)。recovery + watchdog 从 kernel 自己的
  lifespan 启动。

boot 顺序:config gate → store/DataService 就绪 → `SessionOrchestrator` →
`TaskOrchestrator` → `recover_active_tasks()` → `health_monitor.start()`。

### 14.9 session 模块耦合 —— 回合增强作为数据(推,而非拉)

共享回合驱动 `run_session_to_idle` 今天触达五处 host `sessions` 模块内部
(`context_builder._build_additional_context`、`attachments._load_pending_attachments` /
`_mark_attachments_consumed`、`run_orchestrator._finalize_session`、`project_index.record`、
`SESSION_FINISHED`)。天真地做,每处变成**每回合跨 seam 回调**——一旦 actor 跑在沙箱里就是不可
接受的延迟。优雅的解法把它们分三类,热的那些塌缩成**传入的数据,而非拉取**:

**A. 回合增强(`attachments` + `additional_context`)—— 在 host 入口点推入。** kernel
`run_turn` **本就接受** `attachments` 和 `additional_context` 作为输入。它们是 host 所有的
覆盖层,且只在**host 发起的输入**启动一回合时才有意义 —— `kickoff`、用户 `inject`,或一条用户
消息。这些全在 host 侧发起,那里 `_load_pending_attachments` / `_build_additional_context` 本
就有 DB access。所以 host 在这些入口点算好它们、**推入** kernel task API(kickoff/inject 携带
`attachments` + `additional_context`)。自主 actor 回合 —— member 的 goal-loop 迭代,或 lead
响应 `member_done` —— 不带新用户输入;它们的上下文已在 `session.instructions` 里(resolver 在
dispatch 时建),所以以空增强驱动 `run_turn`。**结果:每回合零 host 回调。** 耦合变成"增强是
数据,host 侧在入口点算好、传下去";`_load_pending_attachments` / `_mark_attachments_consumed`
/ `_build_additional_context` 留 host 侧,由 host 路由 handler(kickoff/inject)在它们本就运行
处调用 —— member 是 no-op(无 staged 文件)。

**B. 回合终结(`_finalize_session`)—— 本就是 kernel op。** 它 append 一个 `session_error`
事件 + 盖 kernel 状态;是 `finalize_session` 的包装。kernel 内它变成 actor `finally` 块里一个
**直接** `SessionOrchestrator` 调用 —— 无 host 耦合残留。`run_session_to_idle` 自身变成一个
kernel 原语(它是通用回合驱动;chat 路径也调 kernel 的版本,传它自己 host 建的增强)。

**C. host 投影(`project_index`、`SESSION_FINISHED`)—— task 弃用它们;无新事件。** *已核实*
(§17.4):kernel **不**发 `session.created` 事件,且 `project_index` 是 **chat-scoped** ——
`touch_activity`/recents 走 chat 回合路径,任务活跃度来自任务表而非索引。chat 仍由 **host** 路由
创建,所以它们的 `record` / `touch_activity` 留 host 侧不变。**task lead/member session 干脆不再
进 `project_index`**;唯一承重的用途 —— `project_of(session_id)` 反查 —— 改从 durable session 的
`metadata.valuz.project_id`(每个 session 都带)解析。task session 的 `SESSION_FINISHED` 变成一个
**task 事件**(§17.5);chat 的留 host chat 路径。→ 无 kernel→host 回调,无新 kernel 事件。

优雅的核心:**kernel actor loop 每回合不从 host 拉取任何东西。** 增强随 host 发起的输入推入;
终结是原生的;投影事件驱动地跑在 host 本就消费的流上。

### 14.10 P2 内部迁移步骤(有序,每步进程内绿)

1. **定义端口**(`resolver.py` stub、`task_store.py`、`side_effects.py`)与
   `kernel/src/tasks/` 里的 `TaskOrchestrator` 壳 —— 尚无逻辑。
2. **搬 runtime 三件套**(`actor_runner`、`mailbox`、`live_member_registry`)进 `runtime/`,把
   `kernel_client.run_turn/emit_live_event` 重接为直接 `SessionOrchestrator` 调用(§14.4)。
3. **搬五个服务**(`dispatcher`、`coordination`、`planning`、`lifecycle`、`recovery`、
   `messaging`、`queries`)到共享单例后面;把 host-datastore/resolver import 换成注入端口。
4. **搬 `health_monitor`**;把 recovery + watchdog 接进 kernel boot(§14.7)。
5. **绑进程内 impl** 到 host boot;把 host toolkit MCP handler 指向 kernel `TaskOrchestrator`
   (临时桥)。
6. **搬 actor 测试套件**(`tests/modules/tasks/test_actor_v2.py`、`test_plan_orchestrator.py`
   …)与代码同搬;让它们对着 kernel `TaskOrchestrator` 跑。绿 `make dev` + 完整 task 套件是 P2
   门。

步骤 1–3 是大头;每个服务能独立搬迁并转绿,因为端口把它与尚未搬的邻居隔离。

### 14.11 开放边(P2 构建期决定)

1. **`SideEffectPorts` 形状。** §8 主张多数副作用(通知、记忆)应**事件优先**(host 对
   `task_events` 反应),而非同步外发端口。P2 可注入 no-op 副作用端口、把 host 反应接线推到 P6
   —— 只要*事件*仍然发出。判断 P2 里是否真有副作用需要同步回调(大概没有)。
2. **queries 放置。** `queries.py` 是读侧;§9 让读在 P6 走 host。P2 里它可作为 `TaskStorePort`
   之上一个薄 kernel 服务;host 读路径在 P6 替换它。P2 别在它上面过度投入。
3. **`kernel_client` 监督钩子。** `scan_orphan_pendings` / `scan_orphan_runs` /
   `cleanup_runtime` 是进程内独有、无远程对应的监督。确认 task recovery 路径是否需要其中任何一个,
   还是 kernel 自己 boot 时的 `scan_orphan_runs` 已覆盖 task session。

---

## 15. P4 详解 —— 表入 kernel + DataService 双写

**P4** 的完整设计,交付 **D1**(表 kernel 所有、去 `valuz_*` 前缀、保留 `user_id`)与 **D2**
(经 DataService 持久化、**kernel 侧双写**、host 终结的查询)。它逐字复用 kernel 自己的三表机器
—— `_owner_column`、`event_uid` 幂等、`WriteThroughStore`、`store_wire`,以及
`kernel_db_colocate` 数据搬迁先例。**P4 改的是 P2 `TaskStorePort` 背后的 impl;kernel task 代码
不再改。**

### 15.1 P4 交付什么,以及它填的 P2 坑

P2 引入 `TaskStorePort` 并注入一个包住今天 `TaskDatastore` 的 host wrapper(仍 `valuz_task*` 在
`valuz.db`)。P4 用真家伙替换那个 impl:

- **kernel 所有的 ORM 表** `tasks` / `task_events` / `task_sessions`(重命名,保留 `user_id`),
  在 kernel schema 里 —— 于是它们在 `kernel.db`(本地 buffer)和 durable store(host `valuz.db`,
  或 SaaS 下 PG)双双物化。
- **`WriteThroughTaskStore`** —— 与 `WriteThroughStore` 相同 `authority` 语义的双写(remote/沙箱
  下 durable = 真相源;local = buffer)。
- **DataService RPC ops** for task(§4.2),owner 从验证过的 token。
- **一次性数据搬迁**,从退役的 host `valuz_task*`(镜像 `kernel_db_colocate`)。

`TaskStorePort` *接口*与 P2 定义完全一致 —— 只换绑定(host 包 → `WriteThroughTaskStore`),镜像
`KERNEL_STORE` 如何在一个工厂后面换 session store。

### 15.2 三张 kernel task 表

在 `SessionModel`/`MessageModel`/`EventModel` 旁的新 ORM 模型,放
`kernel/src/adapters/sqlalchemy_store/task_models.py`(同一 `Base`),**严格**遵循既有约定:

- **`_owner_column()`** for `user_id` —— `String(64)`、`NOT NULL`、indexed、**无 default**,由
  converter 从调用方 owner 显式盖章。
- **`tasks`** —— `id`(PK)、`user_id`、`project_id`、`title`、`goal`、`status`、`plan`(JSON
  DAG)、`plan_version`(int CAS)、`trigger_*`、`metadata_`(`"metadata"` JSON)、
  `created_at`/`updated_at`(BIGINT epoch ms)。`status` 上 `CheckConstraint`(镜像
  `ck_sessions_status` 风格)。`project_id`、`status`、`trigger_task_id`/`trigger_automation_id`
  上索引。
- **`task_events`** —— `id`(Integer PK **autoincrement**,wire 游标)、`user_id`、`project_id`、
  `task_id`、`type`、`actor`、`session_id`、`payload`(JSON)、`timestamp`(BIGINT)、
  **`event_uid`**(`String(64)`,nullable)加 **`uq_task_events_owner_uid (user_id, event_uid)`
  unique** —— 逐字节 `EventModel` 幂等范式。`(task_id, id)`(SSE 游标)和 `(task_id, type)` 上
  索引。
- **`task_sessions`** —— `id`(PK)、`user_id`、`project_id`、`task_id`、`session_id`(→ kernel
  `sessions.id`,业务键,无 FK)、`kind`、`subtask_key`、`sequence`(0=lead)、`status`、`label`、
  `goal`、`dispatched_by`、`project_mode`、`run_dir`、`result_manifest`(JSON)、`ended_at`。
  unique `(task_id, session_id)`。

一切方言无关(SQLite / PG),instant 是 epoch-ms `BIGINT`,JSON 列经 `sqlalchemy.types.JSON`
—— 与 kernel 表一致。

### 15.3 seq 模型决策 —— 采纳 kernel 范式

今天 `valuz_task_event` 携带 **host 分配的 per-`(project, task)` 单调 `sequence`**,带
retry-on-collision(`datastore.py:282`),SSE 游标在它上面翻页。kernel `events` 表则用
**per-store autoincrement `id`** 作 wire 游标 + `event_uid` 作跨库身份,并刻意容忍分叉的
local/durable id(记忆:[[valuz-event-seq-two-stores]])。

**P4 采纳 kernel 范式**(干净双写所需):

- `task_events.id`(durable autoincrement)**是 wire 游标**。单个 task 事件流内 durable id
  严格递增,所以 SSE `?after_seq=` 契约照常工作 —— 只是在 durable 行 id 上翻页,而非 per-task
  计数器。
- `event_uid` 跨两库桥接身份;重试的双写 append 复用该 uid,unique 索引塌缩重复。
- **丢弃** host 分配的 per-`(project, task)` `sequence` + 其 retry-on-collision 循环。这移除一整
  类写竞争代码。
- `append_event` 返回**权威**方的 seq(remote/沙箱下是 durable);两库 id 按设计以常量偏移分叉
  —— 绝不对账(§10.6)。

**待验证边(§15.12):** 前端 Todo-panel SSE 纯把 `after_seq` 当单调游标用,所以切到 durable-id
是透明的。删列前确认无消费方把旧 per-task `sequence` 当稳定业务值(今天它并未作为业务值暴露)。

### 15.4 `TaskStorePort` 面

task 形状(键于 `task_id`/`project_id` 而非 `session_id`),但结构上与 `StorePort` 同样
owner-first、双写感知:

```python
# kernel/src/tasks/ports/task_store.py   (P2 定义,impl 在 P4 落地)
class TaskStorePort(Protocol):
    # tasks
    async def save_task(self, task: Task) -> None: ...                      # owner 从 task.user_id
    async def load_task(self, user_id: str, task_id: str) -> Task | None: ...
    async def list_tasks(self, user_id: str, *, project_id: str | None = None,
                         status: str | None = None, limit: int = 50, offset: int = 0) -> list[Task]: ...
    async def list_active_tasks(self, user_id: str | None) -> list[Task]: ...   # None ⇒ recovery 扫描
    async def update_task_status(self, user_id: str, task_id: str, status: str) -> None: ...  # 断言转移

    # task events(kernel 范式:autoincrement id 游标 + event_uid 幂等)
    async def append_task_event(self, user_id: str, task_id: str, event: TaskEvent,
                                *, request_id: str | None = None) -> int | None: ...
    async def get_task_events_after(self, user_id: str, task_id: str, *,
                                    after_seq: int = 0, limit: int = 200) -> list[StoredTaskEvent]: ...

    # task sessions(lead + member 索引)
    async def save_task_session(self, user_id: str, row: TaskSession) -> None: ...
    async def load_task_session(self, user_id: str, task_id: str, session_id: str) -> TaskSession | None: ...
    async def list_task_sessions(self, user_id: str, task_id: str) -> list[TaskSession]: ...
    async def update_task_session_by_session(self, user_id: str, session_id: str, **fields) -> None: ...
```

`list_active_tasks(None)` 是跨 owner 的 recovery 扫描 —— `list_sessions` 已为 kernel 孤儿扫描
记录的那个 `user_id=None` 逃生口。

### 15.5 `WriteThroughTaskStore` —— 双写

`WriteThroughStore` 的 task 形状兄弟,**authority 语义相同**(别重新推导):

- **`authority="durable"`**(remote / SaaS 沙箱):**durable DataService 是真相源** —— 读 + 事件
  id 游标来自 durable,durable append 是**fail-loud**(返回前必须落地;沙箱易逝)。本地
  `kernel.db` 副本是 best-effort buffer(`_buffer_local`,失败不致命)。
- **`authority="local"`**(`pg` tier,常驻):local 权威 + durable 经 `DurableOutbox` 重放队列
  best-effort。
- **每个 store 拥有自己的 `task_events` autoincrement**;返回权威方的;`event_uid` 桥接身份。
  **绝不**给另一个 store 传显式 id。

本地 `SQLAlchemyTaskStore` 与 durable 那个是*同一* impl 跑在不同 engine 上 —— 与
`SQLAlchemyStore` 完全一样。工厂 `build_task_store(store)`(§14.8)仅在 durable 与 local 真正
不同时构造 write-through 包装(co-located DSN 塌缩为单写)。

### 15.6 DataService RPC + `store_wire` 扩展

给 DataService(`kernel/app/data_service.py`,或同 app 挂载的 `task_rpc` 路由)加 task ops ——
同 `POST /rpc/{op}`、同 `OwnerDep`/`StoreDep`、同 `{"data": ...}` 信封、同 **anti-spoof**(owner
从 token 强制,body `user_id` 忽略;PG `install_rls_guc` 兜底):

```
save_task · load_task · list_tasks · list_active_tasks · update_task_status
append_task_event · get_task_events_after
save_task_session · load_task_session · list_task_sessions · update_task_session_by_session
```

在既有 session/message/event converter 旁加 `store_wire` converter(`task_to_row`/`row_to_task`、
`task_event_to_row`/…、`task_session_to_row`/…)—— wire 通货保持纯 dict 行,绝不 domain
dataclass。

### 15.7 数据迁移 —— `task_colocate` boot step

镜像 `boot/kernel_db_colocate.py`(**别**发明新范式):

- **新 boot step** `boot/task_colocate.py` —— 把 host `valuz_task` / `valuz_task_event` /
  `valuz_task_session`(在 `valuz.db`)复制到新 kernel `tasks`/`task_events`/`task_sessions`
  (durable)。**早**跑(schema bootstrap 之后、durable task store 被读之前)。
- **仅 sqlite、insert-only、幂等、count-gated** —— target 已有的 task 跳过;不更新/删除;首次
  boot 之后是快速 no-op。首次 seed 前把 `valuz.db` 备份一次(`.bak-pretaskcolocate`),若前次跑留
  了备份则保留首份。
- **owner 原样保留**(`user_id` 直接复制;durable store 反正在边界盖章)。
- **PG/remote durable** → 非本地 co-locate 情形(早返回);SaaS 路径经 DataService seed,与
  sessions 相同。

**可逆 + 保数据**(仓库规则):复制是 insert-only 且有备份,故恢复备份即可轻易回退。旧
`valuz_task*` 表**保留一个 release**(belt-and-suspenders);一个**后续 host Alembic 迁移**在
kernel 路径验证后 drop 它们 —— `downgrade()` 重建它们。**不要**在引入复制的同一 release 里 drop。

### 15.8 两条 schema 路径 —— Alembic(local)vs create_all(durable)

kernel schema 活在**两**处、用**两**种创建机制 —— 弄对,否则 durable 副本会静默缺表:

- **本地 `kernel.db`** —— 由 **kernel Alembic 链**(`alembic/kernel/`,`alembic_version`)拥有。
  加一个可逆 revision 建 `tasks`/`task_events`/`task_sessions`(带 `event_uid` unique 索引、status
  check 约束、游标索引);`downgrade()` drop 它们。仅数字 revision id(`"00NN"`);SQLite `ALTER`
  受限 —— 后续改动用 batch ops。
- **durable 副本(host `valuz.db`,或 SaaS 下 PG)** —— **非 Alembic。** durable schema 经
  `ensure_host_data_service_schema` 从 kernel `Base` `create_all`(与它已建 durable
  `sessions`/`messages`/`events` 同一条路)。把三个模型加进 kernel `Base` 就让它们在那里自动物化
  —— 无独立 durable 迁移。
- **host 链**(`alembic/host/`,`alembic_version_host`)—— 只有那个**后续**可逆 revision 退役
  `valuz_task*`(复制验证之后)。

### 15.9 查询接线(契约不变,durable 读源)

按 D2,host 保留公共读/流路径(§4.3)。P4 之后:

- `GET /v1/tasks…` 和 task SSE 读 **durable** task 表(DataService 写入目标)—— OSS 下是
  `valuz.db`、SaaS 下是中央 PG,而对易逝沙箱 host 直接读 durable(kernel `events` 已用的
  `DataServiceReadClient` 范式,让死沙箱仍供史)。
- 500ms DB 轮询 SSE(`_iter_task_events_sse`)在 `task_events.id` 上翻页(§15.3)—— 同
  `?after_seq=` wire,durable 游标。

### 15.10 P4 内部迁移步骤(有序)

1. **ORM + Alembic**:加三个 kernel task 模型 + 可逆 kernel revision(仅建;尚无数据)。绿 schema
   bootstrap。
2. **`store_wire` converter** + `SQLAlchemyTaskStore` impl(local + durable,一个类)。
3. **`WriteThroughTaskStore`** + `build_task_store` 工厂;绑到 `TaskStorePort` 后面(替换 P2 的
   host 包)。进程内(`local` authority 路径)先绿。
4. **DataService RPC ops** + `store_wire` 接线;扩展契约测试。
5. **`task_colocate` boot step**;早跑;验计数 + 备份。
6. **退役路径**:保留 `valuz_task*`;把 host 链 drop 排到后续 release。

步骤 1–3 在常驻路径落地双写;4 点亮沙箱(`remote` authority);5 保住既有安装的历史。

### 15.11 契约测试 + 回归

- **扩展 `test_data_service_contract.py`**:每个 `TaskStorePort` 方法一个 `/rpc/{op}` 路由;task
  行经 wire 往返无字段漂移。
- **幂等**:同 `event_uid` 的重试 `append_task_event` 返回原 id、不插第二行
  (`uq_task_events_owner_uid` 守卫)—— 与 kernel `events` 表同款测试。
- **authority 对齐**:`authority="durable"` 下 durable append 失败 fail-loud(raise)而
  local-buffer 失败被吞;`authority="local"` 下反过来(durable 失败 → outbox,local fail-loud)。
- **迁移幂等**:`task_colocate` 跑两次只复制一次;target 已有的 task 跳过;备份取一次。
- **游标回归**:从 per-task `sequence` 切到 durable id 后,task SSE `?after_seq=` 仍投递无缝、
  单调的流。

### 15.12 开放边(P4 构建期决定)

1. **per-task `sequence` 丢弃。** §15.3 建议用 durable autoincrement id 替换它。删列前确认无
   前端/消费方把旧 `sequence` 当稳定业务值。
2. **task-event `types` 过滤。** kernel `get_events` 支持 `types=` 过滤以 O(matches) 读;判断
   `get_task_events_after` 是否也需要它以支持 panel 只读 `task_plan_update`,还是 panel 已在客户端
   过滤。
3. **drop 时机。** 确认引入复制到 drop `valuz_task*` 之间的 release 间隔 —— 一个 release 是安全
   默认;SaaS 分批推更长。

---

## 16. P5 详解 —— task 工具在 kernel 侧 serve

**P5** 的完整设计,交付 **D5**:task 内置 MCP 工具在 **kernel 侧** serve,于是沙箱化任务的工具
调用在沙箱内解析(本地持久化 + 写回 host 的 DataService),无 host 往返。

### 16.1 今天 vs 之后

今天 task 工具(`dispatch`、`await_members`、`send`、`list_members`、`finish_task`、
`update_deliverable`、`stop_subtask`、`plan_task`、`get_plan`、`modify_plan`、`review_subtask`
+ base 集 `create_task`/`draft_task`/`commit_task`/`abandon_task`/`inject_into_task`/
`resume_task`/`list_tasks`/`get_task`)由 **host** toolkit MCP server
(`integrations/toolkit_mcp_server.py`,挂在 `/_internal/mcp/toolkit/{base,lead}`)serve,在
`session.mcp_servers` 里以 `harness` 条目引用,owner 经 `_call_tool` 边界的
`HostExecContext(ExecContext)` 注入。

P5 之后 **task** 工具变成 **kernel 原生** —— 注册进 kernel 工具注册表
(`src/core/tool_registry.py`),由 kernel 自己的 MCP toolkit 在一个 **kernel/沙箱本地地址** serve。
handler 直接调进程内 `TaskOrchestrator`,移除临时 host 桥(§14.5)。

### 16.2 owner 从 session,gate 从 kernel 状态

- **owner。** handler 从 **`session.user_id`** 读 owner(每个 task 动作跑在一个带 owner 的
  session 上下文里),**不**从 `ExecContext`。这保住 `ExecContext` owner-agnostic
  ([[builtin-mcp-user-id-context-break]]),同时给 handler 需要的 owner —— 替换 host
  `HostExecContext` 注入。
- **lead-gate。** `_check_lead_gate` / `_check_plan_writer_gate` 键于 task-session **角色**
  (lead vs member),现在是 kernel 所有的状态(`task_sessions.kind`)—— 比今天仅在 handler 的
  gate 更干净、更权威。

### 16.3 `mcp_servers` 接线交接(来自 §13.8)

这是 P3 与 P5 之间唯一易手的一行:

- **P5 之前:** resolver 注入 `always_on_http_mcp_servers(..., toolkit="lead"|"base")` —— task
  toolkit 作为 `session.mcp_servers` 里的一个 **host URL**。
- **P5 之后:** resolver 只注入 **host 所有的 MCP**(docs / schedules / connectors + agent 自己的
  server);**kernel** 在建 session 时把**原生 task toolkit** append 进 `session.mcp_servers`,指向
  自己进程内/沙箱本地的 toolkit —— task 工具无 host URL。

**范围围栏。** 只有 **task** 工具搬。今天共享 toolkit server 的其他 harness 工具(`memory`、
`submit_skill`、非 task 编排)与 host 内置 MCP(docs / schedules / connectors)**留 host serve**
—— 它们的 owner 上下文与回调接线明确不在本重构范围。P5 沿 task 边界**拆分** toolkit;它不清空 host
server。

### 16.4 沙箱地址(D5)

kernel 沙箱化时,其 MCP toolkit 在一个**沙箱本地地址**可达(agent runtime 在沙箱内连它,如一个
loopback 端口或 stdio)。因此一次 task 工具调用永不离开沙箱:handler 改本地 task 状态,写回 host
durable store 走 DataService(§15)—— 与 kernel 自身表的行为一致。这就是 D5 要的"本地持久化 +
DataService API 写回"对称。

### 16.5 P5 内部迁移步骤(有序)

1. **注册** task 工具进 kernel 工具注册表(declarations → kernel `ToolDef`);handler 直接调
   `TaskOrchestrator`。
2. **owner + gate**:handler 读 `session.user_id`;lead-gate 读 `task_sessions.kind`。
3. **serve**:kernel MCP toolkit 暴露 task 工具;kernel 在 create 时把原生 toolkit 注入
   `session.mcp_servers`;删 resolver 的 `always_on_http_mcp_servers` 里的 `toolkit=` arm
   (§16.3)。
4. **移除**临时 host 桥(§14.5):host toolkit MCP server 不再 serve task 工具;删 task 工具的 host
   handler。
5. **沙箱地址**:发布/消费沙箱本地 toolkit 地址。

### 16.6 测试与回归

- 每个 task 工具经 kernel toolkit 解析,args/结果与 host toolkit 一致(golden 对齐)。
- member session 无法调 lead-only 工具(gate 于 `task_sessions.kind`)。
- 沙箱下,一次 task 工具调用**不**打 host(断言无 host-toolkit 请求),而由此产生的 task-event 写入
  **确**经 DataService 落地 host durable store。
- 非 task harness 工具(`memory`、`submit_skill`)仍 host serve(不变)。

### 16.7 开放边

1. **沙箱内 toolkit 传输** —— kernel 自己 toolkit 走 loopback HTTP vs stdio MCP。选 runtime 的
   MCP 客户端已最便宜地会讲的那个。
2. **`harness` 条目身份** —— 保留 `harness` server 名,让 runtime 和既有 session 不变地解析它,即便
   它现在指向 kernel 本地。

---

## 17. P6 详解 —— 事件、副作用、公共 API、实时中断

**P6** 的完整设计 —— 让搬迁后的引擎可观测、可控的接线,完成 **D2** 的 host 终结查询侧,闭合实时
interrupt/stop 路径(缺口 #4)与 activity 投影路径(缺口 #6)。

### 17.1 公共 HTTP —— host 面保留,委托 kernel

`api/routes/tasks.py` **形状不变留 host 侧**(前端面刻意保留;后续前端重构可再议)。每个路由通过新
`KernelClient` task 方法(双传输)委托给 kernel,与 store/session 路由已有做法完全一样:

| 路由(host,不变) | 委托给 |
|---|---|
| `POST /v1/projects/{id}/tasks`(kickoff)、`:draft`、`:commit`、`:abandon`、`:inject` | `KernelClient.task_*` → `TaskOrchestrator` |
| `POST /v1/tasks/{id}:intervene`(note / revise_goal / pause / resume / **stop**) | `KernelClient.task_intervene`(§17.3) |
| `GET /v1/tasks…`、`/events`、`/plan` | host 读路径,读 **durable** task 表(§15.9) |
| `GET /v1/tasks/{id}/events/stream`(SSE) | host DB 轮询 durable `task_events`(§17.2) |
| `POST /v1/runs/{session_id}:stop` | `KernelClient.stop_member` |

host 在 kickoff/inject 入口点算好回合增强(attachments + `additional_context`)并传入 kernel 调用
(§14.9 A)。

### 17.2 task SSE —— durable 游标,host 终结

既有 500ms DB 轮询 SSE(`_iter_task_events_sse`)保持其 `?after_seq=` 契约,现在在从 durable store
读的 `task_events.id`(durable autoincrement,§15.3)上翻页。对**易逝**沙箱,host 直接从 durable
store 读历史(kernel `events` 已用的 `DataServiceReadClient` 范式),所以死沙箱仍供 task 历史;实时
增量随 durable 行落地而到。

> 对齐说明:一个在途分支正把事件投递统一到 user 级控制面 SSE(`GET /v1/stream`)以替代客户端轮询。
> task SSE 应落到 P6 执行时的当前投递机制上 —— 这里的 **durable 游标语义与传输无关**,两种方式下都
> 成立。

### 17.3 实时 interrupt / stop —— 单次 kernel 内扇出(缺口 #4)

因为沙箱单元是 **task**(§14.1),一次 stop/interrupt 恰好触达**一个** kernel(该 task 的)——
**无跨沙箱扇出**。路径:

```
host 路由  POST /v1/tasks/{id}:intervene {action: stop|pause}
   └─► KernelClient.task_intervene(user_id, task_id, action)      (一次调用)
         └─► TaskOrchestrator.stop_task(task_id)      [在该 task 的 kernel]
               ├─ 设 task 状态(assert_transition)
               ├─ LiveMemberRegistry.drain_members(task_id)  → 对每个活 member:
               │     ├─ mailbox.post(member, shutdown)        (进程内队列)
               │     └─ SessionOrchestrator.interrupt(member) (kernel 原生)
               └─ mailbox.post(lead, shutdown)
```

所有扇出都在**进程内、对调度同步**地跑在内存 mailbox/registry 上 —— 正是 §14.1 保住的基座。这也顺
带修掉已知的 stop/pause **de-spin** 缺口([[valuz-task-stop-state-mismatch]]):`stop_task` park
members,panel 依 task 状态 de-spin,在此闭合而非搬过去。`pause` 是同一路径减去终态;`resume` 经
recovery 重新驱动(§14.7)。

### 17.4 activity / recents / project 反查 —— 事件驱动而非回调(缺口 #6 —— 已核实)

**核实结果**(对着代码检查):kernel `create_session`(`sessions.py:159`)发**无**生命周期事件 ——
它是静默 `save_session`;跨会话流(`subscribe_all_events` / `_global_taps`)只承载经**运行中**
session event bus 流动的事件,建会话不经它。今天 host 用一个显式 `project_index.record(...)` 配对
每次 create(chat 点 + task 点)。据此,优雅的解法**无需新 kernel 事件**:

- **recents/activity 是 chat-scoped。** `ProjectSessionRow` + `touch_activity` 服务 **chat** 会话
  (`run_orchestrator.py:121`,chat 回合路径)。task activity 视图读**任务表**
  (`queries.list_activity_tasks_page`),不走索引;`list_session_ids(user_only=True)` 甚至把 task
  kind 过滤掉。chat 由 **host** 路由创建,所以 host 保 `record` / `touch_activity` 原样 —— **无
  task/kernel 变化。**
- **task session 弃用其索引记录。** 搬迁后它们在 kernel 侧创建、不再调 `project_index.record`。
  recents 不需要它们(任务活跃度来自任务表)。
- **`project_of(session_id)` 反查** —— task 记录唯一承重的用途(3 个调用方:
  `tools_agent_proposal`、`docs_mcp_server`、`agents` 路由),可能收到 task-session id。改从 durable
  session 的 **`metadata.valuz.project_id`** 解析(每个 session —— chat、lead、member —— 都带;host
  本就读 durable sessions),`task_sessions` 兜底。这移除 task session 需要 `project_index` 行的最后
  理由。

→ **无 `session.created` 事件、无 kernel→host 回调。** 索引在 host 创建点保持 host 驱动;task
session 干脆不进它。

**延后(按前端保留决策,缺口 #9):** 在**读时**从 durable `sessions` + `messages` 表派生 chat
recents(last-activity = `MAX(messages.started_at)`),彻底退役维护型索引(只有 host-only 的
`queue_paused_at` 留一张极小侧表)。一次 sessions 模块重写 —— 后续,不是现在。

### 17.5 副作用 —— 事件优先(来自 §8)

decision inbox、通知、记忆调度变成对 **task 事件的 host 反应**(host 拥有其查询路径,D2),而非同步
kernel 回调:

- **decision inbox / AskUser** —— "某 member 卡在澄清问题上"是一个 **kernel 原生**事实(runtime 的
  pending-action / `AskUserQuestion` 状态);kernel 发 `awaiting_user` / `user_answered` **task
  事件**,host decision inbox 变成对它们的纯**读投影**。actor loop 内不再 probe host aggregator。
- **通知 / 记忆** —— host 订阅终态 task 事件(`task_failed`、`task_completed`)并据此驱动通知台账 +
  记忆调度。
- member 归属事件在 emit 时盖 `payload.agent_name`([[valuz-task-event-member-name]])。

### 17.6 P6 内部迁移步骤(有序)

1. **KernelClient task 方法**(kickoff/draft/commit/abandon/inject/intervene/stop_member + 读)双
   传输;`api/routes/tasks.py` 委托。
2. **入口点增强** —— host 路由 handler 算 attachments + `additional_context` 并传入 kernel 调用
   (§14.9 A)。
3. **SSE** 走 durable `task_events`(§17.2)。
4. **实时 interrupt/stop** 扇出(§17.3)+ panel de-spin。
5. **activity 投影**事件驱动(§17.4);确保 `session.created` 发出。
6. **副作用反应**事件优先(§17.5):decision inbox 读投影、通知、记忆。

### 17.7 测试与回归

- 任务中途实时 stop:所有 member interrupt、lead 关停、panel de-spin、task 到终态 —— 在**两种**
  拓扑。
- SSE 在 durable 游标上重连后无缝/单调;死沙箱仍供史。
- activity feed 在 create/turn/finish 上更新,无任何 kernel→host 回调。
- AskUser:卡住的 member 纯从 task 事件浮现在 decision inbox。

---

## 18. 不在范围 —— session/task 沙箱起停控制(商业版)

北极星**控制面** —— *谁*在*何时*起停一个 task 的沙箱(idle-stop、打开即 resume、按请求供给),以及
当前 per-**user** `_kernel_for(user_id)` allocator 向 per-**task** 粒度的演进 —— 是**商业版**关切,
**不在此设计**。本重构是它的**地基**:它保证 task=sandbox 语义(§14.1)、把整个 actor 生命周期放到
kernel seam 之后(D3)、并让 host 成为无状态控制+数据面 —— 这些是起停控制器需要的前提。控制器本身是
后续(称之为 P8),在本基座之上于商业 overlay 里构建。在此仅记录以说明边界:**交付 §§1–17 就是本
重构的全部;P8 后续在其上构建。**

