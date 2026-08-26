# 技术架构

> Valuz OSS 的技术架构。本文档描述**系统是如何构建的**——进程、分层、数据存储与契约。
> 关于**产品能做什么**，见 [product-overview.zh-CN.md](product-overview.zh-CN.md)。

[English](architecture.md)

---

## 1. 系统拓扑

Valuz OSS 是本地优先（local-first）的应用。Agent loop 和全部用户数据都运行在用户自己的机器上；
唯一的对外流量是用户配置的 LLM 服务（以及可选的、用于投研数据的 Reportify 云端）。

```
┌──────────────────────────────────────────────────────────────────┐
│  客户端                                                            │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐           │
│  │ Electron      │   │ 浏览器 WebUI  │   │ 终端 UI       │  (宿主  │
│  │ 桌面端        │   │               │   │ (规划中)      │  外壳)  │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘           │
└─────────┼──────────────────┼──────────────────┼───────────────────┘
          │   HTTP / SSE      │                  │
          └──────────────────┬┴──────────────────┘
                             ▼
          ┌───────────────────────────────────────────┐
          │  后端 (valuz-server, FastAPI)              │
          │  宿主应用 + 智能内核                        │
          └───────────────────┬───────────────────────┘
                             │
        ┌────────────────────┼─────────────────────┐
        ▼                    ▼                      ▼
  ┌───────────┐      ┌───────────────┐      ┌──────────────┐
  │ SQLite    │      │ 本地文件系统    │      │ LLM 服务      │
  │ (应用库)  │      │ (~/.valuz-oss, │      │ + 可选         │
  │           │      │  工作空间)      │      │ Reportify     │
  └───────────┘      └───────────────┘      └──────────────┘
```

同一套后端可以以两种运行形态发布：

- **桌面端** — Electron 外壳内嵌并托管 `valuz-server` 子进程，通过 `http://127.0.0.1` 通信。
- **Headless（无界面）** — `valuz-server` 独立运行，对网络暴露同一套 HTTP API，以 token 认证。
  WebUI / TUI 宿主连接到它。

Go 编写的控制 CLI（`valuz`）是运行时控制平面——负责启动、停止、诊断这些进程，
但不拥有它们的任何实现。

### 桌面模型网络出口

打包桌面端可以在 Electron main process 中启用模型流量的 **Egress Manager**。它是桌面平台服务，既不属于宿主，也不属于内核：

```text
Codex / Claude ── 模型 base_url ──> loopback 薄模型入口 ─┐
                                                         ├─ Resolver
DeepAgents / Provider Test ─ 显式 transport ─> 正向出口 ─┤  + Connector
                                                         └─ env / 系统 PAC / DIRECT ─> 模型 Provider
```

两个 loopback 前端共享同一份不可变代理环境快照、Chromium `resolveProxy()` 结果和 DIRECT / HTTP CONNECT / SOCKS5 连接器。Codex、Claude 通过预注册的模型 `base_url` 接入，因此 Valuz 不靠新增进程级代理变量改道它们的工具 shell、MCP、插件、浏览器或整个 sidecar。DeepAgents 与 Provider Test 使用自己持有的显式 HTTP client，并只对该 client 关闭环境代理自动发现。

Electron 只通过受管理 backend 继承的 stdin 一次性交付桌面控制 envelope，其中包含随机、仅驻留内存的桌面控制 token 和当前 egress bootstrap。backend 只用该 token 鉴权 loopback 网络控制接口；renderer、模型 runtime、工具与 MCP 进程都无法获得它。runtime descriptor 仍为短期租约，使用中续租并在清理时撤销。所有监听器使用随机 loopback 端口，不安装本地 CA，也不做 HTTPS MITM。若初始化失败，UI 与 backend 仍可使用，但已经准入的模型流量保持阻断，直到用户选择“模型客户端自行管理”，避免静默裸直连。

连接管理方的切换由 Electron 按本地事务编排：先查询 backend 的全局 running-runs；存在运行任务时由设置页明确确认，确认后逐个中断受影响 session 并等待接口完成。随后切换本地前端，通过鉴权 loopback 接口替换 backend 的内存 egress registry，并重建受影响的模型 runtime。同版本正常路径不重启 backend；只有旧版或不健康的 backend 无法接受动态配置时才回退为重启。用户取消或任何任务未能安全中断时保持原模式，不进入半切换状态。

已有空闲会话具有显式 runtime 准备路径：打开会话即可在后台初始化 Codex app-server 与 thread，但不会发送用户内容或模型请求；真正发送消息会进入同一把 session 创建锁并复用已准备的 runtime。切换连接管理方时会撤销旧 descriptor，并最多预热最近使用的一个 Codex 会话。Claude 与 DeepAgents 实现相同的安全契约，但本阶段不会主动创建远端会话。

设置页监控贯通本地初始化与真实网络请求：先展示经过字段白名单限制的 runtime/thread/dispatch 阶段，真实连接出现后再用线路、健康状态和分阶段耗时替换初始化占位。终止阶段会立即移除活动项，即使 runtime 仍留在有限的预热缓存中，也不会把一个任务展示成两条连接，或把已完成任务继续显示为活动连接。

桌面端无需启动参数即可使用这项能力；新安装默认选择“模型客户端自行管理”，用户可在设置页主动切换为“Valuz 统一管理”。`VALUZ_EGRESS_FRONTENDS=0` 只保留为开发期紧急禁用开关。独立/headless backend 收不到 Electron capability，继续沿用显式代理环境变量或直连的既有行为。权威行为、准入矩阵与上线标准见 [`docs/design/unified-network-egress.md`](design/unified-network-egress.md)。

---

## 2. 后端：宿主 + 内核

后端分为**宿主应用**（`valuz_agent`）与 **Agent 内核**（`kernel/`）。
两者之间的全部耦合都经过唯一的适配器接缝（adapter seam）。

```
┌──────────────────────────────────────────────────────────────────┐
│  宿主  (backend/valuz_agent)                                       │
│                                                                    │
│  api/routes/   每个模块一个 HTTP 路由                              │
│  modules/      业务模块（扁平布局）                                │
│  integrations/ 端口实现（auth、mcp、parser、docs…）                │
│  ports/        横切协议                                            │
│  infra/        config、db、logging、secret store、fs_registry      │
│  boot/         进程生命周期（schema + 内核引导）                    │
│                                                                    │
│        ▲   全部内核耦合都跨越此接缝   ▲                            │
│        │                                                           │
│  adapters/                                                         │
│   ├── kernel_sync          异步 StorePort 之上的同步门面           │
│   ├── capability_resolver  工作空间 + 附加项 → 内核 skills/MCP     │
│   ├── model_resolver       请求 + provider + 默认值 → model id     │
│   ├── mcp_resolver         slug + 凭证 → MCP server 配置           │
│   ├── event_sse_adapter    内核 events 表 → SSE 帧                 │
│   └── system_prompt_builder 工作空间上下文 → agent 提示词          │
└───────────────────────────────────┬────────────────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────┐
│  Agent Harness 内核  (backend/kernel)                              │
│                                                                    │
│  app/      路由挂载于 /kernel/v1/{projects,agents,sessions,…}      │
│            StorePort + SessionOrchestrator 单例                    │
│  src/core/      Project、AgentConfig、Session、Event、McpServer…   │
│  src/adapters/  SQLAlchemyStore（异步）                            │
│  src/runtimes/  ClaudeAgentRuntime、DeepAgentsRuntime、Codex、     │
│                 skills 物化                                        │
│                                                                    │
│  表（无前缀）：projects · agents · sessions · events               │
└──────────────────────────────────────────────────────────────────┘
```

**内核**拥有 `Project ↔ Agent ↔ Session ↔ Event` 的持久化模型与运行时编排。

**宿主**拥有其余一切——智能体库、项目成员、任务编排器、模型通道、MCP 目录、定时任务、附件、
OAuth 页面，以及对外的 HTTP 接口。宿主自有的表以 `valuz_*` 为前缀。

**适配器**是两层唯一相遇之处。例如：`kernel_sync` 用同步门面包裹内核的异步 store；
`model_resolver` 把请求加上已配置的 provider 解析为具体的 model id；
`event_sse_adapter` 把内核 `events` 表投影为发给客户端的 Server-Sent-Events 帧。

### 运行时（Runtimes）

内核在 session 创建时按所选运行时分派，每个 session 单独选择：

| 运行时 | 底层 SDK | 默认协议 |
|--------|----------|----------|
| Claude Agent | `claude-agent-sdk` | Anthropic |
| Codex Agent | `codex` CLI | OpenAI |
| Valuz Agent | DeepAgents + LangChain | OpenAI / Anthropic |

`(runtime, provider, model)` 三元组在 session 创建后即锁定，`model` 不可中途切换。

---

## 3. 数据层

宿主与内核使用 `~/.valuz-oss/` 下的**两个独立 SQLite 文件**：宿主的 `valuz.db`
（`valuz_*` 业务表）与内核自有的 `kernel.db`（`sessions` / `messages` / `events`
以及内核 `alembic_version`；DeepAgents runtime 的 langgraph checkpoint 存放在同目录
的独立文件 `deepagents_checkpoints.db`——云沙箱下则是文件式 checkpoint 目录树——
而非 `kernel.db`）。这一拆分让沙箱/远程内核独占
自己的文件，并让进程内（`make dev`）与沙箱（`make dev-sandbox`）内核共享同一份 session
历史；若显式设置 `database_url`（如共享 Postgres）则两层仍共置于同一存储。两层都完全运行
在 `aiosqlite` 之上的**异步**模式，WAL 日志加上 `busy_timeout` 保证并发访问安全。

- 宿主全部 DB 访问经由 `infra/db.py`（`async_unit_of_work` / `get_async_session`）；宿主
  绝不在自己的引擎上查询内核表——内核状态一律经 `KernelClient` seam 访问。
- 同步 DB 调用绝不可运行在事件循环上——宿主已从同步引擎迁出，以消除事件循环死锁。
- schema 在启动时创建并迁移：宿主迁移（Alembic + seed）与内核迁移（内核自有 Alembic）在
  `boot/` 中运行。一个一次性启动步骤（`boot/kernel_db_colocate.py`）把内核的 `kernel.db`
  播种进 DataService 的 durable（`valuz.db`）（备份 → 拷贝 → 校验），让在 DataService 成为
  默认读层之前建立的安装仍能看到历史。（早期方向相反的 `kernel_db_split.py`——把内核表移出
  `valuz.db`——已退役，与同库共置相冲突。）

---

## 4. 领域模型

内核拥有持久化原语（`projects`、`agents`、`sessions`、`events`）；宿主在其上叠加编排层。
四个实体承载产品词汇，而适配器接缝正是把"存储的定义"变成"运行中的内核 session"的地方。

### 智能体（Agent）

**智能体**是一等、可复用的工作者——内核 `AgentConfig`（`agents` 表），由宿主 `agents` 模块
（"智能体库"）维护。它由四组构成，每组在 session 创建时通过接缝解析进具体 session：

- **身份** — 名字、说明、头像（宿主侧元数据）。
- **工作方法** — system prompt，由 `system_prompt_builder` 从智能体的 instructions 加工作空间上下文组装。
- **大脑** — runtime + model，由 `model_resolver` 从智能体声明的 runtime/provider 与请求解析。
- **装备** — 技能与连接器，由 `capability_resolver` 与 `mcp_resolver` 解析为内核的 skill 集与 `McpServerConfig` 列表。

没有"模板/实例"双层：智能体*本身*就是存储对象，复制它产生一个新的。

### 项目（Agent 团队）

**项目**是内核 `Project`（内核管理的一个 `cwd`）。宿主在其上叠加**成员**——派驻进项目的智能体。
派驻是**实时引用（live reference）**，不是复制：成员指向库里的智能体，所以编辑该智能体会更新派驻它的每个项目。
因此项目承载一支智能体*团队*；每个成员的能力在 session 创建时通过同一组适配器解析。

### 会话与运行（Session & Run）

- **Session（会话）** — 内核的执行单元与系统记录源。每个 session 是内核 `sessions` 表的一行。
  宿主特有字段挂载在 `sessions.metadata["valuz"]` 之下；宿主不另建平行的 session 表。
- **Run（运行）** — session 在活动总览中的一个*视图*，按来源（`assistant`、`project_chat`、`task`）分类。
  并非存储实体。

### 任务（Task）

**任务**是一种 lead/member 编排。持久的 `valuz_task` 头部拥有结构化的 **plan DAG**；
`valuz_task_session` 索引它所拥有的内核 session——恰好一个 **lead** session 加 N 个 **member** 子运行。
lead 驱动一个 `plan → dispatch(按 key) → review(approve|rework) → finish` 循环：
dispatch 是**非阻塞**的——member 作为兄弟 `asyncio` actor 运行在任务共享的 cwd 中，
经进程内邮箱（`member_done`）回报，lead 用 `await_members` 收集结果后再审阅
（approve 解锁后继；rework 下发反馈）。子系统按层划分（Transport / Services /
Runtime / Domain）：所有 actor 经由唯一的启动原语（`tasks/launcher.py`）拉起，
所有 plan 写入经由唯一的授权入口（`tasks/plan_commands.py`，MCP 工具与 REST 共用），
以状态优先的 `LiveMemberRegistry` 为协调基石。

---

## 5. 文件系统写入

宿主自有的全部写入都流经 `valuz_agent.infra.fs_registry.FsRegistry`。
在 `infra/config.py` 与注册表自身之外，禁止直接使用 `Path.home()` 或硬编码的 `~/.claude/...`。
内核在每个 `project.cwd` 之下管理自己的子树；注册表通过 `project_cwd(...)` 把该 cwd 交给内核，
内核从那里接管。

密钥（API Key、OAuth token）通过 secret store 存于操作系统钥匙串，绝不以明文落盘。

---

## 6. 前端

前端是 pnpm + Turbo 的工作空间，包之间分层严格。应用可依赖任意包；包只能向下依赖；
应用之间互不依赖。

```
frontend/
├── apps/
│   ├── webui/      第一个完全可运行的浏览器宿主
│   ├── desktop/    Electron 宿主（renderer + main + preload）
│   └── tui/        终端 UI 宿主（规划中）
└── packages/
    ├── shared/     最底层类型、常量、纯工具（无内部依赖）
    ├── core/       传输、stores、hooks、特性开关（依赖 shared）
    └── ui/         设计 token、布局外壳、基础组件（依赖 shared）
```

桌面端与 WebUI 通过 `@valuz/app` 共享应用级默认配置，同时每个宿主对路由、平台 provider、
布局组合保留最终所有权（route override / extra route / layout slot 模式）。
状态用 Zustand 管理；样式采用 Tailwind CSS + shadcn/ui。

---

## 7. API 契约

`api/openapi.yaml` 是每个 HTTP 边界的唯一事实来源。流程为契约优先：
先改契约，再改后端，最后改前端。前端 API 类型从契约生成（`make generate-types`），
绝不手写。后端请求/响应 schema 是 Pydantic 模型，对同一契约做校验。

实时更新（事件、决策收件箱、实时 TODO）通过 SSE 推送，由 `event_sse_adapter` 从内核 events 表投影。

---

## 8. 分发

最终组件采用一致的命名：

| 组件 | 产物 |
|------|------|
| 控制 CLI | `valuz`（Go） |
| 后端服务 | `valuz-server`（Python，PyInstaller 打包） |
| WebUI | `valuz-webui` |
| 终端 UI | `valuz-tui` |

桌面包将可执行文件按 `bin/libexec` 切分放置；版本（edition）是构建期 overlay
（`oss`、`enterprise`、`<vertical>`），折叠进打包后的组件，产出名为
`valuz-<edition>-<platform>-<arch>` 的产物。Go 控制 CLI 是运行时控制平面，
不拥有 server、WebUI 或桌面的实现。

---

## 9. 技术栈

| 层 | 技术 |
|----|------|
| 控制 CLI | Go 1.26 + cobra |
| 前端 | TypeScript、React 19、Vite、Tailwind CSS、Zustand |
| 后端 | Python 3.12+、FastAPI、SQLAlchemy、Pydantic |
| Agent 运行时 | claude-agent-sdk、codex CLI、DeepAgents + LangChain |
| 应用数据库 | SQLite（aiosqlite、WAL） |
| API 契约 | OpenAPI 3.1 |
| 桌面外壳 | Electron |

---

## 10. 架构原则

- **契约优先** — `api/openapi.yaml` 先行，实现随后。
- **唯一适配接缝** — 全部宿主 ↔ 内核耦合都跨越 `adapters/`。
- **唯一异步 DB 入口** — 宿主全部 DB 访问经 `infra/db.py`；绝不在事件循环上运行同步 DB 调用。
- **唯一写入注册表** — 宿主全部文件系统写入都经 `FsRegistry`。
- **本地优先** — Agent loop 与用户数据始终留在用户机器上。
