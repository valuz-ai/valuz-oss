# Runtime/Model 兼容性 —— 单一真值、前端 dumb-render、可用性由 kernel 提供

> 状态：设计（提案）。英文版见 [runtime-model-compat-single-source.md](runtime-model-compat-single-source.md)。
>
> 一句话方向：**"哪个 runtime 能跑这个 model" 只在一个地方推导（`runtimes_for`），
> 物化到每条 model 行（`LLMModel.runtimes`），前端原样渲染。** 前端不再从
> `protocol`/`provider_kind` 重新推导兼容性。另外，**"这个 runtime 到底能不能跑"
> 由 kernel 回答**——它才是真正启动 runtime、持有其二进制的进程——经由既有的
> `KernelClient` seam 读取，因此无论 kernel 在进程内（打包桌面版）还是在云端沙箱里，
> 答案都正确且一致。

本文是 OSS 侧契约。贡献方 overlay（往通道列表里加 gateway/catalog 通道的
`LLMProvider`）在自己贡献的行上声明 `runtimes`；OSS 提供推导规则、单一物化点、前端
管线，以及由 kernel 上报的可用性查询及其默认进程内实现。可用性**无需 overlay 绑定**
——kernel-client 抽象本身已经承载了本地/远端差异。

---

## 1. 为什么

Runtime↔model 兼容性目前在**三处**各自实现，已经漂移：

| 实现 | 位置 | codex 规则 |
|---|---|---|
| **权威** | `modules/settings/model_options.py:runtimes_for` | 任何非订阅通道，只要说 `openai-response` → codex |
| 前端重算 #1 | `packages/core/src/hooks/use-composer-providers.ts` | 非订阅 + `canDriveAny(["openai-response"])` → codex |
| 前端重算 #2 | `packages/core/src/api/runtime-compat.ts` | codex **仅**限 `provider_kind==="system"` 或 `codex-subscription` |

`runtimes_for` 已经实现了正确规则（用户自带的 OpenAI 兼容 Responses 端点——例如
Volcengine Ark——可以通过 kernel 合成的 `[model_providers.harness]` 块、
`wire_api="responses"` 来驱动 codex；见 `backend/kernel/src/runtimes/codex/runtime.py`）。
它已原样暴露在 `GET /v1/settings/model-options`（`ModelOption.runtimes`）上，默认
配置卡片（`ModelSection.tsx`）和 onboarding（`ConnectStep.tsx`）已经在 dumb-render
这个字段。

但 composer、项目 agent picker、以及 provider 列表的"可用于"徽章**没有**读这个
字段——它们经由上表两个前端实现从 `compatible_protocols`/`provider_kind` 重算。
两个前端实现彼此、以及与后端都不一致，于是：

- 一个已测通的自定义 `openai-response` provider **不显示"OpenAI Codex"徽章**
  （`runtime-compat.ts` 要求 `provider_kind==="system"`），尽管它能跑 codex。
- 合法声明了 `runtimes=("codex",)` 的贡献通道，会因为不同界面用了不同实现而被
  丢弃或误标。

根因：那次"服务端解析 model-options + dumb-render pickers"的迁移只接进了*部分*
picker。providers 的 list/detail 接口仍然把 `LLMModel.runtimes` 留空，逼着其它
所有界面自己重算。

**第二个、部署形态相关的缺口：** `is_runtime_available`（`adapters/runtime_registry.py`）
探测的是 **API 宿主**的 PATH / bundled 二进制里的 codex。打包桌面版里这个宿主
*就是*跑 turn 的地方，所以正确。但在 kernel 跑在独立沙箱的分离部署里，API pod
的 PATH 是错的度量对象——pod 可能没有 codex 而沙箱有（反之亦然）。
`modules/system/service.py` 里的 `_runtimes_available` 已经放弃探测、直接返回静态
集合，正说明了这一点。修法是去问 **kernel**——真正启动 runtime 的那个进程——经由
本就区分本地/远端的 seam（§3.3）。

## 2. 现状（权威归属图）

- **权威 —— model→runtime 推导：** `runtimes_for(protocols, provider_kind)` 与
  `build_model_options`（`modules/settings/model_options.py`）。
- **权威 —— runtime↔协议能力：** `RUNTIME_REGISTRY`（`adapters/runtime_registry.py`），
  镜像 kernel `src/runtimes/factory.py:ALLOWED_PROTOCOLS_BY_RUNTIME`。
- **今天的可用性：** `is_runtime_available`（`adapters/runtime_registry.py`）探测
  API 宿主的 PATH / bundled `codex_cli_bin`。`GET /v1/runtimes` 读它。这正是 §3.3
  要挪到 kernel 的部分。
- **刻意镜像（成对维护）：** `providers/service.py:_derive_compatible_protocols`
  ↔ `provider_resolver._resolve_api_protocol`；OSS registry ↔ kernel factory ↔
  前端 `runtime-protocols.ts`。
- **生产环境死代码：** `runtime_registry.supports_protocol` —— 只有测试在调；
  真正的 runtime↔协议闸门是会话启动时的 kernel factory。
- **已经 dumb-render `runtimes` 的界面：** `ModelSection.tsx` 默认配置 picker；
  `onboarding/ConnectStep.tsx`。
- **仍在重算（待改造）的界面：** `AgentModelPicker.tsx`、`ConversationsHomePage.tsx`、
  `ProjectDetailPage.tsx`、`ConversationPage.tsx`（都经 `useComposerProviders`）；
  `ModelSection.tsx` 的 runtime 切换 guard 与"可用于"徽章（经
  `isProviderRuntimeCompatible`/`compatibleRuntimes`）。

## 3. 设计

### 3.1 在每个 model 界面物化 `runtimes`

`LLMModel.runtimes` 是已声明的 wire 字段（`modules/providers/schemas.py` 与
`packages/shared/src/types/provider.ts`，`string[] | null`）。今天
`_row_to_list_item` / `_row_to_detail`（`modules/providers/service.py`）在
user/builtin 行上刻意留 `None`、靠 picker 推导。改为用同一条规则填充：

```python
compatible = _derive_compatible_protocols(row)
ch_runtimes = tuple(runtimes_for(compatible, provider_kind=row.provider_kind))
models = [
    LLMModel(id=m.id, label=m.label, runtimes=(m.runtimes or ch_runtimes))
    for m in _resolve_models(row)
]
```

- 贡献方声明的 per-model `runtimes` 仍然优先；只填 `None`。因此
  `build_model_options` 行为不变（值与原先推导一致），`provider_resolver` 也不变
  （它只对*贡献行*、且仅在 UI 未传 `request_runtime_id` 时读 `model.runtimes`；
  DB 行仍走 `derive_runtime_provider`）。**会话解析不受影响——这只是展示/过滤。**
- `GET /v1/providers`（list + get）现在带上权威 `runtimes`，前端所有界面无需第二个
  接口即可读取。

**`default_model`-only 通道的优雅处理。** 一个通道可能解析出零条 `models[]`，却带一个
seeded `default_model`（历史 api-key 行）。今天 composer 靠一条 `default_model` 兜底行
呈现它。为了既保留该行为、又能删掉前端兜底分支：`_resolve_models` 在本会为空但
`row.default_model` 存在时，合成一条带 `ch_runtimes` 的 model 行。于是 list、detail、
`model-options` 三者一致，且每条 model 行都有真实的 `runtimes`。

这是**增量**改动：字段与其 `null` 语义都已存在；仍读 `null` 分支的旧客户端照常工作。

### 3.2 前端：dumb-render `runtimes`，收口到一个检查点

把两处客户端重算收敛到唯一的后端字段：

- `use-composer-providers.ts:useComposerProviders` —— 改为在 **model 级**按
  `m.runtimes?.includes(runtimeFilter)` 过滤。删除 `canDriveAny` /
  `canDriveAnthropic` / `CODEX_PROTOCOLS` / `DEEPAGENTS_PROTOCOLS` 及订阅 kind 的
  runtime 逻辑（订阅排除已由 `runtimes_for` 编码）。保留
  `providerHasUsableCredentials`（凭证闸门，非 runtime 闸门）。删除 `default_model`
  兜底分支（§3.1 保证只要有可跑的 model 就一定有一条 model 行）。
- `runtime-compat.ts` —— 把 `isProviderRuntimeCompatible` / `compatibleRuntimes`
  重写为 `provider.models[].runtimes` 的并集；`CompatProvider` 类型加入 `models`。
  删除 `speaksAnyProtocolFrom` 以及 `ALLOWED_PROTOCOLS_BY_RUNTIME` 的兼容用途。
  消费者（`ModelSection.tsx` 的 runtime 切换 guard + 徽章）签名不变。
- `runtime-protocols.ts` —— **保留**，供 New-Session / Edit-Capabilities 的
  **协议选择下拉**（`defaultProtocolFor` / `isProtocolAllowed`）使用。它不再被兼容性
  过滤引用。

结果：composer、agent picker、徽章、默认配置、onboarding 全部读同一个后端字段；
新增 runtime 时只需改一处（`runtimes_for`）。

### 3.3 Runtime 可用性由 kernel 上报

可用性（"这个 runtime 到底能不能启动"）是那个真正跑 runtime 的进程——kernel——的
属性，它本就持有二进制解析（`runtimes/codex/runtime.py:_resolve_codex_bin`）。把探测
挪到那里，并经由既有的 `KernelClient` seam（`adapters/kernel_client.py`）读取——该 seam
"one-to-one 镜像 kernel HTTP API"，并在同一 protocol 后面用 `InProcessKernelClient`
（打包桌面版）与将来的 `HttpKernelClient`（云端沙箱）互换。

- **Kernel：** 增加一个 `runtime_availability()` 能力（以及 client 所镜像的路由），
  返回 `{runtime_id: (available, reason)}`，由 kernel 自己的二进制探测算出（与它启动时
  用的解析顺序一致：`CODEX_BIN_OVERRIDE` → bundled `codex_cli_bin` → PATH）。kernel 是
  "我能不能启动 codex" 的唯一权威。
- **`KernelClient` protocol + `InProcessKernelClient`：** 加 `runtime_availability()`。
  进程内实现探测本地宿主——与今天打包桌面版行为一致。
- **宿主：** `GET /v1/runtimes`（及任何可用性读取方）改调
  `kernel_client.runtime_availability()`，不再用本地 `runtime_registry.is_runtime_available`。
  `RUNTIME_REGISTRY` 只保留静态元数据（display name、`supported_protocols`、
  `requires_binary`）；实时可用性答案来自 kernel。
- **本地与云端同一条路径：** 进程内 kernel → 探本地宿主；沙箱 kernel 经
  `HttpKernelClient` → 探沙箱（正确的宿主）。因为云端 kernel 镜像装了 codex
  （`backend/pyproject.toml` 在所有平台安装 `openai-codex-cli-bin`），沙箱会如实回答
  "codex 可用"——无需静态配置、无需 overlay port。
- **Picker 时可达性：** 可用性是镜像属性，不属于某个会话；kernel 无需会话即可回答。
  若池化部署在 picker 时不保证有 warm kernel，就按沙箱镜像 digest 缓存该答案——仍是
  kernel 来源，只是做了 memoize。

净结果：作为 API 宿主探测的 `is_runtime_available` 退役；kernel 成为可用性的唯一来源，
本地远端一致。

## 4. 契约影响（`contracts/COMPATIBILITY.md`）

| 变更 | 类别 | 说明 |
|---|---|---|
| `GET /v1/providers` list+get 现在填充 `LLMModel.runtimes` | evolving（增量） | 字段与 `null` 语义已存在；旧客户端读 `null` 分支 |
| `KernelClient.runtime_availability()` + 对应 kernel 路由 | new / stable | 进程内默认 = 当前本地探测；`GET /v1/runtimes` 形状不变 |
| `LLMProvider.list/resolve`、`RUNTIME_REGISTRY`（元数据）、kernel `ALLOWED_PROTOCOLS_BY_RUNTIME` | 不变 | — |

`is_runtime_available` 挪到 kernel 侧；`supports_protocol` 可删除或标记 deprecated
（无生产调用方）。

## 5. 改动清单

Kernel：
- `src/runtimes/factory.py`（或一个小的 capability 模块）—— `runtime_availability()` 探测。
- client 所镜像的 kernel HTTP 路由（与既有 surface 并列）。

后端（宿主）：
- `modules/providers/service.py` —— 在 `_row_to_list_item` / `_row_to_detail` 填 `runtimes`；在 `_resolve_models` 合成 `default_model`-only 的 model 行。
- `adapters/kernel_client.py` —— 给 `KernelClient` protocol + `InProcessKernelClient` 加 `runtime_availability()`。
- `api/routes/runtimes.py` —— 经 kernel client 读可用性；`RUNTIME_REGISTRY` 只留静态元数据。

前端：
- `packages/core/src/hooks/use-composer-providers.ts` —— model 级 `runtimes` 过滤；删推导与 `default_model` 兜底。
- `packages/core/src/api/runtime-compat.ts` —— `models[].runtimes` 并集；删推导。
- （`packages/core/src/api/runtime-protocols.ts` —— 不变；范围收窄到协议下拉。）

## 6. 迁移 / 顺序

1. 3.1 + 3.2 一起上（后端填 + 前端读）—— 自洽、wire 上增量。
2. 3.3（kernel 探测 + client 方法 + 宿主改走它）—— `InProcessKernelClient` 保持本地
   行为不变；云端收益在用 `HttpKernelClient` 时兑现。
3. 发布前契约回归（`make test-contract`）绿。

3.2 依赖 3.1（前端需要已填充的字段）。3.3 独立。

## 7. 测试

- `_row_to_list_item` / `_row_to_detail` 对 `anthropic` / `openai-completion` /
  `openai-response` / 双协议 / 两种订阅 kind（codex-subscription → `["codex"]`、无
  deepagents）都正确填 `runtimes`；`default_model`-only 通道产出一条带 runtimes 的合成
  model 行。
- `InProcessKernelClient.runtime_availability()` 返回本地宿主真值；`GET /v1/runtimes`
  如实反映（codex 当且仅当 kernel 解析到二进制时可用），并在 stub 的
  `HttpKernelClient` 报告不同集合时仍正确。
- `useComposerProviders` 只按 `m.runtimes` 过滤；订阅排除仍成立（由后端 runtimes 驱动）。
- `runtime-compat.compatibleRuntimes` = `models[].runtimes` 并集；已测通的自定义
  `openai-response` provider 显示 codex 徽章。

## 8. 下游（overlay）职责

不放进 OSS，但在此写明，避免 seam 意图含糊：

- 往通道列表加 gateway/catalog 通道的贡献方 `LLMProvider`，在自己的行上声明
  `LLMModel.runtimes`。尤其是意在驱动 codex 的 `openai-response` 卡，声明
  `runtimes=("codex",)` 与 `compatible_protocols=["openai-response"]`（与今天贡献一张
  系统 gateway Responses 卡的方式一致）。OSS 原样消费。
- 可用性**无需** overlay 绑定：云端 kernel 跑在沙箱里、其镜像装了 codex，因此同一个
  `KernelClient.runtime_availability()` seam 会自动为该部署上报真值。

## 9. 非目标

- 不改 kernel factory 作为会话启动时 runtime↔协议最终闸门的地位。
- 不改 `web_search` 对非订阅 codex key 的强制禁用（kernel 侧）。
- 协议选择 UI（`runtime-protocols.ts`）仍是前端职责；它是*配置*辅助，不是兼容性来源。
