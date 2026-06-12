# Kernel 沙箱化部署 — 供给面与 Environment 设计（S 线 / C 线执行计划）

> **Status**: planned · **Date**: 2026-06-11 · **Owner**: backend
> **前置**: PR [#85](https://github.com/valuz-ai/valuz-oss/pull/85)（已合并）+
> [#86](https://github.com/valuz-ai/valuz-oss/pull/86)（P0.3，工具面 MCP 统一）
>
> 本文是 P0（kernel 缝封闭）之后的完整下一步：把 kernel 从「host 同进程的一个
> 模块」变成「可独立部署的执行单元」——先本地沙箱（场景一），再云端托管
> （场景二）。包含架构设计、抽象接口、分阶段功能清单、验收标准与决策点。

---

## 1. 背景：P0 之后的基线

P0 批次完成后，host↔kernel 之间只剩**三条网络可迁移的通道**：

| 通道 | 机制 | 状态 |
|------|------|------|
| 操作 | `KernelClient` 协议（`adapters/kernel_client.py`），双 transport：进程内（默认）/ `HttpKernelClient`（`VALUZ_KERNEL_MODE=http`） | ✅ 已落地，契约测试钉死方法↔端点 1:1 |
| 事件 | kernel 事件订阅 API（`after_seq` 增量 + 会话级/全局 SSE 流 + 进程内 bus tap） | ✅ 已落地，三个绕缝读者全部迁移 |
| 工具 | host toolkit MCP server（`integrations/toolkit_mcp_server.py`，`/internal/mcp/toolkit/{base,lead}/mcp`），会话经 `mcp_servers` 的 `harness` 条目引用 | ✅ 已落地，三 runtime 统一走 MCP 客户端路径 |

已就位的部署杠杆：

- `VALUZ_KERNEL_DATABASE_URL` — kernel 独立数据库文件（存储分家）；
- `KERNEL_AUTH_TOKEN` — 独立 kernel 进程的 bearer 鉴权（HTTP 中间件 + WS 4401）；
- 两个架构级探针：DB 分离探针（`tests/boot/test_kernel_db_separation.py`）、
  **裸子进程冒烟**（`tests/adapters/test_http_kernel_client_subprocess.py`——kernel
  以独立 uvicorn 进程 + 私有 DB + token 运行，全 REST 往返 + SSE 实时投递）。

换言之：**kernel 独立部署的最小形态（裸子进程）已经事实发生**，且被 CI 钉住。
本文规划的是把它产品化：沙箱隔离、Environment 资源化、以及云端托管的路径。

边界由 `backend/scripts/check_module_boundaries.py` 机械执法——后续所有工作
**不得**绕过这三条通道新增耦合。

---

## 2. 解决的问题

### 2.1 两个目标场景

| | 场景一 · 本地沙箱隔离 kernel | 场景二 · 云端托管 kernel |
|---|---|---|
| 动机 | agent 执行（含 LLM 生成代码、任意 bash）与用户主机之间缺乏隔离边界；误操作/提示注入可触达全盘文件与网络 | 桌面算力/在线时长受限；多人/多设备共享一套长跑的 agent 团队；商业版托管服务形态 |
| 部署形 | kernel 进程跑在本机的沙箱里（进程策略 / 轻量 VM / 容器），host（UI + 业务层）原位不动 | kernel 跑在云端沙箱（容器/microVM），host 仍在用户侧（桌面或 LAN headless） |
| 网络 | localhost / host gateway，无 NAT 问题 | host 在 NAT 后，云端无法入站直连 |
| 边界 | 单租户，信任域 = 本机 | 多租户，凭证不得出域持久化 |

**核心论断（指导全部设计）：场景一是场景二的真子集。** 两场景共享同一套协
议、同一个 kernel 镜像/产物、同一个 `KernelClient`；区别仅在三个面选「简化
实现」还是「完整实现」（见 §3.2）。先做场景一不是绕路——它用零网络复杂度把
最贵的缝全部还清，且每个简化实现都是产品上更好的选择而非妥协。

### 2.2 P0 后仍未解决的具体问题

1. **没有「沙箱」概念**：不存在供给（provision）一个跑着 kernel 的端点的抽象；
   裸子进程探针里的 spawn 逻辑散落在测试 fixture 中。
2. **「在哪跑」不是产品能力**：会话/项目无法声明自己的执行环境；沙箱选型只能
   是部署期硬编码。
3. **kernel 不自迁移**：alembic 链由 host boot 代跑（`boot/kernel.py`）；独立
   进程要靠供给方先迁移（探针目前如此），无自举能力。
4. **凭证仍以明文进会话行**：provider API key 序列化进 `agent_config` 落
   kernel DB——本地同机可容忍，云端不可接受。
5. **执行无任何隔离**：即使解决 1-4，本地 kernel 子进程默认仍是全权进程。
6. **云端三件套缺失**：NAT 后的工具回调 transport、skills/附件等「引力文件」
   的跨界分发、多租户 owner 贯穿。

---

## 3. 架构设计

### 3.1 六个面的全景（P0 后状态）

host 与（沙箱内）kernel 之间的全部交互归纳为六个抽象面：

```
 用户机器 (host)                                沙箱 (kernel)
┌─────────────────────────┐                 ┌─────────────────────────┐
│ UI · valuz_* 业务表       │ ──①供给──────▶  │ valuz-server /api/v1     │
│ SandboxProvider 驱动      │ ──②控制──────▶  │ sessions/messages/events │
│ host-MCP 工具面           │ ◀─③事件────────│ runtimes (claude/codex/  │
│ FsRegistry (host 域)      │ ◀─④工具回调────│            deepagents)   │
│ 钥匙串 · 凭证             │ ◀─⑤物料──────▶ │ 项目 cwd · skills 物料    │
│                          │ ──⑥凭证──────▶  │     │                    │
└─────────────────────────┘                 └─────│────────────────────┘
                                                  ▼ 直连（凭证已注入）
                                              LLM 提供商
```

| 面 | 内容 | P0 后状态 | 本计划动作 |
|----|------|-----------|-----------|
| ① 供给 | provision/stop/resume/destroy 一个跑着 kernel 的端点 | ❌ 不存在 | **新建**：`SandboxProvider` 协议 + 驱动矩阵 + Environment 资源（§3.3/§3.4） |
| ② 控制 | KernelClient 操作 | ✅ 双 transport 就位 | 复用；驱动产出的 endpoint+token 喂给 `HttpKernelClient` |
| ③ 事件 | 增量读取 + 订阅 | ✅ API 就位 | 复用 |
| ④ 工具回调 | kernel→host 的 MCP 调用 | ✅ 协议统一（harness MCP）；transport 仅 localhost | **transport 可插拔**：直连（本地/LAN）→ 队列/隧道（云端，§3.6） |
| ⑤ 物料 | 项目 cwd、skills、附件、KB | ⚠️ 同机路径假设 | 本地：bind mount / 同 FS（平凡）；云端：bundle + 文件 API（§3.7） |
| ⑥ 凭证 | provider key / OAuth 进入执行环境的方式 | ⚠️ 明文进会话行 | L1 引用化+注入（本地终态）→ L2 短效令牌 → L3 egress 注入（§3.8） |

### 3.2 两场景 = 同一协议的两形态

| 面 | 场景一（本地）实现 | 场景二（云端）实现 |
|----|--------------------|--------------------|
| ① | process+Seatbelt / apple-container / docker 驱动 | OpenSandbox / E2B 兼容驱动 |
| ④ | **直连** `host:port`（host gateway / 127.0.0.1） | **长轮询队列**（优先评估）或 WS 反向隧道 |
| ⑤ | **bind mount / 同 FS**（保住「拖文件夹进项目」语义） | bundle 上传 + kernel 文件 API（「沙箱即项目目录，host 是查看器」） |
| ⑥ | **env 注入**（同机不出域，即为终态） | 短效令牌（L2）→ egress 注入（L3） |
| 多租户 | 不适用（单租户） | owner 贯穿 + 「沙箱即租户边界」 |

②③ 两面在两个场景下**完全相同**——这是 P0 的成果，也是「真子集」论断的根基。

### 3.3 SandboxProvider：把抽象放在「端点供给」层

**关键选型立场：抽象在「kernel 端点供给」层，而不是「沙箱原语」层。**
Provider 只回答一个问题——「给我一个跑着 valuz-server 的端点」。exec、文件
读写、端口转发等沙箱原语**不进协议**：文件与进程操作一律走 kernel 自己的
API（②⑤），这样任何厂商 SDK 的原语（E2B 的 filesystem API、docker exec…）
都不会渗进业务层，换供应商只换驱动。

```python
# backend/valuz_agent/ports/sandbox_provider.py（草案）

@dataclass(frozen=True)
class SandboxSpec:
    """供给一个 kernel 端点所需的全部声明。"""
    environment_id: str            # 所属 Environment（§3.4）
    image: str                     # kernel 镜像/产物引用（进程驱动忽略）
    env: dict[str, str]            # 注入的环境变量（⑥ L1 的落点）
    mounts: tuple[MountSpec, ...]  # ⑤ 本地模式：项目 cwd 等挂载声明
    network_policy: NetworkPolicySpec | None  # §3.9，驱动尽力而为
    resources: ResourceLimits | None          # cpu/mem/disk，驱动尽力而为

@dataclass(frozen=True)
class SandboxEndpoint:
    sandbox_id: str
    base_url: str                  # HttpKernelClient 直接可用
    token: str                     # KERNEL_AUTH_TOKEN
    state: Literal["running", "stopped", "failed"]

class SandboxProvider(Protocol):
    """端点供给协议 — 每个驱动一个实现。"""
    async def provision(self, spec: SandboxSpec) -> SandboxEndpoint: ...
    async def stop(self, sandbox_id: str) -> None: ...
    async def resume(self, sandbox_id: str) -> SandboxEndpoint: ...
    async def destroy(self, sandbox_id: str) -> None: ...
    async def health(self, sandbox_id: str) -> SandboxEndpoint: ...
    async def logs(self, sandbox_id: str, *, tail: int = 200) -> str: ...
```

约定：

- `provision` 负责**自举**：镜像内（或进程驱动的 spawn 钩子里）完成 kernel
  alembic 自迁移（解决 §2.2-3），等待 `/health` 就绪后才返回；
- 返回的 `(base_url, token)` 直接构造 `HttpKernelClient`——供给面与控制面在
  此交棒，再无其他接口；
- Provider 实现放 `integrations/sandbox_*.py`，协议放 `ports/`（与
  `parser_backend`、`docs_runtime` 等既有 port 同构；开源版绑默认实现，
  商业 overlay 可换绑）。

### 3.4 Environment：一等公民资源（CMA 启示）

借鉴 Claude Managed Agents 的四概念模型（Agent / **Environment** / Session /
Events）：**「在哪跑」应当是和 Agent 平级的 API 资源**，而不是部署期配置。

- **契约**（contract-first，先改 `api/openapi.yaml`）：

  ```yaml
  Environment:
    id: string
    name: string
    type: enum [local-process, docker, apple-container, opensandbox, e2b]
    config: object        # 驱动特定配置（镜像、资源上限、网络策略…）
    status: enum [ready, unavailable]
  ```

- **存储**：`valuz_environment` 表（host 域，`valuz_*` 前缀惯例）；
- **引用**：项目（推荐粒度，见 §3.5）声明 `environment_id`；会话创建时由
  供给面解析为已 provision 的端点；缺省 environment = `local-process`
  （进程内模式则连 provider 都不经过——`VALUZ_KERNEL_MODE=inprocess` 保持
  现状为默认，零回归）；
- **UI 落点**（后续）：设置页新增 Environments 区块；项目设置可选执行环境。
  本计划只交付契约 + 表 + 解析链，UI 可独立排期。

### 3.5 沙箱粒度与生命周期

**每项目一沙箱**（而非每会话）：

- 与「沙箱即项目目录」自洽——项目 cwd 就是沙箱内的持久卷/目录；
- task 的 lead/member 会话天然共享同一 cwd（现有 run_dir 语义原样保留）；
- 多租户时「沙箱即租户边界」（§3.10）让 kernel 进程内部免于行级隔离。

生命周期：lazy provision（项目首个会话创建时）→ 空闲超时 stop →
下次会话 resume → 项目删除时 destroy。kernel 的 SQLite 与 cwd 必须落在
持久存储上（进程驱动：本机目录；容器驱动：named volume；云端：供应商
持久卷/快照）。

### 3.6 驱动矩阵与 ④ transport 选型

**驱动矩阵**（同一协议、三个梯度）：

| 梯度 | 驱动 | 隔离 | 适用 | 备注 |
|------|------|------|------|------|
| 一（本地默认） | `local-process` + **Seatbelt/bubblewrap** | 进程策略 | 桌面 OSS 默认 | 零虚拟化、macOS 零安装；跑与今天相同的 PyInstaller 产物；子进程继承 profile，runtime 派生的 claude/codex CLI 与任意 bash 全在围栏内 |
| 一（本地强隔离） | `apple-container`（macOS 26+/AS，`container machine` 持久 VM）；`docker`（跨平台） | 轻量 VM / 容器 | 可选后端 | docker 驱动同时是云端镜像的本地预演 |
| 二（自托管） | `opensandbox` 驱动 | 容器→K8s RuntimeClass(Kata/gVisor) | LAN/服务器、企业 | Apache-2.0、协议先行，一个驱动拿到 Docker+K8s 双后端；驱动字段参照其 lifecycle spec 设计 |
| 三（云端强隔离） | `e2b-compatible` 驱动 | Firecracker / KVM microVM | 托管服务 | 一个驱动覆盖 E2B 与 CubeSandbox（后者 E2B SDK drop-in，<60ms 冷启动、单机数千实例） |

**④ 工具回调 transport**（kernel→host 的 harness MCP 调用）：

P0.3 把工具面统一为 MCP 后，「跨网络」降级为纯 transport 选择题。三档：

1. **直连**（本地/LAN）：沙箱内 kernel 直接 HTTP 到 host 的
   `/internal/mcp/toolkit/...`。进程驱动 = 127.0.0.1；容器驱动 =
   host gateway（`host.docker.internal` 等）。S 线只做这一档。
2. **长轮询工作队列**（云端，**优先评估**）：host worker 以纯出站 HTTPS
   长轮询 claim 工具调用、执行、回贴结果——CMA self-hosted sandboxes 的
   生产验证模式。优点：失败模型简单（无状态、天然可重试、租约重派
   `reclaim_older_than_ms`）、分钟级 dispatch 长调用靠
   claim/heartbeat/post-result 三段式天然支持、host 离线时调用**排队而非
   失败**。代价：每调用 <1s 轮询延迟（对比 LLM 延迟可忽略）。
3. **WS 反向隧道**（云端备选）：host 拨出持久 WS，MCP JSON-RPC 装帧复用。
   延迟最低，但要自管连接生命周期/心跳/长调用应用层进度。

无论 2/3，kernel 侧落点都是 **loopback stub**：runtime（codex CLI、claude
SDK）只认 URL，教不会自定义 transport——隧道/队列在沙箱内重新发布为
127.0.0.1 的标准 streamable HTTP 端点，两端 MCP 语义零改动。会话身份
（`X-Valuz-Session-Id` → host 侧重建 ExecContext）P0.3 已经做进接口形态，
C 线只需在 host 执行前加真鉴权（本地阶段 token 校验从宽）。

### 3.7 ⑤ 物料面：三种引力分别处理

| 物料 | 本地（S 线） | 云端（C 线） |
|------|--------------|--------------|
| 项目 cwd | 同 FS / bind mount——「拖文件夹进项目」语义零变化 | 「沙箱即项目目录，host 是查看器」：kernel 文件 API（list/read/write/stat）+ 前端文件树走控制面 + 产物按需取回；双向同步（mutagen）仅作后续可选 |
| skills | 同 FS（现有 symlink materialize 原样工作） | slug + tar bundle 上传 API，kernel 收包解到沙箱内 materialize（capability_resolver 已有 slug→path 映射可挂钩） |
| 附件 / KB | 同 FS | 附件按引用传递（session metadata 带引用，沙箱侧 stage——CMA 模式）；KB 文档本体不动，检索本来就是 docs MCP 工具，走 ④ |

新抽象：`FsRegistry` 的 project 域方法（`project_cwd` / `task_path` /
`subrun_dir` / memory 目录）经 **`WorkspaceHandle`** 接口过一遍——本地实现
返回 Path（平凡），远程实现经文件 API 操作。S 线只落接口形态 + 本地实现。

另采纳 CMA 的交付物契约：session cwd 内钉 `outputs/` 目录约定（agent 最终
交付物写这里，host 按此取回展示到上下文面板）。

### 3.8 ⑥ 凭证面：三级机制，区别在「key 躺在哪」

| 级 | 机制 | key 位置 | 阶段 |
|----|------|----------|------|
| L1 | **引用化 + 注入**：`agent_config` 存 `env:ANTHROPIC_API_KEY` 形态引用，Provider 在 provision 时解引用注入 `SandboxSpec.env` | 沙箱 env 内 | S 线落地；**本地即终态**（同机不出域）。接口形态一次定型，L2/L3 只换解引用方式 |
| L2 | **短效令牌**：OAuth 订阅渠道（Claude/Codex）refresh token 永留 host 钥匙串，host 刷新后只把数小时有效的 access token 注入；过期经 ④ 通道续签。API key 渠道用 broker 虚拟 key 仿真（限额/限模型/可回收） | 沙箱内仅短效凭证 | C 线；依赖 ④ 就位（续签通道）。CMA 的 environment-key/API-key 双钥匙分离是实战印证 |
| L3 | **egress 网关注入**：沙箱出站请求不带凭证，出口网关（TPROXY/sidecar）按沙箱身份查表补 `Authorization`，附带域名白名单 + 出网审计 | key 不进沙箱 | 多租户规模化阶段；CubeEgress 已是现成实现，代价是网关 TLS 终止的 CA 物料管理 |

附带要求：codex 订阅渠道现状依赖进程环境兜底（`ch-codex-subscription` 行
`credential_source=none`）——L1 引用化必须把这个隐式依赖显式化，否则沙箱
环境里 codex 必挂（已有生产事故先例：dev 栈换启动方式后 codex 全部
"Missing environment variable: OPENAI_API_KEY"）。

### 3.9 NetworkPolicy：统一模型，三种实现

调研中四个同构物（srt 的域名代理、OpenSandbox egress sidecar、CubeSandbox
CubeEgress、我们 ⑥ 的需求）收敛为一个一等模型：

```
NetworkPolicySpec:
  allowed_domains: [api.anthropic.com, api.openai.com, ...]   # deny-by-default
  allow_host_callback: true       # ④ 的 host MCP 端点
  credential_injection: none | env | egress    # 与 ⑥ 联动
```

由所在执行环境选实现：进程驱动 → Seatbelt profile + srt 代理；容器 →
egress sidecar；microVM → 网关。CMA 的「cloud sandbox 网络默认关闭、显式
开启」印证 deny-by-default 的默认值。S 线交付 spec 形态 + Seatbelt 实现。

### 3.10 多租户（C 线，预研结论先记录）

- kernel auth 中间件 per-request 解析 token → owner（kernel 表 `user_id`
  列已铺好，现为进程级默认值，改请求级注入）；
- 凭证表按 `(owner, provider)` 为键——A 用户的沙箱流量绝不允许挂 B 的 key；
- 事件订阅、用量聚合、配额全部 owner 作用域；
- **结构性简化：沙箱即租户边界**（每项目一沙箱 → 每沙箱单租户），多租户
  主要发生在控制面与共享组件（网关、broker、调度），密度问题交给 microVM
  运行时（CubeSandbox 单机数千实例）解决。

---

## 4. 抽象层面汇总（接口清单）

| 抽象 | 位置 | S 线交付 | C 线扩展 |
|------|------|----------|----------|
| `SandboxProvider` | `ports/sandbox_provider.py` | 协议 + `local-process`（Seatbelt）+ `docker` 驱动 | `opensandbox` / `e2b-compatible` 驱动 |
| `Environment` | `api/openapi.yaml` + `valuz_environment` 表 + `modules/environments/` | 契约、CRUD、会话创建链路解析 | 云端 environment 类型 |
| `KernelClient` 双模 | 已有（P0.4） | 驱动产出喂 `HttpKernelClient`；`VALUZ_KERNEL_MODE` 按 environment 解析 | 不变 |
| toolkit MCP transport | 已有协议（P0.3） | 直连（host gateway URL 注入） | loopback stub + 队列/隧道 |
| `WorkspaceHandle` | `infra/fs_registry.py` 旁 | 接口 + LocalPath 实现 | RemoteFileAPI 实现 |
| 凭证引用 | `agent_config` key 引用形态 + Provider env 注入 | L1 | L2 broker / L3 egress |
| `NetworkPolicySpec` | `ports/sandbox_provider.py` | spec + Seatbelt/srt 实现 | sidecar / 网关实现 |
| kernel 自迁移 | 镜像 entrypoint / 进程驱动 spawn 钩子 | `KERNEL_SELF_MIGRATE=1` 路径 | 不变 |

---

## 5. 功能清单（分阶段）

### S0 — 供给面协议 + Environment 契约（地基，~1 PR）

- [ ] `ports/sandbox_provider.py`：`SandboxProvider` / `SandboxSpec` /
      `SandboxEndpoint` / `NetworkPolicySpec` / `MountSpec`
- [ ] `api/openapi.yaml`：`Environment` 资源 + CRUD 端点 → `make generate-types`
- [ ] `valuz_environment` 表（可逆迁移）+ `modules/environments/`
      （models/datastore/service/errors）+ `api/routes/environments.py`
- [ ] kernel 自迁移：standalone 启动时 `KERNEL_SELF_MIGRATE=1` 触发 alembic
      upgrade（迁移脚本打进发布产物）
- [ ] 会话创建链路：project.environment_id → provider 解析 → 端点缓存；
      `local-process`/缺省 = 现状直通（零回归）

### S1 — 进程驱动 + Seatbelt（场景一 MVP，~1-2 PR）

- [ ] `integrations/sandbox_local_process.py`：spawn valuz-server 子进程
      （复用裸子进程探针的 provision 形态：私有 DB + token + 端口分配 +
      `/health` 等待 + 崩溃重启 + 日志归集接 `valuz logs/doctor`）
- [ ] Seatbelt profile 生成（macOS；Linux 用 bubblewrap 同形态）：
      写白名单 = 项目根集合 + kernel 数据目录 + tmp；网络 = LLM 域名 +
      host 回调端口；**白名单必须含 claude/codex CLI 登录态路径**
      （`~/.claude`、`~/.codex` 等）
- [ ] ④ 直连：toolkit MCP URL 按驱动改写（127.0.0.1 / host gateway）——
      `always_on_http_mcp_servers` 的 base_url 来源按 environment 解析
- [ ] ⑥ L1：provider key 引用化 + `SandboxSpec.env` 注入（含 codex 订阅
      渠道的显式 env 声明）
- [ ] ⑤ 接口形态：`WorkspaceHandle` + LocalPath 实现过一遍 project 域路径
- [ ] 双模验收矩阵（见 §6）

### S2 — 容器驱动（本地强隔离，~1 PR）

- [ ] kernel OCI 镜像（python + claude-agent-sdk + codex CLI + node；
      版本钉 `KERNEL_VERSION`；资源默认值对标 CMA cloud sandbox 规格：
      Ubuntu 22.04 / 8GB / 10GB）
- [ ] `integrations/sandbox_docker.py`（bind mount 翻译 + host gateway +
      named volume 持久化）
- [ ] `integrations/sandbox_apple_container.py`（macOS 26+/AS，
      `container machine` 持久 VM；可选，受平台门槛限制）

### C1 — 远程工具 transport（场景二第一步）

- [ ] 队列 vs 隧道的对比验证（spike：dispatch 分钟级长调用两种 transport
      的失败注入测试），按结论实现其一
- [ ] kernel 侧 loopback stub；host 侧 worker/隧道终端
- [ ] ④ 鉴权升级：session 凭证真校验（从「localhost 即信任」到网络信任模型）
- 验收：云端（或模拟远程）kernel 的 task / memory / docs 全链路跑通

### C2 — 物料完整版

- [ ] skills bundle 上传 API + 沙箱内 materialize
- [ ] kernel 文件 API + 前端文件树查看器模式 + `outputs/` 契约
- [ ] 附件按引用 stage

### C3 — 凭证 L2 + 多租户

- [ ] OAuth 短效令牌注入 + 经 ④ 续签；API key broker 虚拟 key
- [ ] kernel owner per-request 注入 + 订阅/用量 owner 作用域
- [ ] （规模化后）L3 egress 网关注入评估（CubeEgress / OpenSandbox sidecar）

---

## 6. 验收标准

每阶段除常规门禁（`make test-all` / `typecheck` / `lint`，零增量基线对比）外：

| 阶段 | 验收 |
|------|------|
| S0 | Environment CRUD 契约测试；`local-process` 缺省路径全量回归零差异；kernel 自迁移探针（空 DB 冷启动 standalone → 表就位） |
| S1 | **双模验收矩阵**：`VALUZ_KERNEL_MODE=inprocess` 与 `sandboxed`（进程驱动）各跑一遍 e2e 冒烟——quick chat 流式 / task（plan→dispatch→review→finish）/ memory 读写 / docs 检索 / 决策审批 / 崩溃恢复（kill -9 kernel 子进程 → 重启 → finalize 复位）。Seatbelt 专项：沙箱内 `cat ~/.ssh/id_rsa` 拒绝、未白名单域名出网拒绝、claude/codex CLI 登录态可读、LLM 代理路径（`HTTPS_PROXY` 尊重）进矩阵 |
| S2 | 同一矩阵在 docker 驱动下绿；镜像构建进 CI |
| C1 | 工具调用断网注入：host 离线 → 调用排队/快速失败（按 transport 语义）→ 恢复续跑；dispatch 30 分钟长调用存活 |
| C2 | 带 skills 的 agent 在远程沙箱可用；文件树/产物取回 |
| C3 | kernel DB dump grep 不到任何明文凭证；跨 owner 访问被拒的对抗测试 |

---

## 7. 风险与决策点

| # | 决策/风险 | 建议 | 状态 |
|---|-----------|------|------|
| D1 | 场景一默认驱动的隔离强度：进程+Seatbelt（摩擦最小、隔离最弱）vs apple-container/docker（强隔离、有平台门槛） | 默认 Seatbelt，Environment 让用户可升级；产品如要求 VM 级默认则换 | **待拍板** |
| D2 | 沙箱粒度 | 每项目一沙箱（§3.5 论证） | 建议采纳 |
| D3 | C1 transport：队列 vs 隧道 | 队列优先评估（CMA 生产验证；失败模型简单；长调用天然支持），spike 后定 | 待 spike |
| D4 | 云端 cwd 产品语义：「拖文件夹」变「上传」 | 本地场景不发生此分叉；云端按「沙箱即项目目录」+按引用 stage；动手 C2 前与产品确认 | **待拍板** |
| R1 | Seatbelt `sandbox-exec` 被 Apple 标记 deprecated | 事实上极稳定（Claude Code 与 codex CLI 均押注）；保留 bubblewrap 同形态做对冲 | 接受 |
| R2 | 旧会话兼容：存量会话嵌的 agent_config 仍带工具声明/明文 key | P0.3 已验证声明共存无害；L1 引用化对存量行做一次性迁移或惰性重写 | S1 内处理 |
| R3 | apple/container pre-1.0、CubeSandbox 仅 x86_64 Linux+KVM | 均为可选驱动，不在默认路径 | 接受 |
| R4 | 性能：每 memory/docs 调用从函数调用变网络往返 | localhost 开销可忽略（codex 路径已验证）；高频循环工具逐个 review | 监控 |

---

## 8. 参考

- 内部：`docs/architecture.md`（adapter 缝一节）、`backend/CLAUDE.md`
  （kernel boundary 契约）、PR #85 / #86、
  `tests/adapters/test_http_kernel_client_subprocess.py`（供给形态的雏形）
- Claude Managed Agents：self-hosted sandboxes（worker/queue 契约、双钥匙
  分离、`/workspace` 与 `/mnt/session/outputs` 约定、文件按引用 stage）、
  cloud sandbox reference（镜像规格基线）、MCP tunnels（④ 的独立性论证）
- OpenSandbox（Apache-2.0）：lifecycle spec（Provider 字段蓝本）、egress
  sidecar、volume 模型（host/pvc/ossfs）、Docker+K8s 双后端
- CubeSandbox（Apache-2.0）：E2B SDK 兼容、CubeEgress（L3 凭证注入的现成
  实现）、microVM 密度数据
- anthropic-experimental/sandbox-runtime（srt）：Seatbelt/bubblewrap profile
  生成、代理收口网络、违规监控——S1 的直接依赖候选
