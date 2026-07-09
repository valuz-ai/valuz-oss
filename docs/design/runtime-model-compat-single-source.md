# Runtime/Model Compatibility — one source, dumb-render pickers, kernel-reported availability

> Status: Design (proposed).
>
> One-line direction: **"which runtime(s) can run this model" is derived in
> exactly one place (`runtimes_for`), materialized onto every model row
> (`LLMModel.runtimes`), and rendered verbatim by the frontend.** The frontend
> stops re-deriving compatibility from `protocol`/`provider_kind`. Separately,
> **"is this runtime actually runnable" is answered by the kernel** — the process
> that launches runtimes and owns their binaries — through the existing
> `KernelClient` seam, so the answer is correct and consistent whether the kernel
> is in-process (bundled desktop) or in a cloud sandbox.

This is the OSS-side contract. A contributing overlay (an `LLMProvider` that adds
gateway/catalog channels) declares `runtimes` on its contributed rows; OSS ships
the derivation rule, the single materialization point, the frontend plumbing, and
the kernel-reported availability query with its default in-process implementation.
No overlay binding is needed for availability — the kernel-client abstraction
already carries local-vs-remote.

---

## 1. Why

Runtime↔model compatibility is currently computed in **three** places that have
drifted apart:

| Impl | Where | Codex rule |
|---|---|---|
| **Authoritative** | `modules/settings/model_options.py:runtimes_for` | any non-subscription channel speaking `openai-response` → codex |
| Frontend re-derive #1 | `packages/core/src/hooks/use-composer-providers.ts` | non-subscription + `canDriveAny(["openai-response"])` → codex |
| Frontend re-derive #2 | `packages/core/src/api/runtime-compat.ts` | codex **only** for `provider_kind==="system"` or `codex-subscription` |

`runtimes_for` already implements the correct rule (a user-supplied
OpenAI-compatible Responses endpoint — e.g. Volcengine Ark — can drive codex via
the kernel's synthetic `[model_providers.harness]` block, `wire_api="responses"`;
see `backend/kernel/src/runtimes/codex/runtime.py`). It is surfaced verbatim on
`GET /v1/settings/model-options` (`ModelOption.runtimes`), and the default-config
picker (`ModelSection.tsx`) and onboarding (`ConnectStep.tsx`) already
dumb-render it.

But the composer, the project-agent picker, and the provider-list "可用于"
(available-for) badge do **not** read that field — they re-derive from
`compatible_protocols`/`provider_kind` through the two frontend impls above. The
two impls disagree with each other and with the backend, so:

- A tested, working custom `openai-response` provider shows **no "OpenAI Codex"
  badge** (`runtime-compat.ts` requires `provider_kind==="system"`), even though
  it can run codex.
- Contributed catalog channels that legitimately declare `runtimes=("codex",)`
  can be dropped or mislabeled depending on which impl a given surface uses.

Root cause: the "server-resolved model-options + dumb-render pickers" migration
(the `model-options` endpoint) was only wired into *some* pickers. The providers
list/detail endpoints still leave `LLMModel.runtimes` unset, forcing every other
surface to re-derive.

**A second, deployment-shaped gap:** `is_runtime_available`
(`adapters/runtime_registry.py`) probes the **API host's** PATH / bundled binary
for the codex binary. In a bundled desktop that host *is* where the turn runs, so
it's correct. In a split deployment where the kernel runs in a separate sandbox,
the API pod's PATH is the wrong thing to measure — the pod may lack codex while
the sandbox ships it (or vice-versa). `_runtimes_available` in
`modules/system/service.py` already punts and returns the static set,
acknowledging this. The fix is to ask **the kernel** — the process that actually
launches runtimes — through the seam that is already local/remote-aware (§3.3).

## 2. Current state (authority map)

- **Authoritative — model→runtime derivation:** `runtimes_for(protocols, provider_kind)`
  and `build_model_options` (`modules/settings/model_options.py`).
- **Authoritative — runtime↔protocol capability:** `RUNTIME_REGISTRY`
  (`adapters/runtime_registry.py`), mirroring kernel
  `src/runtimes/factory.py:ALLOWED_PROTOCOLS_BY_RUNTIME`.
- **Availability today:** `is_runtime_available` (`adapters/runtime_registry.py`)
  probes the API-host PATH / bundled `codex_cli_bin`. `GET /v1/runtimes` reads it.
  This is the piece §3.3 moves to the kernel.
- **Deliberate mirror (kept in lock-step):** `providers/service.py:_derive_compatible_protocols`
  ↔ `provider_resolver._resolve_api_protocol`; OSS registry ↔ kernel factory ↔
  frontend `runtime-protocols.ts`.
- **Dead in production:** `runtime_registry.supports_protocol` — only tests call
  it; the real runtime↔protocol gate is the kernel factory at session start.
- **Surfaces that already dumb-render `runtimes`:** `ModelSection.tsx` default-config
  picker; `onboarding/ConnectStep.tsx`.
- **Surfaces that re-derive (to be converted):** `AgentModelPicker.tsx`,
  `ConversationsHomePage.tsx`, `ProjectDetailPage.tsx`, `ConversationPage.tsx`
  (all via `useComposerProviders`); `ModelSection.tsx` runtime-switch guard and
  "可用于" badge (via `isProviderRuntimeCompatible`/`compatibleRuntimes`).

## 3. Design

### 3.1 Materialize `runtimes` on every model surface

`LLMModel.runtimes` is the wire field already declared in
`modules/providers/schemas.py` and `packages/shared/src/types/provider.ts`
(`string[] | null`). Today `_row_to_list_item` / `_row_to_detail`
(`modules/providers/service.py`) deliberately leave it `None` on user/builtin
rows and rely on the picker to derive. Change them to fill it from the one rule:

```python
compatible = _derive_compatible_protocols(row)
ch_runtimes = tuple(runtimes_for(compatible, provider_kind=row.provider_kind))
models = [
    LLMModel(id=m.id, label=m.label, runtimes=(m.runtimes or ch_runtimes))
    for m in _resolve_models(row)
]
```

- A per-model `runtimes` (declared by a contributor) still wins; only `None` is
  filled. So `build_model_options` is unchanged (`m.runtimes` is now non-`None`
  with the same value it would have derived), and `provider_resolver` is
  unchanged (it reads `model.runtimes` only for *contributed* rows and only when
  the UI omits `request_runtime_id`; DB rows still resolve via
  `derive_runtime_provider`). **Session resolution is untouched — this is
  display/filter only.**
- `GET /v1/providers` (list + get) now carries authoritative `runtimes`, so every
  frontend surface can read it without a second endpoint.

**Elegant handling of the `default_model`-only channel.** A channel can resolve to
zero `models[]` yet carry a seeded `default_model` (legacy api-key rows). Today
the composer surfaces such a channel through a `default_model` fallback row. To
keep that working *and* delete the frontend fallback branch, `_resolve_models`
emits, when it would otherwise be empty but `row.default_model` is set, a single
synthetic model row carrying `ch_runtimes`. List, detail, and `model-options`
then all agree, and every model row always has a truthful `runtimes`.

This is **additive**: the field and its `null` semantics already exist; a client
that still reads the `null` branch keeps working.

### 3.2 Frontend: dumb-render `runtimes`, one checkpoint

Collapse the two client-side re-derivations onto the single backend field:

- `use-composer-providers.ts:useComposerProviders` — filter at the **model** level
  by `m.runtimes?.includes(runtimeFilter)`. Delete `canDriveAny` /
  `canDriveAnthropic` / `CODEX_PROTOCOLS` / `DEEPAGENTS_PROTOCOLS` and the
  subscription-kind runtime logic (subscription exclusion is already encoded by
  `runtimes_for`). Keep `providerHasUsableCredentials` (a credential gate, not a
  runtime gate). The `default_model` fallback branch is removed (§3.1 guarantees a
  model row exists whenever a runnable model does).
- `runtime-compat.ts` — reimplement `isProviderRuntimeCompatible` /
  `compatibleRuntimes` as the union of `provider.models[].runtimes`; widen
  `CompatProvider` to include `models`. Delete `speaksAnyProtocolFrom` and the
  compatibility use of `ALLOWED_PROTOCOLS_BY_RUNTIME`. Consumers
  (`ModelSection.tsx` runtime-switch guard + badge) keep their call signatures.
- `runtime-protocols.ts` — **retained** for the New-Session / Edit-Capabilities
  **protocol-selection dropdowns** (`defaultProtocolFor` / `isProtocolAllowed`),
  which choose which wire to *configure*. It is no longer referenced by
  compatibility filtering.

Result: composer, agent picker, badge, default-config, and onboarding all read
the same backend field; there is one place (`runtimes_for`) to change when a
runtime is added.

### 3.3 Runtime availability is reported by the kernel

Availability ("can this runtime actually launch?") is a property of the process
that runs runtimes — the kernel — which already owns binary resolution
(`runtimes/codex/runtime.py:_resolve_codex_bin`). Move the probe there and read it
through the existing `KernelClient` seam (`adapters/kernel_client.py`), which
"mirrors the kernel HTTP API one-to-one" and swaps `InProcessKernelClient`
(bundled desktop) for a future `HttpKernelClient` (cloud sandbox) behind one
protocol.

- **Kernel:** add a `runtime_availability()` capability (and the route the client
  mirrors) that returns `{runtime_id: (available, reason)}`, computed from the
  kernel's own binary probe (the same resolution order the runtime uses to
  launch: `CODEX_BIN_OVERRIDE` → bundled `codex_cli_bin` → PATH). The kernel is
  the single owner of "can I launch codex".
- **`KernelClient` protocol + `InProcessKernelClient`:** add `runtime_availability()`.
  In-process, it probes the local host — identical to today's bundled-desktop
  behavior.
- **Host:** `GET /v1/runtimes` (and any other availability reader) calls
  `kernel_client.runtime_availability()` instead of the local
  `runtime_registry.is_runtime_available`. `RUNTIME_REGISTRY` keeps only static
  metadata (display name, `supported_protocols`, `requires_binary`); the live
  availability answer comes from the kernel.
- **Local vs cloud, one path:** in-process kernel → probes the local host; sandbox
  kernel via `HttpKernelClient` → probes the sandbox (the correct host). Because
  the cloud kernel image ships codex (`backend/pyproject.toml` installs
  `openai-codex-cli-bin` on all platforms), the sandbox answers "codex available"
  truthfully — no static config, no overlay port.
- **Picker-time reachability:** availability is an image property, not a session's;
  the kernel answers without a session. If a warm kernel isn't guaranteed at
  picker time in a pooled deployment, cache the answer per sandbox-image digest —
  still kernel-sourced, just memoized.

Net: `is_runtime_available` as an API-host probe is retired; the kernel is the
single source for availability, consistent local and remote.

## 4. Contract impact (`contracts/COMPATIBILITY.md`)

| Change | Class | Note |
|---|---|---|
| `GET /v1/providers` list+get now populate `LLMModel.runtimes` | evolving (additive) | field + `null` semantics pre-exist; old clients read the `null` branch |
| `KernelClient.runtime_availability()` + mirrored kernel route | new / stable | in-process default = current local probe; `GET /v1/runtimes` shape unchanged |
| `LLMProvider.list/resolve`, `RUNTIME_REGISTRY` (metadata), kernel `ALLOWED_PROTOCOLS_BY_RUNTIME` | unchanged | — |

`is_runtime_available` moves kernel-side; `supports_protocol` may be deleted or
marked deprecated (no production caller).

## 5. Change list

Kernel:
- `src/runtimes/factory.py` (or a small capability module) — `runtime_availability()` probe.
- kernel HTTP route the client mirrors (alongside the existing surface).

Backend (host):
- `modules/providers/service.py` — fill `runtimes` in `_row_to_list_item` / `_row_to_detail`; synthesize the `default_model`-only model row in `_resolve_models`.
- `adapters/kernel_client.py` — add `runtime_availability()` to the `KernelClient` protocol + `InProcessKernelClient`.
- `api/routes/runtimes.py` — read availability via the kernel client; `RUNTIME_REGISTRY` keeps static metadata only.

Frontend:
- `packages/core/src/hooks/use-composer-providers.ts` — model-level `runtimes` filter; drop protocol re-derivation and the `default_model` fallback.
- `packages/core/src/api/runtime-compat.ts` — union of `models[].runtimes`; drop protocol re-derivation.
- (`packages/core/src/api/runtime-protocols.ts` — unchanged; scope narrowed to protocol dropdowns.)

## 6. Migration / sequencing

1. Land 3.1 + 3.2 together (backend fill + frontend consume) — self-consistent,
   additive on the wire.
2. Land 3.3 (kernel probe + client method + route the host through it) —
   `InProcessKernelClient` keeps local behavior identical; the cloud win lands
   when `HttpKernelClient` is used.
3. Contract regression (`make test-contract`) green before publishing.

3.2 depends on 3.1 (frontend needs the populated field). 3.3 is independent.

## 7. Testing

- `_row_to_list_item` / `_row_to_detail` populate `runtimes` for the
  `anthropic` / `openai-completion` / `openai-response` / dual-protocol / and both
  subscription kinds (codex-subscription → `["codex"]`, no deepagents); the
  `default_model`-only channel yields one synthetic model row with runtimes.
- `InProcessKernelClient.runtime_availability()` returns the local-host truth;
  `GET /v1/runtimes` reflects it (codex available iff the kernel resolves its
  binary), and stays correct when a stubbed `HttpKernelClient` reports a different
  set.
- `useComposerProviders` filters by `m.runtimes` only; subscription exclusion
  still holds (driven by backend runtimes).
- `runtime-compat.compatibleRuntimes` = union of `models[].runtimes`; a tested
  custom `openai-response` provider surfaces the codex badge.

## 8. Downstream (overlay) responsibilities

Kept out of OSS; documented here so the seam's intent is unambiguous:

- A contributing `LLMProvider` that adds gateway/catalog channels declares
  `LLMModel.runtimes` on its rows. In particular, an `openai-response` card meant
  to drive codex declares `runtimes=("codex",)` and
  `compatible_protocols=["openai-response"]` (mirroring how a system-gateway
  Responses card is contributed today). OSS consumes these verbatim.
- Availability needs **no** overlay binding: the cloud kernel runs in the sandbox
  and its image ships codex, so the same `KernelClient.runtime_availability()`
  seam reports the truth for that deployment automatically.

## 9. Non-goals

- No change to the kernel factory as the final runtime↔protocol gate at session
  start.
- No change to `web_search` being force-disabled for non-subscription codex keys
  (kernel-side).
- The protocol-selection UI (`runtime-protocols.ts`) stays a frontend concern; it
  is a *configuration* aid, not a compatibility source.
