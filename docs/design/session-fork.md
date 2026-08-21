# Session / Message Fork 调研与设计基础

> 状态:**P0 + P1 + P2 全部已实现**——三个 runtime 的
> `RuntimePort.fork_session` 全部接线(codex `thread/fork` / claude
> 离线 transcript fork / deepagents sqlite checkpoint 链复制),kernel
> fork 路由、host seam + REST、前端两个入口(会话头部菜单 "Fork 会话"、
> 消息 hover "从此处 Fork")就绪。实现细节见 §6,交互全貌与已敲定的
> 决策见 §6.5;遗留跟进项见 §6.5 末尾。
>
> 调研基线(2026-08,均为本仓库实际锁定/捆绑的版本,关键结论经实测验证):
> `claude-agent-sdk` 0.2.128(bundled CLI 2.1.220)· `openai-codex` 0.144.4(捆绑二进制
> codex-cli 0.144.4)· `langgraph` 1.2.6 + `langgraph-checkpoint` 4.1.1 +
> `langgraph-checkpoint-sqlite` 3.0.3 · `deepagents` 0.5.9。
>
> 参考:[Codex App Server API](https://learn.chatgpt.com/docs/app-server#api-overview)、
> [Claude Agent SDK — Sessions](https://code.claude.com/docs/en/agent-sdk/sessions.md)、
> [LangGraph — Time Travel](https://docs.langchain.com/oss/javascript/langgraph/use-time-travel.md)。

---

## 1. 产品形态与目标

对标 Codex / Claude Code 的 CLI 与桌面客户端,fork 有两个粒度:

| 粒度 | 语义 | 竞品对应 |
|------|------|----------|
| **Session fork** | 复制整段会话历史,开一个新会话继续 | codex CLI `/fork`、Claude Code `/fork`、codex App "Continue in new chat"、Claude Code App 右键 "Fork" |
| **Message fork** | 从历史中某条消息处分叉——该消息(含)之前的历史进入新会话,之后的丢弃 | codex App 消息上的 "Continue in…"(可选 this workspace / new worktree)、Claude Code App "Fork from here" |

共同的产品语义:**fork 是非破坏性的**——原会话完全不动,新会话获得截至锚点的完整
模型上下文(不是文本摘要式的"导入",runtime 原生历史被真正带过去)。

本调研回答三个问题:

1. 我们接入三个 runtime 时,是否保存了做 message 级 fork 所需的原生消息标识?
2. claude-agent-sdk 是否只有 session 级 fork?
3. DeepAgents 是否只能靠底层 LangGraph checkpoint 实现?

**结论速览**:三个 runtime 的底层**全部具备 message 级 fork 原语**;我们侧的缺口不在
runtime 能力,而在于 (a) 逐轮的原生锚点(turn id / message uuid / checkpoint id)
**一个都没落库**,(b) fork 出的新原生 thread id **没有写入通道**
(`CreateSessionRequest` / `UpdateSessionRequest` 均不接受 `runtime_session_id`)。

---

## 2. 锚点对照:kernel Message ≡ runtime 的一轮

Kernel 的持久化模型对 fork 非常有利:一次 `run_turn` 产生一个 `Message`
(一条用户输入 → 一个完整 assistant 回合,`kernel/src/core/orchestrator.py`
`run_turn`),而这恰好与三个 runtime 的"轮次"一一对应:

| Runtime | 一个 kernel Message 对应 | fork 锚点(原生标识) | 我们现在存了吗 |
|---------|--------------------------|----------------------|:--:|
| Codex | 一个 codex **turn** | `turn.id`(UUIDv7) | ❌ |
| Claude Agent | 一个 CLI turn(transcript 中一段 uuid 链) | transcript 条目 `uuid` | ❌ |
| DeepAgents | 一轮带新输入的 graph 调用(一个 `source="input"` checkpoint) | `checkpoint_id`(uuid6,时间有序) | ❌ |

会话级原生 id 已有落库位:`sessions.runtime_session_id`(claude 的 SDK session id、
codex 的 thread id;deepagents 存的是我们自己的 `session.id`,见 §5.3)。

轮次级锚点的落库不需要 migration:`messages.metadata` JSON 列现成
(`kernel/src/adapters/sqlalchemy_store/models.py`),orchestrator
`_finalize_message` 已有往 metadata 写 `citation_bundle` / `claim_audits` 的成熟模式。

---

## 3. 各 Runtime 的 fork 原语

### 3.1 Codex — `thread/fork`(协议原生,turn 粒度,0.144.4 实测可用)

app-server 协议原生提供 turn 粒度 fork(`codex-rs/app-server-protocol/src/protocol/
v2/thread.rs`,方法名 `thread/fork`):

**`ThreadForkParams` 关键参数**:

| 参数 | 语义 | 0.144.4 实测 |
|------|------|:--:|
| `threadId`(必填) | fork 源线程 | ✅ |
| `lastTurnId` | 截到该 turn,**含**;进行中的 turn 被拒(`-32600`) | ✅ 可用 |
| `beforeTurnId` | 截到该 turn **之前**(不含);experimental | ❌ **静默降级**(见下) |
| 省略两个锚点 | fork 到最新 = 整线程复制 | ✅ |
| `ephemeral` | 不落 rollout、不进 `thread/list` | ✅ |
| `cwd` / `model` / `sandbox` 等 | fork 时覆盖配置(桌面 App 的 "new worktree" 支路靠 `cwd`) | 部分字段 0.144.4 SDK 无 |

**响应**:完整 `Thread`(此时 `turns` 携带截断后的历史 items,可用于回填)+
生效配置;`thread.forkedFromId` = 源线程 id。wire 顺序恒定:fork 响应 →
`thread/tokenUsage/updated` → `thread/started`,fork 完新线程立即是活线程。

**实现机制(0.144.4 = legacy 复制式)**:读源 rollout jsonl
(`<CODEX_HOME>/sessions/…/rollout-<ts>-<thread_id>.jsonl`)→ 按锚点截断
(`codex-rs/core/src/thread_rollout_truncation.rs`)→ 写全新 jsonl(头部记
`forked_from_id`)。源与 fork 完全独立。repo HEAD 已是 paginated 引用式
(`history_base` 指向源前缀,源线程被引用时不可删)——升级 codex 时需重验,
见 §7 版本护栏。

**关键陷阱(实测发现)**:0.144.4 对 `beforeTurnId` 的处理与未知字段一致——
**被接受但被忽略,默默 fork 到最新**。该参数由 codex #33211 引入,不在
`rust-v0.144.4` 祖先链上。护栏:harness 侧只允许发 `lastTurnId`;
`beforeTurnId(N)` ≡ `lastTurnId(N-1)` 可等价模拟(排除第一个 turn = 空历史,
直接 `thread/start` 新会话即可)。

其他确认项:

- codex CLI 的 `/fork` 与桌面 App、任何 app-server 客户端**同路**
  (TUI `SlashCommand::Fork` → `ClientRequest::ThreadFork`)。
- turn id 的获取通道:`turn/start` **响应**(发起方立即拿到)、
  `turn/started` / `turn/completed` 通知、以及 `thread/read(includeTurns=true)`
  (存量会话事后回填的兜底通道)。
- 我们未设 `CODEX_HOME` 覆盖,所有 session 的 codex 子进程共享
  `~/.codex/sessions`;fork 从磁盘读 rollout,**不要求源线程正在加载**——
  任何新拉起的 codex 进程都能按 thread id fork 历史线程,对实现有利。
- 血缘只在 rollout header 与 fork/read 响应里;`thread/list` 的
  `forkedFromId` 恒为 null(实测)——**父子关系必须由我们自己在 kernel 侧落库**。
- 备用原语 `thread/rollback(numTurns)`:从尾部丢弃 N 个 turn,**破坏性**
  (改原 thread),不回滚文件改动。仅作 rewind 类交互的兜底,fork 特性不用它。
- Python SDK 封装现成:`openai_codex` `client.thread_fork(thread_id, params)` /
  async 版,无需裸 request。

### 3.2 Claude Agent SDK — session fork 有文档,message fork 有官方实现但文档未收录

**Session 级(官方文档明面能力)**:`ClaudeAgentOptions(resume=<session_id>,
fork_session=True)` → CLI `--resume` + `--fork-session`,整段历史复制成新
session id,原 session 不变。新 id 从 init SystemMessage / ResultMessage 取。

**Message 级 —— 存在,两条路径**(0.2.128 实测确认,官方 sessions 文档未提及):

- **路径 A(推荐):离线工具函数 `fork_session(session_id, directory=None,
  up_to_message_id=None, title=None)`** —— 顶层公开导出
  (`claude_agent_sdk/__init__.py`,实现在 `_internal/session_mutations.py`)。
  纯 transcript 变换,不拉起 CLI 进程:
  - 按 `up_to_message_id`(= transcript 条目的 `uuid` 字段)切片,**inclusive**,
    找不到抛 `ValueError`;
  - 为每条消息重新生成 uuid 并保持 `parentUuid` 链一致,写出新的
    `<new-uuid>.jsonl`(同一 project dir),原 session 完全不动;
  - 每条消息带 `forkedFrom: {sessionId, messageUuid}` 溯源标记;
  - 剔除 sidechain(subagent 转录)与 progress 条目;**不复制 file-history
    快照**(文件系统状态不随 fork 走,与 codex 语义一致);
  - 之后 `ClaudeAgentOptions(resume=<新id>)` 正常续聊。
- **路径 B:CLI 隐藏 flag `--resume-session-at <message-uuid>`**(hideHelp,
  `--help` 不显示;需与 `--resume` 组合,加 `--fork-session` 则为非破坏性分叉)。
  TypeScript SDK 有一等选项 `resumeSessionAt`;**Python SDK 0.2.128 无对应字段**,
  可经 `extra_args={"resume-session-at": "<uuid>"}` 透传。未文档化,CLI 升级
  理论上可变——路径 A 更稳。

**锚点获取**:fork 匹配的是 transcript 的 `uuid`(不是 Anthropic API 的
`msg_…` id)。AssistantMessage 的 `uuid` 正常随流下发;**要在流里拿到
UserMessage 的 uuid 需 `extra_args={"replay-user-messages": None}`**。

**正交机制,勿混淆**:`ClaudeSDKClient.rewind_files(user_message_id)`
(配 `enable_file_checkpointing=True`)回滚的是**磁盘文件**,不改会话历史;
与 fork 可组合成"消息级完整回退",但不是 fork 本身。

**风险评估**:路径 A 是公开导出 API,但文档未收录,属"实现先行"状态,
需接受随 SDK 版本演进的维护成本;建议在 runtime 侧对 `fork_session` 的
导入与签名做启动期探测,缺失时降级为 session 级 fork。

### 3.3 DeepAgents / LangGraph — deepagents 层零辅助,langgraph 原语完整

**deepagents 0.5.9 全包无任何 fork/rewind/time-travel 辅助**(确认)。
`create_deep_agent` 返回的就是 langgraph `CompiledStateGraph`,pregel API 直接可用。
子代理(`task` 工具)不带 checkpointer,`checkpoint_ns` 全为空串——
复制 thread 时按 `thread_id` 过滤即可,无嵌套命名空间负担(实测存量库确认)。

**同 thread 分叉(官方原生,1.2.6 实测通过)**:

- `aget_state_history(config, filter=…)` 列出 checkpoint 历史
  (含 `checkpoint_id`、`parent_config`、metadata);
- 带 `{"configurable": {"thread_id", "checkpoint_id"}}` invoke **新输入**
  → 同 thread 内产生新分支,新链 `parent_checkpoint_id` 指向分叉点,原历史保留;
- `aupdate_state(旧checkpoint config, values, as_node=…)` 为"改状态后分叉"变体。

**message → checkpoint 映射(官方通道,实测通过)**:langgraph 的
`get_checkpoint_metadata` 会把调用 config 的 `metadata` 标量键自动写进该轮
每个 checkpoint 的 metadata。只需在 `DeepAgentsRuntime` 的 `stream_config`
里加 `"metadata": {"kernel_message_id": …}`,之后
`aget_state_history(cfg, filter={"kernel_message_id": …})` 直接命中。
langfuse overlay 已在用同一通道(`langfuse_session_id`),两者共存无冲突。

**跨 thread 复制(session 级 fork 的底座)——无 OSS 实现,手工可行且很小**:

- `BaseCheckpointSaver.copy_thread / acopy_thread` 接口在 checkpoint 4.1.1
  **已声明但默认 `NotImplementedError`**;sqlite saver 3.0.3 与我们的
  `FileCheckpointSaver` 均未实现(实测确认)。LangGraph Platform 的 thread copy
  是平台功能,OSS 没有。
- sqlite 后端手工复制 = 2 条 `INSERT … SELECT`(仅 `checkpoints` + `writes`
  两张表;`checkpoint_blobs` 是 Postgres saver 的表,sqlite 没有)。
  实测:复制后新 thread `aget_state` 完整、可独立续跑,原 thread 不受影响。
- file 后端 = 复制 `<root>/<thread_id>/` 目录。
- **建议实现成两个 saver 的 `acopy_thread`**——贴官方接口形状,上游将来补齐可无缝切换。
- message 级 fork 的"新 thread 截断复制"变体:沿 `parent_checkpoint_id`
  从分叉点回溯到根,只复制链上的 checkpoints + 对应 writes。

**Caveats**:

- 纯 completion 会话(不走 graph)**没有任何 checkpoint**,无法 checkpoint 级
  fork,只能从 kernel messages 重建输入;
- `run_task_coverage` 复用同一 thread 再跑一轮,会多产生 input checkpoint——
  存量会话按序对齐时须按 message 行对齐而非纯计数;新增数据用
  `kernel_message_id` metadata 键后无此问题;
- HITL resume(`Command(resume=…)`)不产生 input checkpoint,不干扰对齐;
- checkpoint 存储**不在 kernel.db**:是同目录独立文件
  `deepagents_checkpoints.db`(boot 时 setdefault),或云沙箱的
  `FileCheckpointSaver` 目录树;两后端互不相通。

---

## 4. 能力矩阵

| | Claude Agent | Codex | DeepAgents |
|---|---|---|---|
| Session 级 fork | ✅ 官方 `resume` + `fork_session` | ✅ `thread/fork` 不带锚点 | ⚠️ 无官方实现;自实现 `acopy_thread`(2 条 SQL / 目录拷贝) |
| Message 级 fork | ✅ `fork_session(up_to_message_id=…)`(官方实现,文档未收录) | ✅ `thread/fork(lastTurnId)`,实测可用 | ✅ checkpoint 分叉(同 thread 原生 / 跨 thread 截断复制) |
| 锚点 | transcript `uuid` | `turn.id`(UUIDv7) | `checkpoint_id`(uuid6) |
| 锚点已落库 | ❌ | ❌ | ❌ |
| 破坏性 | 无 | 无(0.144.4 复制式) | 无(分支共存) |
| 文件系统随 fork | 不复制(`rewind_files` 正交) | 不复制(rollback 亦明确不回滚文件) | 不涉及 |
| 主要风险 | message 级 API 未收录进文档 | `beforeTurnId` 静默降级;paginated 模式将改变删除语义 | 跨 thread 复制自维护;bare-completion 无 checkpoint |

---

## 5. 我们侧现状与缺口

### 5.1 已有的有利条件

- `messages.metadata` / `sessions.metadata` JSON 列现成,锚点与血缘落库
  **零 migration**;
- `CreateSessionRequest.id` 允许 host 预铸 session id,`metadata` 可在 create
  时传入——fork 血缘可随创建一次写入;
- `import_canonical_message`(`kernel/app/routes/messages.py`,
  `POST /v1/sessions/{id}/messages/import`)已确立 provenance 范式:
  `metadata["imported_from"] = {session_id, message_id}`,fork 沿用同形状的
  `forked_from` 即可。注意它只复制单条消息的**文本**,runtime 对该历史无感知——
  这正是 fork 与 import 的本质区别;
- kernel events 表存的是归并后的 canonical 行(五类 delta 不落库),
  按 `data.message_id` 分组即可截断复制。

### 5.2 缺口清单(实现 fork 前必须补)

1. **逐轮锚点落库**(最低成本,建议先行):每轮结束把原生 id 写进
   `messages.metadata`,建议形状:

   ```json
   {"runtime_native": {"provider": "codex", "thread_id": "…", "turn_id": "…"}}
   ```

   三个 runtime 的取值点都现成:codex 在 `turn_start` 响应
   (`kernel/src/runtimes/codex/runtime.py` 中 `turn_resp.turn.id`,目前过手即丢,
   `turn/completed` 通知可做校验兜底);claude 在流式消息的 `uuid` 字段
   (UserMessage 需 `replay-user-messages`);deepagents 经 `stream_config`
   metadata 键自动入 checkpoint,turn 末尾 `aget_state` 的
   `config.configurable.checkpoint_id` 另存一份到 message metadata。
   存量会话回填:codex 用 `thread/read(includeTurns=true)` 按序对齐;
   deepagents 按 input checkpoint step 对齐;claude 读 transcript jsonl 对齐。
2. **fork 出的新原生 thread id 的写入**:不需要给 `CreateSessionRequest` /
   `UpdateSessionRequest` 加 `runtime_session_id` 字段(早期调研结论有误,
   已修正)。fork 是 kernel 自己的路由操作,kernel 对自己的 store 直接写
   `runtime_session_id`——与今天 `thread_start` 后回填同路。做法:
   **`RuntimePort.fork_session` 一等方法**(与 start/resume 并列的第三种
   thread 出生方式,对应 codex 生命周期的 `thread/start | thread/resume |
   thread/fork` 三竖列),经 `orchestrator.fork_session`(标准 runtime
   工厂)驱动,在**任何 kernel 行落库之前**同步执行——非法锚点/源 rollout
   缺失当场报错(502)且零回滚成本;成功后 `runtime_session_id` 已回填、
   runtime 留温,历史复制后 session 行**最后落库作为提交点**。副作用:
   fork 调用包含一次 codex 子进程冷启动(约 1–2s),首次 Send 直接复用
   温 runtime。三个 runtime 都实现该方法(claude/deepagents 在 P2/P1
   落地前显式 `NotImplementedError` → 路由译为 422,落地后路由零改动)。
3. **deepagents 的 `thread_id == session.id` 硬绑定**
   (`kernel/src/runtimes/deepagents/runtime.py`:`runtime_session_id`
   为空时无条件回填 `session.id`)需解耦为"优先读已有值",
   否则 fork 出的新 session 拿不到旧 thread 的 checkpoint。
4. **fork 操作面**:kernel 无 fork 路由;`RuntimePort` 无 fork 方法;
   host `KernelClient` seam 无对应方法(已有 `import_message` 可参照);
   前端全仓无任何 fork 相关代码。
5. **kernel 消息历史回填**:fork 后 runtime 侧有完整上下文,但新 kernel
   session 的 messages/events 是空的——需从源 session 截断复制
   (host 侧 DataService durable 镜像同步)。codex 的 fork 响应携带截断后的
   `thread.turns`,可作复制结果的校验源。
6. **Claude 侧现有 `_fork_next_spawn` 与新特性的关系**:claude runtime 已在
   permission_mode 变更重建时使用尾部 `fork_session=True`(绕 CLI resume bug),
   fork 后新 id **覆盖回原 session 的** `runtime_session_id`、不建父子关系。
   与本特性语义不同,实现时注意隔离,勿复用其状态位。

### 5.3 血缘与命名(host 侧)

- 血缘落 `sessions.metadata`:kernel 层记
  `forked_from: {session_id, message_id}`(源 kernel id),host UX 字段照既有
  约定挂 `metadata["valuz"]`(命名沿用竞品:标题后缀 "(fork)" 或 "(2)")。
- codex 的 `thread/list` 不返回 `forkedFromId`,claude 的 `forkedFrom` 只在
  transcript 里——**kernel 落库的血缘是唯一可靠的展示来源**,不要依赖 runtime 查询。

---

## 6. 建议落地路线

**P0 — 通用地基 + Codex 端到端**(codex 是唯一"原生、无损、协议级"的
message fork,改动最小、语义最干净):

1. 三 runtime 锚点落库(§5.2-1)——先行合入,让新会话开始积累锚点;
2. `RuntimePort.fork_session` 一等方法(三 runtime 统一签名,codex 先实现)
   + `orchestrator.fork_session` + kernel fork 路由
   (`POST /v1/sessions/{id}/fork`,body 带可选 `message_id`)。
   顺序不变量:**原生 fork 先行 → 截断复制 messages/events →
   session 行最后落库(提交点)**,见 §6.5;无需改动 create/update 的
   wire schema(§5.2-2);
3. host seam(`KernelClient.fork_session`)+ REST + 前端入口
   (会话菜单 "Fork" = session 级;消息 hover "Fork from here" = message 级);
4. 版本护栏:只发 `lastTurnId`;错误按 code(`-32600`)判断。

**P1 — DeepAgents(已实现)**:`DeepAgentsRuntime.fork_session` →
`checkpoint_fork.fork_sqlite_thread`(直接对 sqlite store 做行复制:
tail = 整线程两条 `INSERT … SELECT`;anchor = 沿 `parent_checkpoint_id`
从锚点回溯的链集,新 thread 的 latest 即锚点)。三个原设计项的落地修正:

- **仅接 sqlite 后端**(维护者确认:本地与云端现在都跑 sqlite,
  `FileCheckpointSaver` 已弃用)——`_checkpoint_backend()` 返回 "file"
  时 `NotImplementedError` → 422,fail-loud 而不是复制一个没人读的 store;
- **thread_id 硬绑定无需解除**——fork 把 checkpoint 复制到
  `thread_id = 新 session.id`,现有 `thread_id == session.id` 绑定对
  fork 天然成立;
- **`kernel_message_id` metadata 键不再需要**——P0-1 已把每轮的
  `checkpoint_id` 落在 message 上,正向映射(checkpoint metadata 过滤)
  失去了用途。

**P2 — Claude Agent(已实现)**:`ClaudeAgentRuntime.fork_session` 调 SDK
离线变换 `claude_agent_sdk.fork_session(source_native_session_id,
directory=<session cwd>, up_to_message_id=anchor)`(经 `asyncio.to_thread`
下线程),同步拿到新 id 回填 `runtime_session_id`,首次 Send 走正常
resume;import 失败(SDK 不再导出)→ `NotImplementedError` → 422。

> **为什么不是 spawn 时切片(`--resume-session-at` / SDK PR #1198 的
> `resume_session_at`)**:本机探针证实 bare connect 之后 **init 消息
> (携带新 session id)不会到达,必须等首个 query(即真实模型调用)**——
> 因此 spawn 时切片无法满足 eager fork 契约(fork 调用内同步取得新
> native id)。#1198(已合并未发布,要求 CLI ≥2.1.223,现捆绑 2.1.220)
> 的两个选项是"同会话截断重续"原语,留给将来的 rewind 特性;对 fork
> 无迁移需求。SDK 升级时只需回归 `fork_session` 离线函数仍在导出面上。

**升级 codex 后重验清单**:`beforeTurnId` / `excludeTurns` 是否落地;
paginated 引用式 fork 对**源线程删除**的钉住语义(Valuz 的 session 删除路径
需处理新错误);`thread/list` 是否补上 `forkedFromId`。

---

## 6.5 端到端交互(client → host → kernel → runtime)

两条用户动作,一条完整链路。设计原则:**fork 之后的"继续对话"零新机制**——
fork 的全部工作在 fork 调用内完成,后续对话走既有路径。

### Fork(同步,1–2s,含 codex 冷启动)

```
Client                Host                        Kernel                     Runtime
──────                ────                        ──────                     ──────
会话菜单 "Fork"
或消息 hover
"Fork from here"
  │ POST /api/v1/sessions/{id}/fork {message_id?}
  ├────────────────►  api/routes/sessions.py
  │                   SessionService.fork_session
  │                   1. kernel_client.get_session → 源 DTO
  │                   2. 门控:origin ∈ {user, project_chat 类};
  │                      task lead/member、bare_completion 不开放
  │                   3. 组装完整 valuz metadata(host 全责,kernel 不隐式继承):
  │                      复制源 valuz dict → name 按 D1 规则重命名;
  │                      project_id / agent_slug / locked_provider_id /
  │                      extra_skill_ids / capability_manifest /
  │                      global_instructions / worktree 原样保留;
  │                      last_user_message_text = 锚点(或末条)用户文本
  │                   4. kernel_client.fork_session(...)   ← 新 seam 方法
  │                      (控制面操作:live-kernel-first,
  │                       严禁落到 durable 数据面客户端)
  │                        │ POST /kernel/v1/sessions/{id}/fork
  │                        ├──────────────►  fork 路由(已实现)
  │                        │                 校验(owner/status/锚点)
  │                        │                 → resolve_native_fork_source
  │                        │                   (per-provider 锚点键)
  │                        │                 → build_forked_session(内存,
  │                        │                   盖章 forked_from)
  │                        │                 ① orchestrator.fork_session
  │                        │                     └────────────►  RuntimePort.fork_session
  │                        │                                     (start|resume|fork 三分支
  │                        │                                      的第三竖列,一等方法)
  │                        │                                     codex: _ensure_codex →
  │                        │                                     thread/fork(lastTurnId)
  │                        │                                     → 回填 runtime_session_id,
  │                        │                                     runtime 留温
  │                        │                    失败 → 502(此时零落库,无需回滚;
  │                        │                    NotImplementedError → 422,P1/P2
  │                        │                    落地后路由零改动)
  │                        │                 ② copy_history(messages/events 重铸,
  │                        │                    store 双写,durable 镜像自动同步)
  │                        │                 ③ save_session ← 提交点(最后落库)
  │                        │                    ②③ 失败 → 级联清扫 + 驱逐 runtime
  │                        │◄─ SessionData(含新 thread id)
  │                   5. 发布 SESSION_CREATED(Recents/列表实时更新)
  │◄─ SessionDetail
导航到新会话;
历史即刻可见(已复制);
头部可显示 "forked from" 链接
(metadata.forked_from)
```

顺序不变量:**原生 fork 先行(①,零落库,失败零成本)→ 历史复制(②)→
session 行最后落库(③,提交点)**。由此 `fork_intent` 元数据被移除——
它此前唯一的存在理由是"先复制后 fork"顺序下的崩溃兜底;顺序反转后血缘
只剩 `forked_from`。`prepare` 回归其本职(桌面预热),与 fork 无关。

### 继续对话(fork 后首次 Send)——全部走既有路径

```
Client → POST /api/v1/sessions/{new_id}/messages(既有)
Host   → send_message → kernel_client.run_turn(既有)
Kernel → orchestrator.run_turn → _ensure_runtime:
         eager prepare 留下的 runtime 还在 warm cache → 直接复用;
         codex _prepare no-op(was_ready)→ _ensure_thread 走 resume 分支
         (runtime_session_id 已回填)→ 在 forked thread 上正常出词
自愈   → 若 warm cache 已驱逐:重建 runtime → resume 分支(id 已持久化);
         若进程在 fork 中途崩溃(落库后、原生 fork 前):intent 分支
         在下次 prepare/Send 重放同一 fork(幂等)
源会话 → 全程零写入,继续独立使用(legacy 复制式 rollout,互不影响)
```

### 前端可用性判定(零新增 API)

- **消息级入口**:`message.metadata.runtime_native?.provider === "codex"` 且
  `status === "completed"` 时启用——`MessageData` wire 已带 `metadata`,
  列表接口现成;锚点缺失(存量老消息)显示禁用态 + tooltip。
- **会话级入口**:`runtime_provider === "codex"` 且非 running;running 时
  会话级禁用(kernel 409 兜底),消息级仍可对历史已完成消息使用。
- 错误映射:409(锚点无效/运行中)→ toast;422(runtime 未支持)→
  入口本就不显示;502(原生 fork 失败,已回滚)→ toast "Fork 失败,请重试"。

### 决策点(已对齐,2026-08-12 敲定)

| # | 决策 | 结论 |
|---|------|------|
| D1 | 新会话命名 | **优先 codex App 式 `源名 (2)(3)` 递增**(同项目/同分组内查重);实现代价过高则退回 `源名 (fork)` |
| D2 | metadata 组装归属 | **host 组装完整 valuz dict**,kernel 不隐式继承源 metadata(分层干净,与 create_session 同构) |
| D3 | 来源门控 | P0 只开放普通会话(user/project chat);task、bare_completion、scheduled 触发的不开放 |
| D4 | worktree 会话 | 允许 fork,共享源 worktree(同 cwd);"fork 到新 worktree" 留 P2(codex fork 参数原生支持 cwd 覆盖) |
| D5 | fork 同步 vs 异步 | 同步(1–2s 可接受,按钮 spinner;错误当场可见) |
| D6 | "forked from" 回链 UI | P0.5:会话头部 chip,点击跳源会话(数据已在 metadata.forked_from) |
| D7 | http kernel 模式路由 | fork 走 live-kernel-first(需要 orchestrator + runtime);host 数据面客户端不实现该方法(NotImplemented 显式拒绝) |
| D8 | runtime fork 的调用形态 | **一等 `RuntimePort.fork_session`**(维护者裁定):三 runtime 统一签名、不因 codex 先行而妥协;fork 原生先行、复制后置、session 行为提交点;`fork_intent` 元数据废除;`prepare` 回归预热本职 |

### Host 侧改动清单(P0-3,已实现)

1. `api/openapi.yaml`:`POST /v1/sessions/{id}/fork`(operationId
   `forkSession`;注意前端类型是**手写镜像**——仓库的
   `make generate-types` 指向的 pnpm script 并不存在);
2. `KernelClient` 协议 + 两个 transport 加 `fork_session`;module facade
   走 `_kernel_for(scope)`(控制面 live-kernel-first,数据面永不服务 fork);
   契约测试 `EXPECTED_ROUTES` 已登记;
3. `SessionService.fork_session`:D3 门控(仅 origin=user,排除
   task/automation/bare_completion)、D2 完整 valuz metadata 组装
   (project_id/agent_slug/locked_provider_id 等原样保留,message fork 时
   `last_user_message_text` 取锚点用户文本)、D1 编号命名
   (`_numbered_fork_name`:剥 ` (N)` 后缀取 base,项目内查重递增,
   查重失败退化 `(2)`)、kernel 错误 → 模块错误
   (`ForkRejected` 409 / `ForkUnsupported` 422 / `ForkRuntimeFailed` 502)、
   `project_index.record` + `SESSION_CREATED`;
4. 前端(webui/desktop 共用 `@valuz/app`):`sessionsApi.fork`
   (`packages/core/src/api/sessions-api.ts`,120s 超时 + 列表缓存失效)、
   会话头部菜单 "Fork 会话"(codex 会话可见,running/in-flight 禁用)、
   消息 hover "从此处 Fork"(`ConversationBody.renderTurnActions`,
   锚点 message_id 取自 `turn.id` 的 `turn-` 前缀剥离)、
   `useTitleActions.handleFork`(成功 toast + 跳转 `/conversation/{id}`,
   409 → `conversation.forkConflict`,其余 → `conversation.forkFailed`)、
   i18n 五个 key(zh-CN 采用 "Fork" 外来词文案)。

### 遗留跟进项(P0 之后)——已收口

- **消息级锚点可用性 wire 信号(已实现)**:终结 `session_update` 事件
  携带 `fork_anchor`(锚点在 finalize 时才存在,起始事件带不了),SSE
  adapter 以 `"true"/"false"` 字符串透传,前端折叠为
  `ConversationTurn.forkAnchor`——`false` 禁用 "从此处 Fork";信号缺失
  (该字段之前录制的事件)= unknown,维持"显示 + 409 兜底"。
- **侧边栏 Recents 行菜单 + Activity/项目详情行菜单 Fork 入口(已实现)**:
  侧边栏按 `RunSummary`(runtime/origin/running)精确门控;Activity 的
  `ActivityItem` 不带 runtime/origin——现在三个 runtime 全部可 fork,
  运行时门控已无意义,仅按 status 排除 running,automation 来源行由
  host 422 → toast 兜底。
- **D6 "forked from" 回链 chip(已实现)**:`forked_from_session_id`
  透出到 `SessionListItem`/`SessionDetail`(openapi 同步),会话头部
  渲染可点击 Badge 跳转源会话(源已删除时由目标页自身的 not-found 处理)。
- **存量 codex 会话锚点回填 → won't-do(维护者确认)**:成本不成比例
  (需拉起 codex 子进程 + turn↔message 按序对齐,`run_task_coverage`
  双 turn / 失败轮 / `/compact` 轮都会破坏对齐,错标比缺失更糟);收益面窄
  且自愈(会话级 fork 对存量会话本来可用;老会话发一条新消息即获得锚点)。
  若将来确有需求,做成 `valuz` CLI 显式维护命令(对不齐即整会话跳过)。

---

## 7. 未决问题(设计阶段定夺)

1. **分叉后的历史模型**:kernel messages 是线性的。本方案选择
   "fork = 新 session + 截断复制"(双方都保持线性),不做同会话内的分支树 UI。
   同 thread 分支(langgraph 原生形态)显式不采用——kernel 历史与 runtime
   state 会失配。
2. **文件系统语义**:三家 fork 都不复制文件状态。同 cwd fork(对标 codex App
   "this workspace")两会话共享工作区,写冲突风险由用户承担;
   结合既有 worktree 设计(`docs/design/project-worktree-design.md`)可提供
   "fork 到新 worktree"(codex 侧 fork 参数原生支持 `cwd` 覆盖,claude /
   deepagents 侧即新 session 换 cwd)。P0 是否带 worktree 支路待定。
3. **fork 时的配置覆盖**:是否允许 fork 时换 model / agent(codex 协议原生
   支持 override;claude / deepagents 是新 session 天然可换)?建议 P0 不开放,
   保持"同配置分叉"的简单心智。
4. **运行中的会话**:codex 拒绝 fork 进行中的 turn(`-32600`);统一产品规则
   建议为"锚点必须是已完成的消息;会话运行中允许 fork 历史消息"。
5. **member / task 会话**:task 子运行的会话是否允许 fork?
   建议 P0 仅开放 `assistant` / `project_chat` 来源的会话。
