# 会话事件流生命周期统一(Session-Lifetime Event Stream)

> **状态(2026-07-21):** 设计定稿,随 #590 实施桌面端重构(阶段一)。
> 云端沙箱事件泵租约重校验为独立后续(阶段二,见 §7)。

## 1. 问题与根因

### 1.1 症状族谱

以下线上症状同根同源:

- 队列消息派发后在队列条和 transcript **两边隐身**,刷新才见;
- 队列排空期间 loading 态每条 off→on **闪屏**;
- 商业版云端沙箱下 follow-up 发送后**复用的流不出新 message 和事件**(#589 修复其前端半);
- 定时任务/他端发起的 turn 全程卡「等待中」;
- 后台任务(`run_in_background`)结束后的自发唤醒 turn 无人接收(现靠 3s 轮询兜底)。

### 1.2 架构根因

**桌面 `ConversationPage` 是全系统唯一"每 turn 拆流重订"的实现,而后端从不要求这样:**

- host 会话 SSE(`event_sse_adapter.iter_events_sse`)**永不因 turn 结束关闭**——
  循环唯一退出条件是客户端断连;terminal 帧只被翻译转发("yield … forever")。
- 内核会话总线跨 turn 存活:每次 `run_turn` 复用同一 `SessionEventBus`,runtime
  空闲驱逐**特意保留 bus**,已挂接的 tap 收到后续每个 turn 的全部事件。唯一
  拆 bus 的动作是会话删除(`cleanup()`)。
- **webui `chat-store` 已经是目标架构**:`attach` 建一条流、`detach` 拆,turn
  结束不动流;`isStreaming` 从事件推导(终态帧翻 false),无跟随器。

拆流的历史动机是"用流的关闭释放 `sending`"。由此派生的全部机制——drain-follower、
`checkNow` 探针、`run.started` 边沿触发、`draining` 标志的订阅管理职责、
`sawTurnStart`/`requireUserBeforeTerminal` 回放门、`.finally` 释放 sending、
bg 任务 3s 轮询、auto-resume 桥——**都是在补偿这个客户端自己制造的断层**。

### 1.3 手动发送 vs 队列消息的不对称

手动发送:turn 和流由**同一个前端动作**同步创建(先开新流、后 POST),不存在
"检测 turn 何时开始"的问题。队列/定时/他端 turn:**由后端在未来某刻创建**,
前端拆掉流之后只能靠侧信道(一次性控制帧、一次性探针、draining 标志)猜测那个
时刻并重建流——探测链任何一环失手,该 turn 即无人接收。流常开后此不对称消失,
四条路径归一。

### 1.4 云端沙箱的第二个断点(阶段二范围)

scope 模式下 chat **每 turn 换新沙箱实例**,而 host 事件泵
(`subscribe_session_events_existing` → `alloc.peek`)attach 时 peek 一次、
只在旧流**死掉**时才重 peek。旧实例在 idle 宽限期(默认 600s)内持续心跳,
流永不死 → 泵被钉死在旧实例上,新 turn 的实时帧系统性丢失,只剩 2s 一次的
持久化回填(无 delta)。此为后端独立缺陷,与前端重构解耦(见 §7)。

## 2. 目标架构

**每个打开的会话页 = 一条数据面流。订阅生命周期 = 页面停留;busy 从事件+状态
推导;turn 边界只是流上的普通事件。**

```
会话打开(selectedSessionId 就绪)
  → 水合历史(refreshEvents,推进 historyCursor)
  → 开流(afterSeq = 水合后游标)
  → [常开:live tap + 服务端 2s 持久化回填 + 断线指数退避重连(每次重连先 REST gap-fill)]
会话切换 / 组件卸载
  → 唯一的拆流时机
```

### 2.1 busy 推导

```
isBusy      = sendPending || session.status === "running"
displayBusy = isBusy || (queueDraining && !queuePaused)   // 队列衔接连续性,不变
```

- `sendPending`(原 `sending` 语义收窄):点击发送 → true;本 turn 的
  `message.user` 回显或 `session.update{running}` → false;发送失败/中断/切会话 → false。
  只覆盖"点击 → turn 开始事件"的乐观窗口(附件慢启动场景 500–3000ms)。
- `session.status`:由数据面 `session.update` 事件写入(内核现已在 turn 开始
  补发 `{running}`——#590 地基事件,一切发起方通用)+ 乐观写 + busy 下降沿的
  `refreshActiveSession` 权威对账。
- **陈旧终态防御**(取代 `sawTurnStart`):`sendPending` 为 true 期间,权威读
  返回的终态 status 不落地(慢启动窗口内后端还没翻 running);uid 已见过的
  terminal 帧(回放)不触发终态处理。这是本重构唯一的 QA 高危点
  (图片上传计时器冻结/Stop 回退的历史回归位)。

### 2.2 turn 边界簿记

原先塞在 `stopSubscription` 里的动作全部改挂 `isBusy` true→false 下降沿
(文件树/产物刷新、队列 refetch、drain 收尾侧栏刷新已是此模式):

- `refreshActiveSession`(状态权威对账);
- `fetchSidebarSessions`(保留 `queueContinuesRef` 门:排空未完不刷);
- Workflow 卡片终态兜底(改键到"观察到终态事件",而非流关闭——流不关后
  迟到的 workflow 终态快照反而能送达,原竞态消失)。

### 2.2b 连接预算(每源 6 连接上限)

Chromium 对同源 HTTP/1.1 连接上限约 6,被持有的 SSE 计入其中——池被打满会阻塞
一切 fetch(#508 白屏事故类)。本应用的常驻账本:控制面 `/v1/stream`(1)+
通知流(1)+ 会话数据面流(1,本重构从"仅 turn 期间"变为"停留会话页全程")
= 单可见 tab 3/6,安全。防护:**可见性护栏**——tab/窗口隐藏即释放会话流,
回前台重开(游标续传 + 服务端回填补齐漏掉的事件),常开成本严格限定在唯一
可见的会话 tab。webui 多 tab(≥2 全可见)在重构前后同样逼近上限,根治靠
把数据面复用进 `/v1/stream` 单流多路(范围外,见 §7)。

### 2.3 连接控制器

- 断线(任何原因、任何时刻)→ REST `listEvents(cursor)` gap-fill → 指数退避
  重连(1s…15s 封顶,**不设放弃上限**——沿用既有事故结论:封顶重试会把慢
  turn 演成假完成);收到任何帧即重置退避。
- 首次打开跑一次 reconcile burst(400/1200/2500ms 窗口合并,防"resume 空白"
  竞态);重连不跑(gap-fill 已覆盖)。
- 心跳帧推进 history 游标(采纳 `session-stream.ts` 的机制,长连流必需)。

## 3. 耦合面处置清单

三类处置(核查依据:2026-07-21 对 6800 行 ConversationPage 的逐点盘点):

**删除(c)** — 终态帧→`stopSubscription` 耦合;`.finally` 释放 sending +
`reconnectPending`;`requireUserBeforeTerminal`/`sawTurnStart` 及两处回放门;
`skipReconcileBurst`;drain-follower 全套(边沿触发+level 探针+2s 重试环);
auto-resume 桥的 `tryResume`/created→running 桥/`reconcileFinishedTurn`;
bg 任务 3s 轮询;interrupt 的手工补拉;发送路径游标特例;`abortRef`/
`isSendInFlightRef` 的防双订阅守卫群(与被删订阅方一起整体删,不可零碎删)。

**重做(b)** — 边界簿记挪到 busy 下降沿(§2.2);`reconcileStreamEnd` 收敛为
连接控制器(§2.3);burst/水合门改为每次打开一次;`sending` → `sendPending`
推导(§2.1);`dispatching` 气泡门从 `!sending` 改为 `!isBusy`;
`seenEventUidsRef` 加容量上限(长页面存活更久,参照 chat-store 8192)。

**兼容保留(a)** — `historyCursorRef` 两序空间纪律(REST-only 推进);uid 渲染
去重;全部 busy 下降沿效果;pin/scroll refs;retryCounts;`displayBusy` 三个
UI 消费点;分页;切会话/卸载拆流。

## 4. 发送路径

`performSend` 不再开流(流已由会话打开效果持有):乐观 pending 卡 + 乐观
status 写 + POST;错误路径回滚 pending/`sendPending` 并 `refreshActiveSession`
(新公式下乐观 running 必须对账回滚,否则 busy 永挂)。防双发守卫依旧走
`isBusy`。`/conversation/new` → 真 id 晋升后由会话打开效果开流,POST 与开流
之间的事件由服务端初始回填(游标起点)覆盖。

## 5. 队列语义(不变项)

- 派发时机门在后端不变:post-turn drain 串行 + idle-kick + **忙检门**
  (`_session_busy`:turn 在飞或 bg 任务存活即等待,见 session-input-queue §14.5 补强⑤);
- `draining`/`dispatching` 字段保留:前者驱动 `displayBusy` 衔接与发送路由,
  后者补派发瞬间的气泡空窗(长连流下窗口近零,降级为 UX 细节);
- 前端 ticket 护栏、5s 队列 backstop 轮询保留(廉价、收敛一切 stale 角落)。

## 6. QA 计划

浏览器实机全流程(#538 教训:此文件的 sending/busy/流改动无实机 QA 不得合入):

1. 新会话首 turn:徽章 created→running 即时、计时器/Stop 正常、终态释放;
2. 图片/附件慢启动:发送后计时器**不冻结**、Stop 不回退(§2.1 防御位);
3. 运行中入队 2 条:气泡→dispatching→transcript 逐条实时流式,**无每条闪屏**,
   全程一条流(DevTools 网络面板确认无重订);
4. 排空间隙输入 → 正确入队(无 409);Stop → 队列暂停 +「继续」可恢复;
5. 断流注入(kill 后端重启):gap-fill 补齐 + 自动重连,无假完成;
6. 中断:立即释放 busy,不撕流,后续发送正常;
7. 切会话/回切:旧流拆、新流建,transcript 不串;
8. bg 任务:turn 结束后 bg 事件仍实时到达(3s 轮询已删)。

## 7. 范围外(独立后续)

1. **云端沙箱事件泵租约重校验**(§1.4):`_pump` 周期性重 peek /实例代际标记
   变更时主动终止旧迭代器,不再依赖旧流自然死亡。OSS seam 内修,商业版受益。
2. **webui chat-store 收编**:桌面重构落稳后,评估两实现共享同一 controller
   (`session-stream.ts` 已具雏形;其"clean close = 终局"注释相对 forever
   服务端已过时,收编时一并修)。
3. 排空是否等待 bg 任务的**产品级开关**(当前默认等待)。

**已了结**:§2.2b 可见性护栏(以及任何断线)留下的 live-only delta 缺口,
由内核侧的"进行中状态快照"补齐 —— 重连时按流下发累积**状态**而非 delta
序列,不引入游标/代际/gap,详见
[live-partial-snapshot.md](live-partial-snapshot.md)。护栏本身保持不变。
