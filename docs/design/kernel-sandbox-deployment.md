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
    mounts: tuple[MountSpec, ...]  # ⑤ 挂载声明，含存储后端维度（§3.7.1）
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

#### 3.7.1 文件存储抽象：正本、物化视图与挂载清单

**心智模型（三句话）**：

1. **正本归属是 Environment 的属性**（`workspace_authority: host |
   sandbox`），不是定死的。本地部署两者重合（同一块盘，含 Seatbelt
   沙箱化的 kernel）；云端二选一 —— **host 正本**（OSS 默认：沙箱是
   无状态算力，cattle 不是 pet，数据全留用户机器，「拖文件夹」语义
   完整保留）或 **sandbox 正本**（托管服务：持久卷 + host 经文件 API
   做查看器）。
2. **进 = 引导时按清单挂载/打包**：沙箱启动时把 kernel 运行所需的全套
   物料一次性带入（见下方挂载清单），而不是逐文件零散拉取。
3. **出 = 正本天然持久化**：所有进入沙箱的文件，其正本存放在
   「本地目录或对象存储（S3/OSS）+ DB 索引」中（附件表、document 表、
   skills 索引）；沙箱只是正本的**物化视图**。本地「出」天然（同一
   文件系统）；云端 host-正本模式「出」= 回合边界增量写回（见下）；
   sandbox-正本模式「出」= `sync_out` / 文件 API 取回。

**host 正本模式的引导序列（云端 OSS 默认）**：

```
1. provision   镜像 + 空卷（或命中内容寻址缓存的暖卷）
2. stage-in    按清单物化：workspace 增量包 + skills bundle
               + 绑定 KB 子集 + kernel.db（上次回写的副本）
3. run turns   kernel 在本地卷上全速 POSIX 读写（零 WAN 延迟）
4. write-back  回合边界（kernel 静止期）：workspace 增量 diff +
               kernel.db 快照（WAL checkpoint 后复制）→ host 应用
               并更新索引；outputs/ 同通道
5. destroy     随时可销毁/被抢占 —— 沙箱上没有任何正本
```

关键洞察：**回合边界是天然同步点** —— kernel 回合间静止，此时写回
既安全又恰好匹配产品体验（用户在 agent 回复后查看文件）。工程纪律：
回合进行中 workspace 锁定给 run（host UI 只读/警告），复用 DB 单写者
的同一条产品纪律，不引入双向冲突解决；「实时镜像」体验是后续 mutagen
升级项。host 离线 = 任务暂停 —— 与 ④ 工具回调的可用性边界天然重合。
**明确拒绝**的两条路：host 导出网络文件系统（NFS/SSHFS —— agent 的
grep/ls 风暴打在 WAN 延迟上）与逐文件 API 代理（runtime 派生的真实
进程需要真实 FS）。

**挂载清单（provision manifest）** —— 进沙箱的全部物料，按部署形态
翻译；每项标注 `authority: host|sandbox` 与
`writeback: none|turn_end|continuous`（host-正本模式下 workspace 与
kernel.db 为 `turn_end`，skills/KB 为只读 `none`）：

| 物料 | 本地 Seatbelt（进程驱动） | 本地容器 | 云端沙箱 |
|------|---------------------------|----------|----------|
| kernel 服务本体 | 同一 PyInstaller 产物 | OCI 镜像 | OCI 镜像 |
| kernel.db（会话/事件） | 原路径，profile 白名单 rw | named volume | 持久卷 |
| 项目 workspace（cwd） | 原路径白名单 rw | bind mount rw | 持久卷（正本在卷） |
| 依赖 skills 集 | 原路径白名单 ro | bind ro | bundle 打包进 |
| KB（源文件 + 解析后） | 原路径白名单 ro | bind ro | **选择性** stage（仅项目绑定的 KB）或 fuse_ro |
| 凭证 | env 注入（⑥L1） | env 注入 | 短效令牌/egress（⑥L2/L3） |

**红线（不进沙箱的东西）**：host 的 `valuz.db`（业务库，单写者锁属于
host 进程）与 OS 钥匙串/secrets **永不进入挂载清单** —— 本地 Seatbelt
同样如此，这正是沙箱化的意义；`~/.valuz` 整目录挂载是禁止项，清单只
枚举上表中 kernel 真正需要的子集。云端 KB 默认只带项目绑定子集
（local-first：全量上云必须是用户显式选择）。

**cwd 持久层的物理选型**（正本所在卷的介质）：块存储/卷，**不是对象
存储**。agent 负载是高频 `ls`/`grep`/小文件随机写，S3 的 POSIX 模拟
（s3fs/ossfs）有结构性缺陷（无原子 rename、stat 风暴、分页 list）——
FUSE-S3 不做 cwd。本地 = 本机目录/named volume；云端 = 供应商持久卷 +
快照；企业需跨节点时 JuiceFS（S3 后端 + 真 POSIX + 本地缓存）作为
选型而非默认。

**进出通道的机制层**：`MountSpec` 的 source 联合类型，由驱动按后端
能力把上面的清单翻译成具体动作：

```python
@dataclass(frozen=True)
class MountSpec:
    target: str                      # 沙箱内路径（如 /workspace/data）
    mode: Literal["rw", "ro"]
    source: HostPathSource | VolumeSource | ObjectStoreSource

@dataclass(frozen=True)
class ObjectStoreSource:
    protocol: Literal["s3", "oss", "gcs"]
    bucket: str
    prefix: str
    credentials_ref: str             # ⑥ 凭证面引用（env:.../secret:...），不存明文
    strategy: Literal["stage", "fuse_ro", "sync_out"]
```

三种 strategy 的语义与适用：

| strategy | 语义 | 适用 | 驱动翻译 |
|----------|------|------|----------|
| `stage`（**默认**） | provision/回合边界按清单把正本**物化进沙箱**（CMA 的 by-reference 模式）；写回显式触发（更新正本 + 索引） | 附件、绑定 KB、skills bundle、中小数据集 | 全驱动通用——进程驱动也能做（仅它可用） |
| `fuse_ro` | 只读 FUSE 挂载（s3fs/ossfs/goofys） | 大数据集（拉不动、只读扫描） | 容器/microVM 驱动；OpenSandbox 原生 `ossfs` volume 即此形态 |
| `sync_out` | `outputs/` 目录定期/会话结束时归档回对象存储 | 产物长期留存、host 离线取回 | 云端驱动 |

职责边界（防腐烂的三条线）：

1. **FsRegistry 永远不认识 S3** —— 它是 host 域路径权威；存储后端是
   Environment config / MountSpec 的属性，由 Provider 驱动翻译。
2. **凭证经 ⑥ 走引用**（`credentials_ref`）——对象存储的 AK/SK 不进
   MountSpec 明文，L3 阶段可由 egress 网关注入（S3 请求同样是 HTTPS）。
3. **kernel 不感知存储协议** —— 它只看到 cwd 里的文件；stage/fuse/sync
   全部发生在 Provider/驱动层（provision 钩子或 sidecar）。

引用方案统一（⑤ 按引用传递的具体化）：session metadata / MountSpec 中的
文件引用采用 URI 形式 —— `s3://bucket/key`、`oss://…`、
`valuz-attachment://<id>`（host 附件库）、`valuz-kb://<doc_id>`。
驱动/worker 在执行前解析并 stage，与 CMA 的 `metadata.input_file`
模式同构。

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
| `MountSpec.source`（存储后端） | `ports/sandbox_provider.py` | `HostPathSource`（平凡） | `ObjectStoreSource`：stage（C2）→ fuse_ro / sync_out（按驱动） |
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
- [ ] 附件按引用 stage（统一 URI 引用：`s3://` / `valuz-attachment://` / `valuz-kb://`）
- [ ] `ObjectStoreSource.stage` strategy（驱动通用 + 凭证经 ⑥ 引用）
- [ ] `sync_out`：`outputs/` 归档回对象存储（云端驱动）
- [ ] `fuse_ro` 大数据集只读挂载（容器/microVM 驱动；OpenSandbox ossfs 直通）

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
| D4 | 云端 cwd 产品语义：「拖文件夹」变「上传」 | **已消解（§3.7.1）**：云端 OSS 默认 host-正本模式（沙箱为无状态算力，回合边界写回），「拖文件夹」语义完整保留；仅托管服务的 sandbox-正本模式存在"上传"语义，属商业版产品决策 | 已消解 |
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

---

## 附录 A · Split 模式 blocked-point 实测审计（2026-06-12）

方法：独立 kernel（私有 DB + token，:18401）+ host 以
`VALUZ_KERNEL_MODE=http` 启动（隔离 VALUZ_DATA_DIR，:18099），实跑探测，
辅以代码审计。基线：upstream/main + PR #87 round-3（**不含 #86**）。

### 实测通过的（split 模式下真实工作）

| 面 | 证据 |
|----|------|
| ② 控制面 | host 经 `HttpKernelClient` 全程对话远端 kernel（httpx 日志可见每次 REST 调用）；host 业务 DB 与 kernel DB 完全分家 |
| ③ 事件面 | `DecisionAggregator` 启动即经 HTTP SSE 订阅远端 `GET /api/v1/events/stream` 200 — 全局事件流跨进程工作 |
| 鉴权 | token 链路（`VALUZ_KERNEL_TOKEN` → `KERNEL_AUTH_TOKEN`）正常 |
| 容错 | boot 不因 kernel 缝异常而失败（异常被捕获记日志） |

### Blocked points（按修复优先级）

| # | 断点 | 实测证据 | 修复归属 |
|---|------|----------|----------|
| B1 | **会话创建被远端 kernel 400 拒绝**：`Unknown or unregistered tools: abandon_task, …, memory_get, memory_write, submit_skill`（15 个）。host 把工具注册进自己进程的 registry，agent_config.tools 声明发到远端后对着空 registry 校验失败 | POST /v1/sessions → kernel 400 | **PR #86 即解**（tools=() + harness MCP 注入）。#86 是 split 模式硬前置，不只是架构统一 |
| B2 | `HttpKernelClient` 无 `scan_orphan_pendings/scan_orphan_runs/cleanup_runtime`（设计如此），但 `boot/steps.py` 经 facade 无条件调用 → boot 期 AttributeError（被捕获，恢复逻辑静默失效） | host.log 两条 AttributeError | S0：boot 按 kernel_mode 跳过（standalone kernel 启动时自跑 orphan 扫描，职责已在 kernel 侧） |
| B3 | **boot 零模式感知**：http 模式下仍跑 kernel 迁移、init 进程内单例、把 kernel 路由挂上 host app、注册工具——产生一个无人使用的"幽灵进程内 kernel"（独立 DB），浪费且混淆 | 代码审计（boot/* 无 VALUZ_KERNEL_MODE 引用） | S0：`init_kernel` / `get_kernel_routers` / 工具注册按模式分支 |
| B4 | `backend_base_url` 默认硬编码 `http://127.0.0.1:8000`，不随实际监听端口变化 → 非 8000 部署时 always-on MCP（docs/automations/connectors/harness）URL 指错进程 | host 跑 18099 时 settings 仍为 :8000 | S0/S1：base_url 从实际 bind 推导或强制显式配置 |
| B5 | kernel 不自迁移：standalone 启动容忍空 schema，探针需手工跑迁移 | 探针第 1 步即手工迁移 | S0：`KERNEL_SELF_MIGRATE=1`（设计 §5/S0 已列） |
| B6 | codex `CODEX_TOOLKIT_BASE_URL_DEFAULT=:8000` 指向 host 端口，而（#86 前）registry 在 kernel 进程 → codex 自定义工具双重死路 | 代码审计 | #86 合并后 expose_toolkit 恒 False，该路径休眠；S2 镜像阶段删除 |
| B7 | 凭证经进程 env 兜底（codex 订阅渠道 `credential_source=none`）：kernel 子进程必须显式带 OPENAI_API_KEY 等 | 既往生产事故 + 设计 §3.8 | S1 ⑥L1（key 引用化 + SandboxSpec.env 注入） |

### 结论

P0 的三条通道（控制/事件/工具协议）在 split 模式下成立；剩余断点全部
集中在 **boot 编排（B2/B3）与配置（B4/B5/B7）**，无新增架构缺口。
执行顺序建议：合并 #86 → S0 一个 PR 内清 B2-B5 → S1 清 B7。

---

## 附录 B · 最小化收口落地（实施基线）

> 本节是**实际落地的版本**：从完整设计（§3–§7）收口到「只服务两个真实
> 形态」的最小抽象集，并钉死实现边界。完整设计的其余部分（隧道/队列、
> host 正本写回、对象存储策略、凭证 L2/L3、Environment 一等资源）降级为
> **扩展路径**，仅当出现第三形态「桌面 host + 云 kernel」（NAT 才存在）
> 或对应需求时请回。

### B.1 服务的两个形态

| | 本地 | 远端 · host 作为 SaaS |
|---|---|---|
| host 位置 | 桌面 127.0.0.1 | 内网 service 地址 |
| kernel 位置 | 同机沙箱（Seatbelt / docker） | 云端沙箱池 |
| 拓扑 | 互相可达 | **互相可达**（host 有内网地址，无 NAT） |
| ④ transport | 直连 | **直连**（无隧道/队列） |

**关键洞察**：完整设计里 ④ 的隧道/队列复杂度全部源于「桌面 host 躲在
NAT 后」。这两个形态都不含该前提 —— 协议层**完全同构**，唯一变量是
`base_url` 与供给驱动。

### B.2 最小抽象集（仅四项，前三已建成）

| 抽象 | 状态 | 位置 |
|------|------|------|
| ② KernelClient（InProcess｜Http 双 transport） | ✅ P0.4 | `adapters/kernel_client*.py` |
| ③ 事件订阅（after_seq + SSE/tap） | ✅ P0.2 | kernel API + `event_sse_adapter` |
| ④ harness MCP（纯 HTTP 直连，两形态同构） | ✅ P0.3 | `integrations/toolkit_mcp_server.py` |
| ① **SandboxProvider + 挂载清单** | 🟠 新建 | `ports/sandbox_provider.py` + `integrations/sandbox_*.py` |
| ⑥ 凭证 **L1**（key 引用化 + env 注入） | 🟠 新建 | provision 时注入 `SandboxSpec.env` |
| ⑤ **WorkspaceHandle**（FsRegistry project 域抽象） | 🟠 新建 | `ports/workspace.py` |

> ⑤/⑥ 在最小版只落「本地实现」与「接口形态」，远端实现是扩展路径。

### B.3 FsRegistry 的本地/远端拆分（WorkspaceHandle）

FsRegistry 劈成两半，**职责不混**：

- **host 域**（不变、永不识 S3）：`doc_asset_dir` / `attachment_dir` /
  `secrets_dir` / `logs_dir` / parser 模型等 —— 全留本机，与沙箱无关。
- **project 域**（抽象到 `WorkspaceHandle`）：`project_cwd` / `task_path`
  / `subrun_dir` / `memory_dir` / skill staging —— 这些指向「项目工作区
  内的路径」，是 host 与 kernel 都要触及的区域。

```python
# ports/workspace.py
class WorkspaceHandle(Protocol):
    """项目工作区的路径/IO 抽象 —— 本地返回 Path，远端经 kernel 文件 API。"""
    def cwd(self) -> Path | str: ...            # 项目根
    def subpath(self, *parts: str) -> Path | str: ...
    async def read_bytes(self, rel: str) -> bytes: ...
    async def write_bytes(self, rel: str, data: bytes) -> None: ...
    async def exists(self, rel: str) -> bool: ...
```

| 实现 | 形态 | 状态 |
|------|------|------|
| `LocalWorkspaceHandle` | 直接 Path 操作（host 与 kernel 同盘） | 🟠 最小版落地，本地终态 |
| `RemoteWorkspaceHandle` | 经 kernel 文件 API（云端 host 作查看器） | 扩展路径（C2） |

**最小版立场**：本地形态（含 Seatbelt 沙箱化的 kernel）host 与 kernel
**同盘**，`LocalWorkspaceHandle` 即终态 —— 没有远端文件 API 的需求。
RemoteWorkspaceHandle 仅当 SaaS 上线、cwd 正本进沙箱卷时才实现。

### B.4 SandboxProvider —— 端点供给协议

```python
# ports/sandbox_provider.py
@dataclass(frozen=True)
class MountSpec:
    target: str                      # 沙箱内（本地=同路径）
    source: str                      # host 路径
    mode: Literal["rw", "ro"]

@dataclass(frozen=True)
class SandboxSpec:
    sandbox_id: str
    kernel_db_path: str              # 私有 kernel.db
    mounts: tuple[MountSpec, ...]    # 挂载清单（见红线）
    env: dict[str, str]              # ⑥L1 凭证注入落点
    allowed_domains: tuple[str, ...] # LLM 域名 + host 回调（deny-by-default）
    host_callback_url: str           # ④ harness MCP 的 host 地址

@dataclass(frozen=True)
class SandboxEndpoint:
    sandbox_id: str
    base_url: str                    # HttpKernelClient 直接可用
    token: str                       # KERNEL_AUTH_TOKEN

class SandboxProvider(Protocol):
    async def provision(self, spec: SandboxSpec) -> SandboxEndpoint: ...
    async def health(self, sandbox_id: str) -> bool: ...
    async def destroy(self, sandbox_id: str) -> None: ...
```

**立场**：协议在「端点供给」层 —— exec/文件读写不进协议（走 kernel
自己的 API），换驱动只换一个文件。provision 负责自举（迁移 + 等
`/health`），返回 `(base_url, token)` 直接构造 `HttpKernelClient`，
供给面与控制面在此交棒。

### B.5 最小本地形态：SeatbeltSandboxProvider

`integrations/sandbox_seatbelt.py`，两个职责：

**(a) profile 生成**（纯函数，核心价值，完全单测）：把挂载清单翻译成
`sandbox-exec` 的 Seatbelt profile —

```
(version 1)
(deny default)
(allow process-fork process-exec)
(allow file-read*)                          ; 读放宽
(deny file-read*  (subpath "<host valuz.db dir>"))   ; 红线
(deny file-read*  (subpath "<secrets dir>"))         ; 红线
(allow file-write* (subpath "<project cwd>"))        ; cwd rw
(allow file-write* (subpath "<kernel data dir>"))    ; kernel.db rw
(allow file-write* (subpath "<TMPDIR>"))
(allow file-read*  (subpath "~/.claude"))            ; CLI 登录态
(allow file-read*  (subpath "~/.codex"))
(allow network-outbound (remote tcp "127.0.0.1:<host port>"))  ; ④ 回调
(allow network-outbound (remote tcp "*:443"))        ; LLM（最小版宽松）
```

**技能与项目依赖目录（挂载清单完整性）**：rw 白名单由
`host_sandbox_rw_mounts()` 从 fs_registry 枚举 —— 项目根（`~/Valuz`）、
聊天 cwd 根、以及全部技能根（`~/.agents/skills`、`official_skill_root`、
`~/.claude/skills`、`~/.codex/skills`）。技能物化把符号链接写进
`<cwd>/.agents/skills` 与 `.claude/skills`，技能创建写进技能根 —— 漏掉
任一目录，runtime 一旦 set up 技能就 "Operation not permitted"。

**动态覆盖（按根授权）**：Seatbelt profile 在进程启动时一次性固定，无法对
运行中的沙箱追加规则。因此 write-allow 的是**根目录**而非具体项目 —— 在
`~/Valuz` 和各技能根**之下**动态新建的项目/技能无需重新 provision 即生效。
唯一不覆盖的是绑定到这些根**之外**的任意文件夹项目，那是单一 host-wide
沙箱的固有边界，需 per-project 沙箱（§3.5）。

**红线（强制）**：host 的 `valuz.db` 所在目录与 `secrets_dir` 显式
`deny`，`~/.valuz` 不整体放行 —— 这正是沙箱化的意义。

**(b) 进程驱动**：`provision()` = spawn `valuz-server` 子进程（套上
profile），私有 DB + 自迁移 + token + `--port 0` 读回端口 + 等 `/health`；
`destroy()` 终止进程。

**子进程环境**：`DATABASE_URL=私有kernel.db` · `KERNEL_AUTH_TOKEN` ·
`KERNEL_SELF_MIGRATE=1` · `CODEX_TOOLKIT_BASE_URL=host回调` ·
provider key（⑥L1，显式注入避免 codex 订阅渠道 env 兜底问题）。

### B.6 boot 模式感知（清 B2–B5）

`kernel_mode=http` 时 boot 必须收敛（否则养一个「幽灵进程内 kernel」）：

| # | http 模式下的行为 |
|---|------------------|
| B2 | 跳过 `scan_orphan_*`（HttpKernelClient 无此方法；standalone kernel 自跑） |
| B3 | 跳过 kernel 迁移 / 进程内单例 / 挂 kernel 路由（kernel 自管）；**保留** host toolkit MCP（④ 回调靶点） |
| B4 | `backend_base_url` 必须反映实际监听端口（沙箱内 kernel 据此回调 ④） |
| B5 | kernel 子进程 `KERNEL_SELF_MIGRATE=1` 自迁移 |

### B.7 砍掉项与回归触发条件

| 砍掉 | 回归条件 |
|------|----------|
| WS 隧道 / 长轮询队列 / loopback stub · host 正本写回 | 第三形态「桌面 host + 云 kernel」（NAT） |
| ObjectStore fuse_ro / sync_out · JuiceFS | 大数据集 / 产物归档需求 |
| 凭证 L2 / L3 | SaaS 安全加固期 |
| Environment 一等 API 资源 | 降级为部署配置 + project 元数据存 sandbox 句柄；「按项目选环境」时升一等 |
| RemoteWorkspaceHandle | SaaS 上线、cwd 正本进沙箱卷 |

**一句话**：最小版本 = P0 三通道（已建成）+ SandboxProvider(清单) +
WorkspaceHandle(本地) + L1 凭证 ≈「修 B2–B5 + 写一个 Seatbelt 驱动」。

### B.8 运行与环境校验

**启动**（env 开关,默认不变）：

```bash
VALUZ_SANDBOX_DRIVER=seatbelt make dev     # 或 ./scripts/dev.sh
make dev-sandbox                            # 等价便捷目标
```

env 经 make → dev.sh → `python -m valuz_agent` → `_provision_sandboxed_kernel`
一路透传:host 在 `create_app` 前 provision 一个 Seatbelt 受限 kernel,切
http 模式对接(`settings.kernel_mode=http` + url/token + `rebind_client`)。
不设 `VALUZ_SANDBOX_DRIVER` 则字节级等同进程内模式。

**环境校验(preflight,失败响亮报错而非静默退回)**:
`seatbelt_preflight()` 在 provision 前检查三个硬条件 —— macOS、
`sandbox-exec` 存在、kernel 产物(`KERNEL_DIR/app/main.py`)可达。

立场:用户显式要了沙箱(`VALUZ_SANDBOX_DRIVER=seatbelt`),若环境不支持则
**拒绝启动**(`SystemExit` + 可执行指引),绝不静默退回进程内 —— 否则用户
以为 agent 被关进沙箱而实际没有,是安全惊吓。需要带警告降级时显式设
`VALUZ_SANDBOX_FALLBACK=inprocess`。

**reload 安全**:`--reload` 的 reloader 子进程见到父进程设的
`VALUZ_KERNEL_URL` 即跳过重新 provision,直接连已存活的沙箱(不会spawn第二个)。

**沙箱内 runtime 的前置**(per-session,非 boot 级):codex/claude CLI 的
登录态目录(`~/.codex`/`~/.claude`)在 profile 中为 **rw**(codex 要写
`state_*.sqlite`);provider key 经 `SandboxSpec.env` 注入(⑥L1)。host 业务
DB(+wal/shm)与 secrets 始终 deny。

---

## 附录 C · 长命 kernel + 动态挂载（macOS sandbox extensions，2026-06-12）

> 解决的问题:沙箱在 boot 时 profile 固化文件权限。**沙箱已启动后**,用户
> 新建 project 映射一个静态 mount 之外的外部目录,如何在**不重启整个 kernel**
> 的前提下把该路径实时挂进运行中的沙箱?这是核心不变式("外面套一层关住整个
> kernel,给 kernel 一台电脑")下唯一不破坏该不变式的动态挂载手段。

### C.1 机制:三方协作的 sandbox extension

macOS `sandbox_extension_issue_file` / `consume` / `release`(libsystem_sandbox
SPI,无公开头文件;是 Apple powerbox 与 Chromium/WebKit renderer 动态文件访问
的同一底层,事实上稳定 ABI):

1. **profile 预声明**(provision 时一次性,零风险):
   `(allow file-read* file-write* (extension "com.apple.app-sandbox.read-write"))`
   —— 仅表示"我接受这一类别的合法令牌",无令牌则不放行任何路径。
2. **宿主签发**(host 未沙箱化,有权访问该路径):
   `issue_extension(realpath, "rw")` → 224 字节 HMAC 令牌。
   **两个实测坑**:`flags` 必须为 0(`flags=1` 返回字面量 `"invalid"`);
   路径必须先 `realpath`(Seatbelt 按真实路径匹配)。
3. **kernel 消费**(已在沙箱内、已运行):`sandbox_extension_consume(token)`
   → handle。此刻该路径活,**进程不重启**;`release(handle)` 撤销。

**承载性已实测确认**(本机):
- 完整生命周期:consume 前 DENIED → consume 后 OK → release 后 DENIED;
  宿主侧文件真实可见(非假象)。
- **子进程继承**:kernel 进程 consume 后,它 fork/exec 的 codex/claude CLI
  子进程**继承**该权限(无需自己 consume)—— agent 真正的文件访问在子进程,
  这一条是设计成立的关键。

### C.2 抽象(云端就位)

| 落点 | 文件 | 职责 |
|---|---|---|
| 抽象 | `ports/sandbox_provider.py` | `MountGrant`(`MountSpec` 的运行时对应物)+ `bind_workspace`/`unbind_workspace`。`kernel_cwd` 是**云端缝**:本地 == 入参路径(同一文件系统),云端 == staging 后的 `/workspace/{id}` |
| host 签发 | `integrations/sandbox_seatbelt.py` | `issue_extension`;`SeatbeltSandboxProvider.bind_workspace`(issue + POST grant)、`from_existing`(reload 子进程用) |
| kernel 消费 | `kernel/app/sandbox_control.py` | 自包含 ctypes consume/release + `/internal/sandbox/{grant,revoke}`;仅 `KERNEL_SANDBOX_CONTROL=1` 时挂载;**不 import host**,kernel 保持宿主无关 |
| host 注册表 | `integrations/sandbox_runtime.py` | `ensure_workspace_granted(cwd) -> kernel_cwd`:静态根/进程内模式下 no-op;外部路径 issue 一次 + 缓存 + 锁;失败降级返回原 cwd(不阻塞建会话) |
| 唯一缝 | `adapters/kernel_client_http.py::create_session` | 所有建会话调用方(sessions/tasks/dispatcher/orchestrator)都汇流于此;仅 http transport(沙箱/远端)需要 |

**为什么是 `create_session` 这一处**:host 侧所有 `kernel_client.create_session`
最终走 `client.create_session(req)`,HTTP transport 是单一咽喉。每次建会话前
`ensure_workspace_granted(req.cwd)` 幂等地确保 cwd 在沙箱内可达 —— 静态根下的
project(`~/Valuz`、`data_dir/projects`)直接放行;boot 后绑定的外部目录才
issue 扩展。"首次"与"动态新 project"由同一路径统一覆盖。

**reload 健壮性**:`sandbox_runtime` **从 env 惰性激活** —— 任何带
`VALUZ_SANDBOX_DRIVER=seatbelt` + `VALUZ_KERNEL_URL` 的 host 进程首次调用时
自激活(用 `from_existing` 构造只持有 endpoint 的 provider,签发只需宿主特权 +
endpoint)。不依赖 uvicorn 如何在 provision/serve 之间拆分进程。

### C.3 边界与代价

- **零回归**:profile 预声明那行向后兼容(无令牌即原行为);未设沙箱时
  `ensure_workspace_granted` 全程 no-op 返回原 cwd。
- **SPI 风险**:`sandbox_extension_*` 是私有 SPI,需 ctypes 取符号 + 钉版本
  回归(每个 macOS 大版本跑一遍探针)。off-macOS / SPI 缺失时 endpoint 答 501、
  `issue_extension` 抛 `SandboxProvisionError`,不崩。
- **云端不走此路**:extensions 是 macOS 本地专属;云沙箱挂统一工作区路径 +
  文件 API staging(`kernel_cwd` 返回 `/workspace/{id}`),是 `bind_workspace`
  的另一实现 —— 抽象已就位,下一步落地。

### C.4 验证

- 单元:profile 含预声明行;`issue_extension` 真令牌(>50 字节、非 `"invalid"`);
  `sandbox_runtime` 静态根 no-op / 外部路径 issue 一次 / 失败降级(fake provider)。
- **端到端(macOS)**:真实 `sandbox-exec` kernel(挂控制面)consume 一个
  host 为**未挂载**外部路径签发的令牌,返回 live handle,接受 revoke ——
  证明跨进程边界的 no-restart 动态授权。
- 文件:`tests/integrations/test_sandbox_dynamic_mount.py`(9 通过)。
