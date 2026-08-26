# DataService 架构

[English](data-service-architecture.md)

> **DataService** 是 kernel 三张表(`sessions` / `messages` / `events`)的**唯一
> CRUD 数据层**。kernel 数据的每一次读写都流经它。本文是其架构、交互流程,以及各
> **部署形态**(纯本地、沙箱、remote 同步、SaaS)如何用**同一套机制 + 两个可切换
> 旋钮**实现的权威来源。
>
> 配套文档:[architecture.md](../architecture.md)(系统拓扑)、
> [kernel-sandbox-deployment.md](kernel-sandbox-deployment.md)(沙箱供给)。

---

## 1. 原则

**唯一数据层,永远在路径上。** 生产接线里,kernel **从不**直接用数据库驱动访问它
的三张表,而是访问 **DataService** —— 一组 CRUD 操作(即 `StorePort` 接口),以
**挂在 host 应用上的 FastAPI router** 形式暴露(`POST /rpc/{op}`,每个 StorePort
方法一个 op)。**不另起独立的 DataService 进程**,它是 host 的一个子路由。

DataService 的**后端可切换**,**传输也可切换**。其它一切——"本地""沙箱""remote
PG""SaaS"——都只是这两个旋钮的组合。**不存在按形态分叉的代码路径**。

```
        kernel(进程内 或 在沙箱中)
                     │  StorePort
                     ▼
        ┌─────────────────────────────┐
        │  DataService(host router)   │     ← 唯一数据层
        │  POST /rpc/{op}              │
        └──────────────┬──────────────┘
                       ▼  后端(可切换)
         ┌─────────────┴──────────────┐
         │ host sqlite(默认)          │  或  remote PG(开启"remote 同步"时)
         └─────────────────────────────┘
```

---

## 2. 两个正交旋钮

| 旋钮 | 取值 | 由谁决定 | 作用 |
|------|------|----------|------|
| **执行位置** | 进程内 kernel · seatbelt 沙箱 · (未来)云沙箱 | 部署 / `VALUZ_SANDBOX_DRIVER` | *agent loop 在哪运行*,从而决定到 DataService 的**传输**(进程内调用 vs HTTP) |
| **DataService 后端** | host sqlite(默认) · remote PG | **OSS 设置页**("数据服务" → remote 同步) | *kernel 数据持久落到哪*;remote PG 会启用 **JWT 鉴权边界** |

两者**相互独立**:沙箱化不等于 remote PG;remote PG 不等于有沙箱。任意组合都成立。

---

## 3. 部署形态(旋钮矩阵)

| # | 执行 | 后端 | 到 DataService 的传输 | 说明 |
|---|------|------|----------------------|------|
| 1 | 进程内 kernel(无沙箱) | host sqlite | **进程内**调用 | OSS 默认。kernel 三表*经* DataService 落到 host 托管的 sqlite。 |
| 2 | seatbelt 沙箱 | host sqlite | **HTTP**(沙箱 → host 回调 URL,JWT) | 用户视角与 #1 一致。沙箱同时写自己的本地 sqlite(缓冲);outbox 保证 host sqlite 最终收敛。 |
| 3 | 进程内 **或** 沙箱 | **remote PG** | 进程内 或 HTTP | 配置了"remote 同步"。数据额外经 DataService 落到 remote PG。有沙箱时 **JWT 边界**确保 **PG 凭证不进沙箱**。 |
| 4(SaaS) | 云沙箱 | remote PG | HTTP,JWT | 即 #3 + 临时云沙箱 + 中心 PG —— **即配即用**,因为机制完全相同。 |

要点:形态 1→4 是**同一份实现**翻动两个旋钮。SaaS 不是分叉,而是「form 3 + 云沙箱
驱动 + PG 后端」。

下图汇总所有形态的交互与模块依赖(蓝=数据流/读写,红虚线=沙箱 HTTP+JWT 边界):

![DataService 架构 — 所有形态的交互与模块依赖](data-service-architecture.svg)

---

## 4. 写路径 —— 双写 + outbox 一致性

一次写做**两件事**:

1. **本地 sqlite**(kernel 执行本地的库——沙箱本地,或进程内时的 host 本地)。快、
   始终可用、扛得住 DataService 抖动。
2. **DataService**(→ host sqlite 或 remote PG)。读所服务的那份持久/共享副本。

为了让 DataService 那份即使在 DataService/PG **短暂不可用**时也能**最大程度一致**,
写 DataService 失败时会记入**本地 sqlite 的 `durable_outbox`**,由后台 drainer
**重推**直到落地。重放是幂等的(event 用 `event_uid`,session/message 用 UUID
主键),所以 at-least-once 重投安全。这正是 `durable_outbox` 的职责:**保证双写中
「写入 DataService」这一路的最终一致性。**

**塌缩优化。** 当执行本地 sqlite 与 DataService 后端解析为**同一文件**(纯进程内 +
sqlite 后端,form 1),双写**塌缩为一次写**,读是直接调用——无自我镜像、无需 outbox。

**Event seq。** 每个物理 store 各自拥有自己的 `events` 自增序列;两条序列**相互独立**,
靠 `event_uid` 桥接身份。读者看到的 seq 是 **DataService 后端**的 seq(读来自那里)。
**绝不**把一个 store 的 seq 强加到另一个 store 的主键上(会和已存在的 id 冲突并丢行)。

---

## 5. 读路径 —— 一律经 DataService

读(历史重塑:`get_events` / `get_events_window` / session 与 message 拉取)由
**DataService 后端**服务——**绝不**来自执行本地 sqlite。理由:沙箱(尤其云沙箱)是
**临时的**,其本地 sqlite 可能已不存在,故不能作读源。

- **Form 1**(进程内 + sqlite):host 经**进程内** DataService → host sqlite 读。无 HTTP。
- **Form 2–4**(有沙箱 和/或 PG):host 经 DataService router → 后端读。因为
  DataService 在 **host** 上,所以**即便沙箱 kernel 已销毁,历史读依然成功**。实时、
  不持久化的 deltas(`text_delta` / `tool_output_delta` 等)在沙箱存活时仍走 kernel
  的 live bus;沙箱没了就退化为只有历史。

---

## 6. 鉴权与隔离边界

DataService 对每个请求的 **owner** 来自**验签过的不透明 bearer 凭证**，**绝不**
取自请求体。OSS 当前凭证是 per-owner HS256 JWT。host 的异步
`SandboxCredentialVerifierPort` 与内置 MCP 共用，因此托管部署可验证数据库/缓存
支持的 workload credential，而无需改变 HTTP 契约。kernel DataService 同时保留
旧同步 `TokenVerifier` 适配，兼容独立 OSS 调用方。后果:

- **沙箱只持有短时凭证** + DataService URL。它**永不**拿到 DB DSN、驱动或 PG 凭证
  ——凭证只在 host 上(DataService 的后端配置)。
- 「短时」意味着它会在 **kernel 运行期间过期**,所以它是可就地轮换的:`RemoteStore`
  通过**每次调用**的 `access_token` hook 解析 bearer,`dependencies.set_data_api_token`
  在其背后换值,`POST /internal/credentials/refresh` 让 host 从外部触发(host 先把新值
  写进 config gate 那个文件,这样之后若真的重启也仍是最新的)。**不要**靠重启 kernel 或
  替换沙箱来轮换:kernel 持有进行中的 turn 以及挂在它下面的 `run_in_background` 进程。
  刷新只应用一个白名单——无差别重读会让进程拿到全新的 `os.environ`,而其它组件手里
  还是启动时捕获的旧值。
- 在 **remote PG** 后端上,**行级安全(RLS)**是 DB 侧兜底:DataService 按事务从
  验签 token 把 `app.current_user_id` 注入(`SET LOCAL`),并以**非 owner 角色**连接,
  即使 app 层漏了过滤,RLS 策略仍然生效。
- owner-from-token 规则意味着被攻破的沙箱无法读写他人数据。

---

## 7. 传输

DataService 的客户端接口与传输无关,只是绑定不同:

| 执行 | 绑定 | 线路 |
|------|------|------|
| 进程内 kernel | 直接调用 host 的 DataService router(或其 store) | 无 |
| 沙箱 kernel | HTTP `POST /rpc/{op}` 到 host 回调 URL | JSON 行 + `Bearer <jwt>` |

host 自身的消费方(SSE adapter 等)用进程内绑定;只有沙箱化的 kernel 才跨 HTTP 边界。

---

## 8. 交互流程

### 8.1 写(沙箱 kernel,remote PG 后端 —— form 3/4)

```
agent turn → kernel.append_event
   ├─ 写沙箱本地 sqlite                      (缓冲;快)
   └─ POST /rpc/append_event  ─HTTP+JWT─▶  host DataService
                                              ├─ 验 JWT → owner
                                              ├─ SET LOCAL app.current_user_id
                                              └─ INSERT … RETURNING seq → PG
        HTTP/PG 失败 ▶ 入 durable_outbox(本地) ▶ drainer 重推
```

### 8.2 读历史(host,沙箱已销毁)

```
client 打开会话 → host SSE adapter
   └─ DataService(host router) → PG:get_events_window / get_events_after
        → 翻译成 legacy SSE 帧 → client                 (无需 kernel)
   实时 deltas:订阅 kernel SSE → 沙箱已没 → 退化为只读历史(优雅降级)
```

### 8.3 默认(进程内 + sqlite —— form 1,塌缩)

```
kernel.append_event → DataService(进程内) → host sqlite   (单次写)
读 → DataService(进程内) → host sqlite
```

---

## 9. 控制面

**所有行为都从 OSS 设置页控制**——不再有按形态定制的启动脚本。

- **设置 → 数据服务**(常规设置分区,位于「文档解析」与「服务日志」之间):
  - **模式 / 后端**:默认(host sqlite) vs **remote 同步**(PG DSN);(沙箱/远程时)
    DataService URL + token。
  - **健康状态**:DataService 及其后端的实时健康指示。
  - **OpenAPI**:暴露 DataService 的 OpenAPI(`/rpc/*` 契约),便于查看 schema。
- **取消 `make dev-remote`。** 它把"起 PG""起数据服务""跑沙箱"揉进一个脚本。换成一个
  极薄的 **`make pg` / PG-podman helper**,只拉起本地 Postgres;其余(开 remote 同步、
  指向 PG、是否沙箱)全部由设置页驱动。这样基础设施与行为解耦。

---

## 10. SaaS 扩展

SaaS 即 **form 4,无新数据路径**:云沙箱驱动(执行旋钮)+ 中心 PG 后端(后端旋钮),
两者均已抽象。由于 DataService 凭证边界与本地形态完全一致,云沙箱与中心化 PG
**即配即用**:SaaS overlay 绑定云 `SandboxDriver` 和
`SandboxCredentialVerifierPort`、把 DataService 后端指向托管 PG 即可，kernel 与
数据路径不变。

---

## 11. 与现状的差距

> **已落地:** host 持久化 DS 密钥 + token 签发;DataService 挂为 host router
> `/internal/data`(store + verifier 在 lifespan 绑定);沙箱经 HTTP+JWT 指向它、
> 不持 DSN;设置页有健康 + OpenAPI;OSS 两档 `仅本地 | 数据服务·Postgres`;
> `make pg` 取代 `make dev-remote`。**读已统一走 DataService**——host 进程内直读
> 自己挂载的 DS store,与沙箱死活无关(无 alive/dead 分支),**死沙箱也完全可读**。
> 已由 `scripts/e2e_host_data_service.py` 验证(host DS over 真实 PG,JWT 往返)。
> **剩余:** live seatbelt E2E。

> 本节是承接本文之后实现工作的桥梁。

**已就位:** `/rpc/{op}` DataService 应用 + StorePort 接口
(`kernel/app/data_service.py`)、`store_wire` 编解码、JWT 签发/验签 +
`TokenVerifier` 端口、RLS 迁移、`event_uid` 幂等、`durable_outbox` 表 +
`DurableOutbox` drainer、host 的 `DataServiceReadClient` + SSE 读路由、进程内 PG
的 `WriteThroughStore`,以及设置页 + `/v1/settings/data-service` 配置。

**为符合本文须改:**

1. **DataService 永远是数据层、挂成 host router。** 目前 `local` 模式绑的是直连
   `SQLAlchemyStore`(绕过 DataService),DataService 应用只在 `remote` 时独立使用。
   应把 DataService router 挂到 host FastAPI,并让进程内 kernel 走它(同文件场景做
   塌缩优化)。
2. **重构旋钮。** 把 `local | pg | remote` 这种单一 *store-mode* 换成两个独立设置:
   **后端**(host sqlite | remote PG DSN)与沙箱执行选择。"remote 同步" = 后端是 PG;
   它与「有/无沙箱」自由组合。
3. **复活 dual-write + outbox** 作为沙箱/HTTP 路径的写一致性机制(最近被我误判为死代码
   并停用的 local-authority + `durable_outbox` 正是此处的正确工具——复活它,由「是否有
   DataService 跳转」驱动,而非 `pg` 档)。
4. **读一律经 DataService**(host router),所有形态——包括 form 1(进程内),此时是
   进程内直接调用。
5. **设置页**:修复 9 连点不出分区的 bug;并给数据服务面板加 **健康状态** + **OpenAPI** 展示。
6. **删除 `make dev-remote`**;新增极薄的 **PG-podman** helper;remote 同步从设置页驱动。

每一项都在契约测试(`test_data_service_contract.py` 钉住 route↔client↔StorePort)与
全量测试守护下增量落地。
