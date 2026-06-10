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
  │ (应用库)  │      │ (~/.valuz,     │      │ + 可选         │
  │           │      │  工作空间)      │      │ Reportify     │
  └───────────┘      └───────────────┘      └──────────────┘
```

同一套后端可以以两种运行形态发布：

- **桌面端** — Electron 外壳内嵌并托管 `valuz-server` 子进程，通过 `http://127.0.0.1` 通信。
- **Headless（无界面）** — `valuz-server` 独立运行，对网络暴露同一套 HTTP API，以 token 认证。
  WebUI / TUI 宿主连接到它。

Go 编写的控制 CLI（`valuz`）是运行时控制平面——负责启动、停止、诊断这些进程，
但不拥有它们的任何实现。

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
│  app/      路由挂载于 /api/v1/{projects,agents,sessions,…}         │
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

宿主与内核共享**同一个 SQLite 文件**，位于 `~/.valuz/app/valuz.db`。两层都完全运行在
`aiosqlite` 之上的**异步**模式。WAL 日志加上 `busy_timeout` 保证宿主/内核并发访问安全。

- 宿主全部 DB 访问经由 `infra/db.py`（`async_unit_of_work` / `get_async_session`）。
- 同步 DB 调用绝不可运行在事件循环上——宿主已从同步引擎迁出，以消除事件循环死锁。
- schema 在启动时创建并迁移：宿主迁移（Alembic + seed）与内核迁移（内核自有 Alembic）在 `boot/` 中运行。

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
它把一个就绪的 plan 节点派给一个 member（在自己子运行目录中的兄弟 `asyncio` 任务），
member 把 manifest 同步返回到 lead 的工具调用中，lead 再审阅它（approve 解锁后继；rework 下发反馈）。
任务子系统按层划分（Transport / Services / Runtime / Domain），以状态优先的 `LiveMemberRegistry`
为基石。

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
