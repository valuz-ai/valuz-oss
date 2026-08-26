# 会话输入队列(Session Input Queue)— 设计

> 状态:Design / Approved(2026-06-24)。本文是 Valuz "turn 进行中排队后续消息"能力的单一设计来源。
>
> 取向一句话:**turn 运行中允许用户继续提交消息 → 持久化进 host DB 的 per-session 队列 → 当前 turn 结束后由 host 按 FIFO + budget 预检自动续跑(无损但要等);队列项支持编辑/删除;打断 = 软暂停(保留队列、需显式继续);不做 Codex 式 `turn/steer`(无损即时注入),kernel 零改动。**

> **实现状态(2026-06-24):** 后端(model / `0009` 迁移 / datastore / service / 排空引擎 / boot 恢复 ①+②)+ 共享前端 core(`queue-api` + `chat-store`:`send`→`enqueue` 路由、边界 refetch、队列 actions)已完成并测试通过(后端 10 + 前端 5 个新测试,全量门禁按已知 RED 基线零增量)。前端 UI 已接入 **webui**(`ChatComposer` 运行中可入队 + `QueuedInputs` 气泡)**和 desktop `ConversationPage`**(运行中 Composer 旁出现"排队"发送键 + Enter 入队;`QueuedInputsBar` 气泡 编辑/删除 + 暂停后"继续";`Composer` 加 `queueWhileSending` 开关,非聊天场景不受影响)。迁移随 main 合并后顺延为 **`0009`**。一处实现补强:`_active_drains` 单飞守卫 + `is_draining_queue` 让 `send_message` 在两个排空项之间的极短 idle 窗口仍 409 防插队(§8.2)。

---

## 0. 背景与现状

### 0.1 起因

Codex app-server 有 `turn/steer`:turn 在跑时,用户可把新输入**并入当前 turn**——不开新 turn、不打断在途模型请求,模型跑完当前回合后在下一轮把新输入折进 history。Claude Agent SDK(streaming 模式,kernel 已在用)只有 **Queued Messages(顺序排队)+ interrupt**,没有 `turn/steer` 的等价物;Valuz Agent(DeepAgents)同样没有。

调研结论:`turn/steer` 那种"无损即时注入"是 Codex 独占、三 runtime 不对称的能力。**本设计只做三 runtime 都能一致提供的那一半——"排队、当前 turn 结束后按序续跑"**,把 steer 留作后续 Codex-only 增强(§11)。

### 0.2 当前"运行中再发"被三层硬阻断

- **前端**:`isStreaming` 时输入框 disabled、发送键变 Stop、`handleSend`/`send()` 双重 early-return(`ChatComposer.tsx:37/70`、`chat-store.ts:194`)。
- **Host**:`POST /v1/sessions/{id}/messages` → `SessionService.send_message`,`status=="running"` 直接 `SessionConflict` 409(`modules/sessions/service.py:1050`);否则置 running + `asyncio.create_task(_run_agent_background)` 后台跑、立即返回。
- **Kernel**:`RuntimePort` 仅 6 法(`run/submit_action/interrupt/close/update_sink/approval_rule_matcher`),无 enqueue;turn 串行靠 host 的 409。

"打断"半边已完整可用:Stop → `/interrupt` → `orchestrator.interrupt` → `runtime.interrupt`。**本设计不碰 kernel,也不改打断链路**,只在 host + 前端增加"排队 + 续跑 + 暂停/继续"。

### 0.3 Codex 队列是纯内存的——我们要做持久化

§3.5 明确:Codex 的两个队列(core 的 `TurnState.pending_input`、TUI 的 `InputQueueState`)**都是纯进程内内存、不落盘**,切会话即清。对长时间运行的任务不合理。因此本设计把队列**落 host DB**(`valuz.db`),扛客户端断连/刷新,并支持后端重启恢复(§9)。

---

## 1. 范围

### 1.1 In scope

- turn 运行中提交的后续消息进入持久队列,当前 turn 结束后按 FIFO 自动续跑。
- 队列项操作:**编辑、删除**。
- 打断 = 软暂停(停当前 turn、保留队列、暂停自动续跑),提供显式"继续"。
- 队列软上限 ≤ 20。
- 后端重启恢复:复位孤儿 running 状态 + 恢复未暂停会话的排空。
- 前端为纯 API 视图(自身不持久化队列状态)。

### 1.2 Non-goals(本期不做)

- **不做 `turn/steer`**(无损即时注入)——三 runtime 不对称,留作 Codex-only 后续(§11)。
- **不改 kernel / `RuntimePort` / 打断链路**。
- 不做"Turn off queueing"全局开关。
- 不做队列项跨会话/跨设备实时推送(单用户本地优先场景下用 refetch-on-boundary 即可,§8.4;真正多端 push 留作后续)。

---

## 2. 关键决策(已定)

| # | 决策 | 取值 |
|---|---|---|
| 存储 | 队列放哪 | **host + DB 表**(`valuz.db`,带 `user_id`/`project_id`/`session_id`) |
| 驱动 | 谁排空 | **host 驱动**,每条续跑前跑 `_enforce_budget` 预检 |
| 入口 | 入队接口 | **新 `/queue` 端点**;`/messages` 保持 running→409 |
| 打断 | interrupt × 队列 | 只停当前 turn;队列**保留但暂停**;需显式"继续"按钮 |
| 上限 | 队列容量 | **软上限 ≤ 20**(超出拒绝入队 + 提示) |
| 恢复 | 后端重启 | **①复位孤儿 running + ②恢复排空** 都做 |
| 参数 | provider/model | 入队时**镜像** `send_message` 的 override,续跑时回放 |

---

## 3. 数据模型

均在 `backend/valuz_agent/modules/sessions/models.py`,对齐既有 `SessionAttachmentRow` 规范(`Base + PrimaryKeyMixin + TimestampMixin + UserMixin`;业务键无 FK)。

### 3.1 新表 `valuz_queued_input`

```python
class QueuedInputRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """turn 运行中排队的后续用户消息。当前 turn 结束后由 host 按 FIFO + budget
    预检续跑。落库 → 扛客户端断连 / 后端重启(§9)。"""
    __tablename__ = "valuz_queued_input"

    project_id: Mapped[str | None] = mapped_column(String(36), index=True)  # 快速对话为 NULL
    session_id: Mapped[str]        = mapped_column(String(36), index=True)
    # 用户自撰部分的 JSON 快照(入队时冻结),取 kernel ``UserMessage`` 的子集:
    #   {"text": str, "attachments": [{"source_path": str, "parsed_path": str|None}]}
    # 注:additional_context 不进此快照——它是每轮重建的系统上下文(见 §8.1 / §8.6)。
    input:      Mapped[dict[str, Any]] = mapped_column(JSON)
    # queued(待发) | dispatched(已派发执行) | blocked(预检失败) | cancelled(被删/清)
    status:     Mapped[str]        = mapped_column(String(16), default="queued", index=True)
    position:   Mapped[int]        = mapped_column(Integer, default=0)      # 会话内 FIFO 序,单调递增
    provider_id: Mapped[str | None] = mapped_column(String(36))             # 镜像 send_message override
    model_id:    Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)                 # blocked 原因,给 UI
```

- `id`/`created_at`/`updated_at`(ms)来自 mixin;`user_id`(String(64), indexed)来自 `UserMixin`。导入需补 `JSON`(`from sqlalchemy import JSON, ...`)与 `Any`。
- **`input` 只冻结"用户自撰部分"**:`text` + 入队时该会话的"待发(pending)attachment 集"快照(并就地标记 `consumed_at`,杜绝带到下一条/下一轮,见 §8.6)。
- **`additional_context` 不冻结**:它是每轮重建的**系统上下文**(项目记忆 + 绑定 KB scope 等,`context_builder._build_additional_context`,每 turn 现算),冻结会导致几分钟后派发时回放陈旧记忆/KB。故在**派发时现算**注入(见 §8.1),与普通 turn 完全一致。
- `provider_id`/`model_id` 是 **turn 级覆盖参数**(非 `UserMessage` 字段),按决策 #4 单列镜像保存。
- FIFO 取序:`ORDER BY position ASC, created_at ASC`。`position` 入队时取该会话当前 `MAX(position)+1`。
- 历史隔离:队列项**不进**对话历史(kernel `messages`),只有真正派发执行时才作为新 turn 的 `UserMessage` 落 kernel。

### 3.2 `valuz_project_session` 增列(会话级暂停态)

暂停是会话级、且需持久(重启保持暂停)。在既有 `ProjectSessionRow` 加一列(仿 `consumed_at`):

```python
queue_paused_at: Mapped[int | None] = mapped_column(BigInteger)  # NULL=未暂停;有值=暂停时刻(ms)
```

---

## 4. Migration

新增 `backend/alembic/host/versions/0009_session_input_queue.py`(拉取 main 后链头已是 `0008` automation_origin_tool_call_id;host 链为**增量、保数据**:`drop_stale_host_tables` 保留任一已知 stamp 并 `alembic upgrade head` 前滚,不清库):

- `revision = "0009"`,`down_revision = "0008"`。
- `upgrade()`:`create_table("valuz_queued_input", ...)` + `batch_alter_table("valuz_project_session")` 加 `queue_paused_at`。
- `downgrade()`:`drop_table` + `batch_alter_table` 删列(SQLite 限制走 batch)。
- **无需改 `boot/schema.py`**:known-revisions 由 `_known_host_revisions()` 动态走链得出;`BASELINE_REVISION`(现仍为 `"0004"`,仅作引用常量)不必每次迁移 bump——参照 0005–0007 的落地方式。

既有用户数据原样保留。

---

## 5. API(contract-first:先改 `api/openapi.yaml`)

所有端点落 `api/routes/sessions.py` → `SessionService` → 新 datastore;一律按 `user_id` 限定归属(§10)。

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | `/v1/sessions/{id}/queue` | 入队一条。running → 排队;**idle → 立即踢排空**(避免卡死);超上限 → 409/422 + i18n 提示。Body:`{input: {text, attachments?}, provider_id?, model_id?}`(`additional_context` 不由客户端给,派发时现算)。 |
| GET | `/v1/sessions/{id}/queue` | 返回该会话 `status IN (queued, blocked)` 列表(FIFO)。刷新/重连用。 |
| PATCH | `/v1/sessions/{id}/queue/{qid}` | 编辑(仅 `status=queued` 可改)。Body:`{input}`(实际多为改 `input.text`)。 |
| DELETE | `/v1/sessions/{id}/queue/{qid}` | 删除一条(置 `cancelled` 或物理删,见 §8.5)。 |
| POST | `/v1/sessions/{id}/queue/resume` | 显式继续:清 `queue_paused_at` + 踢一次排空。 |

返回体统一回**最新队列列表**(前端纯视图,见 §8.4)。`/messages` 与 `/interrupt` 不变。

---

## 6. 前端(纯 API 视图)

- **composer**:`isStreaming` 时**不再禁用**;提交按 `isStreaming` 分流——running → `POST /queue`,idle → 原 `/messages`。
- **待发气泡**(composer 上方,截图样式):每条仅 **Edit(弹回 composer 改 → PATCH)/ Delete(→ DELETE)**;**无 Steer**。
- **暂停态**:打断后渲染"继续(N 条待发)"显式按钮 → `POST /queue/resume`;blocked 项展示 `error_message`。
- **同步模型**:加载/重连 `GET /queue`;每次自身变更用接口返回的列表就地刷新;turn 边界(`isStreaming` 由 true→false,来自既有 `events/stream`)触发一次 `GET /queue`(派发/blocked 后状态收敛)。详见 §8.4。
- **i18n**:`zh-CN` / `en-US` 同步新增文案(气泡操作、继续按钮、超限/blocked 提示)。

---

## 7. store 改造(`@valuz/core` chat-store)

- 移除"running 即 throw"的死路;新增 `queue: QueuedInput[]`、`enqueue/editQueued/deleteQueued/resumeQueue` actions(走新 `queue-api`,仿 `sessions-api` 手写客户端)。
- `send(prompt)`:`isStreaming ? enqueue(prompt) : <原 send>`。
- 监听既有 session 事件流:`isStreaming` 落沿 → `refetchQueue()`。

---

## 8. 排空引擎(host 侧)

### 8.1 主循环

在 `_run_agent_background` 跑完一轮后接 `_drain_queue(session_id)`:

```
_drain_queue(session_id):
  if project_session.queue_paused_at is not None: return          # 暂停 → 停,保留队列
  head = oldest QueuedInputRow where status=queued (FIFO)
  if head is None:
      finalize_session(idle); return                             # 队列空 → 这才收尾 idle
  try: _enforce_budget(session, head.provider_id/model_id)
  except BudgetExceeded as e:
      head.status = blocked; head.error_message = e.key
      finalize_session(idle); return                             # 预检失败 → 标 blocked,停
  head.status = dispatched
  ac = await _build_additional_context(session)                 # 派发时现算(记忆/KB),非冻结
  msg = UserMessage(text=head.input["text"],
                    attachments=_to_attachments(head.input["attachments"]),
                    additional_context=ac)
  run_turn(session, msg, provider=head.provider_id, model=head.model_id)
  # run_turn 结束后回到 _drain_queue 继续下一条
```

### 8.2 status 全程 running(防插队)

排空期间会话状态**保持 running**;只有"队列空 / 暂停 / blocked"时才允许收尾 idle。好处:`/messages` 在整段续跑期间持续 409,杜绝中途闪 idle 被别的请求插队。

### 8.3 idle 入队的即时踢动

`POST /queue` 时若会话当前 idle(刚好没在跑),入队后**立即启动 `_run_agent_background`/`_drain_queue`**,使该条立刻执行——避免"入队了但没有 turn 边界来触发排空"的卡死。

### 8.4 事件交付:refetch-on-boundary(本期)

队列只由"本客户端自身操作"和"host 排空"改变:
- 自身 POST/PATCH/DELETE/resume → 接口直接回最新列表;
- host 派发/blocked → 体现在既有 `events/stream`(新 turn 事件 / `isStreaming` 落沿),前端据此 `GET /queue` 收敛。

故**本期不新增 SSE 通道**;前端纯视图靠"接口返回 + 既有事件流落沿 refetch"即可覆盖截图全部交互。多端实时 push 留作后续(§11)。

### 8.5 软上限与编辑/删除

- 入队前查 `count(status=queued) < 20`,超则拒绝 + i18n 提示。
- Edit 仅 `status=queued` 行可改(`dispatched`/`blocked` 不可)。
- Delete:`status=queued` → 物理删或置 `cancelled`(统一选**物理删**,GET 只看 queued/blocked,简单)。

### 8.6 attachment 的"按条快照、不串条"

附件沿用既有 `SessionAttachmentRow` 的暂存语义(`consumed_at`:NULL=待发 pending,有值=已随某 turn 消费)。入队是消费点之一:

- `POST /queue` 入队某条时,把该会话当前 **pending 附件集**(`consumed_at IS NULL`)快照进这条的 `input.attachments`,并就地把这些行标 `consumed_at=now`。
- 这样:用户在输入框新加的附件**会跟着这条入队**;而**上一条已消费的附件不会串到下一条**(下一条入队时 pending 集已空)。与 `send_message` 现有"每 turn 只取 pending 集再 consume"完全同源,只是把消费点从"turn 执行时"提前到"入队时"。
- 派发时直接用 `input.attachments` 快照(不再二次查 pending),`additional_context` 仍现算(§8.1)。

---

## 9. 打断 / 暂停 / 继续 / 重启恢复

### 9.1 打断 = 软暂停

`/interrupt` 沿用现有(停当前 turn)**并**置 `queue_paused_at=now`。`_drain_queue` 见暂停即停,队列与气泡保留。

### 9.2 继续

`/queue/resume` 清 `queue_paused_at` + 踢一次 `_drain_queue`。前端显式按钮触发。

### 9.3 重启恢复(boot reconcile,①+②)

host 启动时(`boot/`)增加一步对账:
- **①复位孤儿**:`status=running` 但 kernel 无活跃 runtime(重启后必然如此)的会话 → 复位为 idle(其"当前 turn"已随进程消失)。
- **②恢复排空**:对"队列非空(queued)且 `queue_paused_at` 为空"的会话 → 重新触发 `_drain_queue`,继续把剩余队列跑完。
- 暂停态(`queue_paused_at` 非空)的会话**保持暂停**,不自动续跑(避免崩溃/重启后自己接着跑)。

这才兑现 DB 持久化的意义:长任务的后续指令扛得住后端重启。

---

## 10. 安全 / 归属

- 所有 `/queue` 操作经现有鉴权;datastore 全部按 `user_id` + `session_id` 限定,跨用户访问返回 404/403。
- `input`(`UserMessage` 形)当作普通用户消息处理(与 `/messages` 同源),无额外信任假设。
- `_enforce_budget` 在每条续跑前执行,保留现有 402/钱包语义(host 驱动的根本理由)。

---

## 11. 边界 / 未来

- **边界**:同一会话任一时刻只有一条 `_run_agent_background` 链在跑(天然串行);`/messages` running→409 不变;队列项不污染对话历史,直到派发。
- **已落地:Steer(立即发送 / send-now)**——见 §14。runtime-agnostic 的有损模拟,**不是** Codex 无损 steer。
- **后续①:Codex `turn/steer`(无损)**——在 §14 的同一 steer 入口下,runtime==Codex 时改走 `AsyncTurnHandle.steer()`(无损即时注入,不打断、不丢进度),其它 runtime 仍走 §14 的"打断+续跑"模拟;需扩 `RuntimePort` + kernel route,Codex-only 增强。届时前端 §14 的"末条才显 stopped"抑制可对 Codex 路径回退(无打断 = 无 cut turn)。
- **后续②:多端实时 push**——把 `queue.*` 作为轻量事件并入既有会话事件流,实现跨客户端实时气泡(替代 §8.4 的 refetch)。
- **后续③:Turn off queueing** 客户端偏好开关。

---

## 14. Steer(立即发送 / send-now)— 有损模拟

> 状态:Implemented(2026-06-25)。在 §1–§10 的排队基础上加一个"立即发送"动作:把某条排队项**马上**跑掉,打断在途 turn。因为三 runtime 里只有 Codex 有无损 `turn/steer`(§11 后续①),本期统一用**有损模拟**:`interrupt(静默)+ 把该条提到队首 + 复用既有排空`。

### 14.1 语义

气泡上每条 `status=queued` 多一个绿色"立即发送"键(截图的 ↪ Steer 位)。点击:

- 该条**提到 FIFO 队首**(`position = min−1`),清掉打断软暂停;
- 若有 turn 在跑(或正在排空)→ **静默打断**当前 turn(丢在途进度),既有 `_run_agent_background`/排空链收尾后,`_drain_queue_after_turn` 自然 peek 到队首这条并派发;
- 若会话恰好 idle → 直接 `schedule_drain` 踢一次。

被 steer 的那条**插队**先跑;其余排队项保持相对序,排在它后面续跑(Codex-like)。

### 14.2 "静默"打断(无 kernel 改动)

关键点:**不能**走 host 的 `SessionService.interrupt()`——它会 `finalize_session(stop_reason_type="user_interrupt")` 并对有队列的会话置软暂停。Steer 改调**底层** `kernel_client.interrupt()`:只让 runtime 停止吐 token,host 侧 `run_session_to_idle` 读回 `status=idle`(`_resolve_turn_status` 见 stop_reason 非 error → idle),以**干净 idle** 收尾(无 `session_error`,无 host 软暂停)。

但 **kernel orchestrator 自身**在 `runtime.interrupt()` 后会把被切 message 标 `status="cancelled"` + `stop_reason=UserInterrupt`(`orchestrator._finalize_message`)——这是 kernel 行为,kernel 零改动下抹不掉。因此"前台不显示已中断"放到**前端**解决:

- **前端抑制**:`StopReasonHint`("stopped" 角标)**只在最后一条且非 streaming** 时渲染。Steer 必然续跑 → 被切 turn 不再是最后一条 → 角标自动隐藏;真正的 Stop 停在末条 → 仍显示。副作用:普通 Stop 后又手动发一条,旧角标也隐藏(可接受,属历史噪音)。
- desktop 侧无 per-message 角标;`SessionStatusPill` 看**会话状态**(steer 是 idle→running,不经 cancelled),"已中断" toast 仅 Stop 键触发,故 desktop 天然静默。

### 14.3 并发 / 不踢竞争排空

running / draining 分支**只**打断,**不**自己 `schedule_drain`——让在途链的 post-turn 排空接手(`_active_drains` 单飞;提队首已先落库,排空 peek 必见)。自己再踢会和在途链抢着起下一条 turn、和切 turn 的 finalize 抢会话状态。仅 idle 分支才 `schedule_drain`。

### 14.4 端点 / 改动

- 契约:`POST /v1/sessions/{id}/queue/{queue_id}/steer` → `QueuedInputList`。
- datastore:`promote_to_front`(仅 `status=queued`,`position=min−1`)。
- service:`steer_queued`(提队首 → 清暂停 → running/draining 则静默 `kernel_client.interrupt`,否则 `schedule_drain`)。
- 前端:`queue-api.steer`、chat-store `steerQueued`、webui `QueuedInputs` + desktop `QueuedInputsBar`/`ConversationPage` 绿色键、webui `MessageList` 角标抑制、i18n `common.queueSteer`。

### 14.5 排空期的直播(desktop drain-follower 修正)

> 背景:desktop **每 turn 拆 SSE**(收到 `session.idle` 即 teardown 释放 loading)。排空里每条都是独立 turn,所以需要一个"跟随器"在每条 drained turn 起来时重新订阅。原实现(a7ed986)用 `轮询直到 status==="running" 再订阅`,有两个洞——steer 把它们都暴露了:

- **洞① 快 turn**(单条 steer 也看不到):被 steer 的 turn 可能在 500ms 轮询间隙内就跑完了,`status` 已回 idle → 跟随器永不订阅 → 该 turn 只在 DB,重开才见。
- **洞② 队列空窗**(两条 steer 一条、另一条续跑看不到):`list_queue` 只返回 `queued`/`blocked`,**dispatched(在跑)那条不在列表里**。上一条跑完后下一条已 dispatched → 前端 `queue` 空 → 跟随器以为没活了就停 → 下一条无人订阅。

修正:

1. **`QueuedInputList.draining`**(后端 `is_draining_queue(session_id)`):排空链在飞就为真。跟随器据此在"队列看起来空、实则有 dispatched 项在跑"时**继续跟**,直到最后一条结束(填补洞②)。`_active_drains` 整段排空期间恒为真,只有最后一条结束才落,故无中途误判。
2. **订阅条件改为 `running` 或 `DB 有 maxSeqRef 之后的新事件`**:后者用 `listEvents(maxSeqRef)` 探一下——命中说明有一条 turn 在轮询间隙跑完了,`subscribeToSession` 回放这些事件并在回放到那条的 `session.idle` 时**自终止**(填补洞①)。安静 idle(无新事件、无在跑)**不订阅**——裸订阅会把 SSE 挂住(条间无妨,但最后一条之后会永不释放 `sending`)。

这样单条/多条、快/慢 turn 都直播;最后一条结束后 `draining=false`、队列空 → 跟随器自然安静。

两处补强(2026-07-20):

3. **`QueuedInputList.dispatching`**:洞②的"draining 兜住跟随器"没有兜住**气泡本身**——队头被标 `dispatched` 后立即从 `items` 消失,但它的 `message.user` 要到 turn 真正起来才落 kernel;边界 refetch 落在这个空窗里,该消息就在队列条和 transcript **两边都不可见**(刷新才回来)。修正:排空引擎在标 `dispatched` 前先登记 `_dispatching_heads[session_id] = head_id`,turn 返回后清除;`list_queue` 把这条以 `dispatching` 字段(完整 `QueuedInput`,status=`dispatched`)返回。前端可据此让气泡一直渲染到 transcript 出现该消息为止,而不是提前一次 refetch 就丢。
4. **`schedule_drain` 同步占位**:原实现在 spawn 的 task 内部、且在一次 awaited owner 查询之后才 `_active_drains.add`,导致 idle-kick 的 `enqueue` 自己的响应就可能返回 `items=[], draining=false`(排空已把该项 dispatch 走)——跟随器从一开始就不武装,turn 在服务器上默默跑完。修正:`schedule_drain` 在**返回前**同步占位(spawn 的 task 负责释放,owner 查询失败等早退路径同样释放),保证 enqueue 响应必然 `draining=true`。
5. **忙检门(busy gate)**:排空原本把"上一条处理完"等同于"内核 turn 进入 idle"。但派生了 `run_in_background` 后台任务的 turn 在回复流完的瞬间就 idle 了,后台任务(和用户看到的"后台任务运行中"chip)还在继续——此刻立即派发下一条,观感就是"队列消息打断了上一条正在处理的消息"。修正:派发前 `_session_busy` 检查(`status=="running"` **或** `bg_busy_session_ids` 含该会话即为忙),忙则 2s 轮询等待而非退出——占位保持(前端 `draining=true` 维持连续性),每轮重查 shutdown/暂停/队列变更,Stop(软暂停)和 steer(打断)是等待期的逃生口。bg 探测失败按不忙处理:偶发提前派发好过永不派发。

---

## 12. 改动清单

| 层 | 文件 |
|---|---|
| 契约 | `api/openapi.yaml`(+5 端点 & schema) |
| Host model | `modules/sessions/models.py`(`QueuedInputRow` + `ProjectSessionRow.queue_paused_at`) |
| Host migration | `alembic/host/versions/0009_session_input_queue.py`(down_revision `0008`);无需改 `boot/schema.py` |
| Host datastore/service | `modules/sessions/datastore.py`(队列 CRUD)、`service.py`(`_drain_queue` / enqueue / resume / 上限 / 预检) |
| Host boot | `boot/`(重启对账 ①+②) |
| Host routes | `api/routes/sessions.py`(+5 端点) |
| 前端 API | `packages/core/src/api/queue-api.ts`(手写,仿 sessions-api) |
| 前端 store | `packages/core/src/store/chat-store.ts`(queue 状态 + actions + send 分流) |
| 前端 UI | composer 组件(气泡列表 + Edit/Delete + 继续按钮);解除 running 禁用 |
| i18n | `i18n/locales/{zh-CN,en-US}.json` |
| 测试 | host(datastore/drain/budget/暂停/重启对账)+ 前端(分流/气泡/refetch) |

---

## 13. 验证

按仓库规约:`make test-all` / `make typecheck` / `make lint` 全过(main 非全绿,按已知 RED 基线做 delta,不追求绝对绿);UI 改动浏览器验证;迁移 `upgrade`/`downgrade` 双向跑通。
