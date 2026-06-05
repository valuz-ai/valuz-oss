# Valuz OSS

**一个工作台统管你所有的 Agent——让它们在真实项目里协同干活，跑在你自己的机器上。**

[English](README.md) · [产品概览](docs/product-overview.zh-CN.md) · [技术架构](docs/architecture.zh-CN.md)

---

Valuz OSS 是一个开源、**本地优先（local-first）的 Agent 工作站**。你组建一支智能体团队——
每个跑在你选择的 Runtime 与模型上——把它们放进真实项目里干活：规划、派单、把任务推到完成。
Agent loop 和你的全部数据都留在**你自己的机器上**；唯一的对外流量是你配置的 LLM 服务。

它行业中立，面向任意"项目化知识工作"——研究、写作、策划、产品设计、运维。
领域深度（第一站是投资研究）以可选的行业版叠加在上面。

## 亮点

- **本地优先、完全自控。** Agent loop、你的文件、你的数据都跑在你的机器（或你自己的局域网/服务器）上——
  没有任何"只能托管"的部分。
- **Runtime 与模型中立。** 不绑定任何厂商。每个智能体跑在你选的 Runtime 上——
  **Claude Agent**、**Codex Agent** 或 **Valuz Agent**——配上你自己的 API Key 或 Claude / Codex 订阅。
  凭证存于系统钥匙串。
- **Project-as-Agent-Team。** 项目是一支智能体团队的容器，而非某一个 Agent 的聊天窗。
  每个智能体都是一等的工作者，自带角色、记忆与装备（技能 + 连接器）。
- **目标驱动的多智能体 Task。** 一个 lead 智能体把工作规划成依赖图，把子任务派发给 member 智能体、
  审阅产出、把目标推到完成——工作以任务流转，而非消息。
- **可扩展。** 技能、私有知识库、连接器（MCP）、定时自动化。
- **Open Core。** 单租户工作站开源、免费。
- **可选行业版。** 连接 Reportify 解锁投研 Skill、数据工具、云端高级解析。

完整功能全景见 **[产品概览](docs/product-overview.zh-CN.md)**。

## 快速开始

```bash
# 工具链前置：uv、pnpm、asdf（.tool-versions 锁定 Go 1.26）
cd backend && uv sync && uv run alembic -c alembic/host/alembic.ini upgrade head
cd frontend && pnpm install && pnpm run generate-types
make dev          # 启动后端 + 前端开发外壳
make test-all     # 验证一切正常
```

`scripts/dev.sh` 是规范的开发启动器——它在一个前台进程组里同时启动 `:8000` 的后端与桌面开发外壳
（Ctrl+C 同时停止两者）：

```bash
./scripts/dev.sh                  # 后端 + 桌面（默认）
./scripts/dev.sh backend          # 仅后端
./scripts/dev.sh frontend         # 仅前端
VALUZ_BACKEND_PORT=18080 ./scripts/dev.sh
VALUZ_RELOAD=1 ./scripts/dev.sh   # uvicorn --reload
```

## 技术栈

| 层 | 技术 |
|----|------|
| 控制 CLI（`valuz`） | Go 1.26 + cobra |
| 前端 | TypeScript、React 19、Vite、Tailwind CSS、Zustand |
| 后端（`valuz-server`） | Python 3.12+、FastAPI、SQLAlchemy、Pydantic |
| Agent 运行时 | claude-agent-sdk、codex CLI、DeepAgents + LangChain |
| 应用数据库 | SQLite（aiosqlite、WAL） |
| API 契约 | OpenAPI 3.1（`api/openapi.yaml`） |
| 桌面外壳 | Electron |

完整技术设计见 **[技术架构](docs/architecture.zh-CN.md)**。

## 项目结构

```
├── api/              OpenAPI 契约（唯一事实来源）
├── backend/          Python/FastAPI 服务（打包为 valuz-server）
│   ├── kernel/       内嵌的 Agent harness 内核（只读）
│   └── valuz_agent/  宿主应用
├── cli/              Go 控制 CLI——面向用户的 `valuz` 二进制
├── frontend/         pnpm 工作空间
│   ├── apps/         webui · desktop · tui
│   └── packages/     shared · core · ui
├── docs/             产品概览 + 技术架构
├── i18n/             多语言文件（zh-CN、en-US）
└── scripts/          开发 + 构建工具
```

## 开发

```bash
make test-all         # 运行全部测试
make typecheck        # 前端 + 后端类型检查
make lint             # 前端 + 后端 lint
make check            # 以上全部
make help             # 显示所有可用命令
```

`valuz` 控制 CLI（`cli/build/valuz`，用 `cd cli && go build -o build/valuz .` 构建）
覆盖开发启动器之外的高级操作——状态、日志、诊断、自启动：

```bash
valuz status        # 端口 + PID + HTTP 探活
valuz doctor        # 环境 + 路径 + 后端健康
valuz logs backend  # tail 后端日志
```

## 打包

`scripts/build-desktop.sh` 产出 macOS 桌面包与 DMG：

```bash
bash scripts/build-desktop.sh                           # 完整构建，edition=oss
bash scripts/build-desktop.sh --signed --edition=oss    # Developer-ID 签名
bash scripts/build-desktop.sh --edition=enterprise      # 其他 edition
bash scripts/build-desktop.sh --skip-backend --skip-cli # 仅迭代 Electron
```

它按顺序运行三个阶段：**后端**（PyInstaller 打包 `valuz-server`）、
**CLI**（Go 构建 `valuz` 二进制）、**前端**（Vite + electron-builder 产出 `.app` 与 DMG，
命名为 `valuz-<edition>-<platform>-<arch>`）。

## 许可

Valuz OSS 采用 **Open Core** 模式：本仓库中的单租户工作站开源、免费。
SaaS 托管的公共资源、云端同步与团队能力在商业版；领域深度在行业版。许可条款见 `LICENSE`。
