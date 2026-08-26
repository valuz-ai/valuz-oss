# File Address Resolution — Local/Remote-agnostic File Access

> Status: **Shipped** (2026-07). Amends [`artifact-file-viewer.md`](artifact-file-viewer.md).
>
> §9 below is annotated with what actually landed: three of its frontend bullets
> describe behaviour the shipped code does not have. Corrected in place rather
> than deleted, so the gap between plan and code stays visible.
>
> One-line direction: **the frontend never opens a file by assuming its path is on
> the local machine.** A file's identity is a `valuz-file://<absolute-path>` URI.
> When it is opened/rendered, the frontend exchanges that URI at a single backend
> resolver endpoint for an **access-address descriptor** — a local absolute path
> (bundled desktop) or a presigned object-storage URL (cloud deployment). The
> backend **never proxies file bytes**; the client fetches from the returned
> address.

This is the OSS-side contract. A commercial overlay binds the storage-specific
implementation (e.g. COS presigned URLs); OSS ships the port, the default local
implementation, the endpoint, the URI scheme, and the frontend plumbing.

---

## 1. Why

`artifact-file-viewer.md` gave us `ArtifactViewerShell` + a renderer registry +
`ArtifactDescriptor`/`ArtifactContent`. It works when host and kernel share one
filesystem (bundled desktop). It breaks the moment the agent runs in a **cloud
sandbox with the workspace mounted from object storage**:

- File trees / artifacts / diff cards / prose file paths all assume the path is
  reachable on the user's machine. In cloud, a path like
  `/data/valuz_data/workspace/<owner>/proj/report.pdf` does not exist on the
  user's laptop → `revealInFinder` and any local read **silently fail**.
- Models frequently *mention* an output file in prose ("wrote `reports/q3.pdf`")
  without calling a tool. That text is currently inert; it should be a clickable,
  openable link regardless of where the file physically lives.

`artifact-file-viewer.md` §5.2 says *"for local files the backend should normally
return bytes or text through a Valuz API rather than exposing raw filesystem
paths to the browser."* **This document reverses that stance**: returning bytes
through the app process (a) does not scale to cloud object storage
(`storage → backend → client` doubles bandwidth and pins storage traffic on the
app tier) and (b) is unnecessary — a resolved address (local path via a desktop
custom protocol, or a presigned URL) lets the client fetch directly.

---

## 2. Principles

1. **Address, not stream.** The resolver returns an *access address*; it never
   returns file bytes through the app service. Cloud → client fetches object
   storage directly (presigned). Desktop → client reads the local path directly.
2. **Location is transparent to the frontend.** The client renders from the
   descriptor's `kind` + `capabilities`; it does not branch on "am I OSS or an
   overlay" or "local or cloud". Behavior is capability-driven, one code path.
3. **Identity is the absolute path.** A file's identity is
   `valuz-file://<absolute-path>`. The absolute path is the common currency of
   local and cloud sandboxes; the kernel uses absolute paths in both. Differences
   appear only at resolve time.
4. **Abstraction in OSS, storage impl by deployment.** OSS defines the port and a
   local default; deployments bind a storage-specific resolver.
5. **One resolver, all surfaces.** Trees, artifacts, prose links, and diff cards
   all funnel through the same endpoint.

---

## 3. URI scheme

```
valuz-file://<absolute_path>
```

Canonical form is three slashes — `valuz-file:///path/to.md` (`valuz-file:` +
empty authority + absolute path, which already starts with `/`), matching
standard `file://`. Examples:

- Cloud sandbox: `valuz-file:///data/valuz_data/workspace/u_42/proj/reports/q3.pdf`
- Desktop: `valuz-file:///Users/u/MyProject/reports/q3.pdf`
- Windows: `valuz-file:///C:/Users/u/MyProject/report.pdf`

The URI encodes **only** the absolute path — never a storage key, bucket, or
mount detail. Location is decided by whichever backend resolves it. This means the
same link in a persisted message stays valid whether the project later lives
locally or in the cloud.

Owner isolation rides on the path prefix: in cloud, all of an owner's data lives
under `.../workspace/<owner>/`, so validating a bare absolute path is one prefix
match (§6).

---

## 4. Descriptor (resolver output)

```jsonc
{
  "ref": "valuz-file:///data/valuz_data/workspace/u_42/proj/reports/q3.pdf",
  "kind": "local" | "remote",     // = the resolving backend's deployment form
  "abs_path": "/Users/u/proj/reports/q3.pdf", // kind=local; null when remote
  "url": "https://…?<signature>",              // kind=remote (presigned GET)
  "expires_at": 1720500000,                    // kind=remote
  "name": "q3.pdf",
  "mime_type": "application/pdf",
  "size": 10240,
  "exists": true,
  "preview_kind": "pdf",          // reuse existing ArtifactPreviewKind
  "capabilities": {               // reuse existing capability shape
    "can_preview": true,
    "can_download": true,
    "can_open_external": true,    // finalized client-side (see §9)
    "can_copy_content": true
  }
}
```

`preview_kind` / `capabilities` reuse the existing detection in
`modules/projects/service.py`. The descriptor carries **no project_id / rel_path**
— identity is the absolute-path `ref`.

This replaces the fetch role of `ArtifactContent` in `artifact-file-viewer.md`
§5.2: renderers no longer receive `content` from a backend read endpoint; they
receive a descriptor and fetch bytes/text themselves from the resolved address
(desktop custom protocol for `kind=local`, `fetch(url)` for `kind=remote`).

---

## 5. Port: `FileAddressResolverPort`

New `backend/valuz_agent/ports/file_address.py` (mirrors `ports/billing.py`):

```python
from pathlib import Path
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class ResolvedAddress:
    kind: str                       # "local" | "remote"
    abs_path: Path | None = None    # kind=local
    url: str | None = None          # kind=remote (presigned)
    expires_at: int | None = None   # kind=remote

class FileAddressResolverPort(Protocol):
    async def to_address(self, *, owner_user_id: str, abs_path: Path) -> ResolvedAddress: ...
    # raise PermissionError if abs_path is not owned by owner_user_id

class LocalFileAddressResolver:
    """OSS default: bundled/local deployment returns the absolute path as-is."""
    async def to_address(self, *, owner_user_id, abs_path) -> ResolvedAddress:
        return ResolvedAddress(kind="local", abs_path=abs_path)

def get_file_address_resolver() -> FileAddressResolverPort:
    from valuz_agent.ports.extensions import ext
    return ext.file_address_resolver

def set_file_address_resolver(port: FileAddressResolverPort) -> None:
    from valuz_agent.ports.extensions import ext
    ext.file_address_resolver = port
```

- `ports/extensions.py`: `self.file_address_resolver: FileAddressResolverPort = LocalFileAddressResolver()`
- `ports/__init__.py`: export `FileAddressResolverPort`, `ResolvedAddress`, `get_/set_file_address_resolver`.

> **Not `WorkspaceHandle`.** `WorkspaceHandle` is byte-level workspace IO
> (`read_bytes`/`write_bytes`), reserved for a future kernel-file-API remote
> handle. "abs_path → access address" is orthogonal. The local resolver may reuse
> `fs_registry` for path joins, but not `WorkspaceHandle` for IO.

---

## 6. Endpoint: `/v1/files/resolve`

New `api/routes/files.py`, prefix `/v1/files`:

```python
@router.post("/resolve")
async def resolve_files(
    body: ResolveRequest,                        # { refs: list[str] of valuz-file:///abs }
    user_id: str = Depends(get_current_user_id),
) -> ResolveResponse:                            # { results: list[descriptor] }
    out = []
    for ref in body.refs:
        abs_path = parse_valuz_file_uri(ref)
        try:
            assert_owned(user_id, abs_path)      # owner-boundary + symlink guard
            meta = stat_meta(abs_path)           # size/mime/preview_kind/capabilities/exists
            addr = await get_file_address_resolver().to_address(
                owner_user_id=user_id, abs_path=abs_path)
            out.append(descriptor(ref, addr, meta))
        except PermissionError:
            out.append(forbidden_descriptor(ref))  # do not leak existence
    return ResolveResponse(results=out)
```

- **Batch**: prose may contain several file links; batching is one round-trip and
  lets the frontend cache.
- **`assert_owned(user_id, abs_path)`**: `abs_path.resolve()` first (defeat
  symlink escape), then require it under one of `owner_allowed_roots(user_id)` — a
  **configurable prefix list**:
  - Cloud: `fs_registry.project_root(user_id)` (`VALUZ_USER_PROJECT_ROOT` with the
    `{user_id}` template resolves to `.../workspace/{user_id}`). One `startswith`
    is the multi-tenant isolation line. Skills/config prefixes are **not** opened
    preemptively; add explicitly when needed.
  - Bundled desktop: single user; allowed roots = that user's project `root_path`s
    (DB) ∪ managed `project_root`.
- **Auth** reuses `Depends(get_current_user_id)`; owner is passed explicitly into
  `assert_owned` / `to_address(owner_user_id=…)` — never read from ambient context.
- Optional `GET /v1/files/resolve?ref=…` convenience form.
- `exists=false` does not block rendering; the click surfaces a toast.

---

## 7. Remove the byte/content endpoints (no transition)

The following in `api/routes/projects.py` return file content through the app
process and are **removed in the same change** that lands the resolver + frontend:

- `GET /{project_id}/raw-files/{file_path}` (`FileResponse` streams bytes)
- the inline-`content` behavior of `GET /{project_id}/files/{file_path}` — its
  metadata role is subsumed by the resolver descriptor.

Replacement: content is fetched by the client from the resolved address
(`valuz-local://` on desktop, presigned URL in cloud). This is a **breaking API
change** and must be recorded in the extension-contract / OpenAPI.

**Preconditions before removal:**
- Ship together with the frontend that fetches content from addresses.
- Full regression green: text/code/markdown, image, pdf, office, large-file
  truncation, cross-owner 403 — across **bundled desktop × cloud**.
- Confirm no remaining callers (stale UI, e2e, download entry points).

Scope: project-file endpoints only. Skills/KB content endpoints migrate when
their trees adopt the resolver.

---

## 8. Trees, artifacts, prose

- **Trees** stay backend-recursive metadata (name/type/size/children) — not
  streams. The project `FileNode` currently carries only `name`; the frontend can
  accumulate `cwd + level names` to build the absolute path, then form
  `valuz-file://<abs>` on click. Optionally add an `abs_path` field to
  `_node_to_dict` (additive, backward-compatible). Skills `SkillFileNode` already
  carries a relative `path`.
- **`deliver_artifacts`** needs **no DB change**. It already stores the absolute
  `file_path` in `valuz_session_artifact`. The `GET /v1/sessions/{id}/artifacts`
  response gains a **derived** `ref = "valuz-file://" + file_path` (computed per
  request, not stored). To make prose links reliable, constrain model output:
  - `TOOL_DESCRIPTION` in `modules/sessions/artifacts_tool.py` (a plain constant,
    not i18n): "When you reference a delivered file in your prose, link it as
    `[<fileName>](valuz-file://<the absolute filePath you delivered>)`."
  - Global system prompt via `adapters/system_prompt_builder.py`
    `assemble_session_instructions` (the single append chokepoint): add an
    `("output_format", …)` section stating the same. Base preset stays in kernel;
    only the valuz append changes.
  - The model's absolute path is the kernel/sandbox path (cloud =
    `.../workspace/<owner>/…`); the resolver translates it to a presigned URL at
    open time. **This is where the sandbox→object-storage translation is realized.**

---

## 9. Frontend

- ~~**`useFileResolver()`**: batches `valuz-file://` refs to `POST /v1/files/resolve`
  with in-memory cache/dedupe.~~
  **Not shipped.** The hook existed but had zero callers, and had neither cache,
  dedupe, nor batch-merge (its own docstring said deliberately un-cached, since a
  presigned URL expires). Deleted. The real call path is `useArtifactFile` →
  `filesApi.resolveOne` — **one request per opened file**, and `filesApi.resolve`
  (the batch form) is only ever called with a single ref.
- ~~**`FileLink`**: the single clickable file element…~~
  **Not shipped under that name.** The equivalent is split across
  `MarkdownContent.tsx`'s `<a>` handling, `useConversationLocalFileLinks`, and
  `useArtifactFile` + `resolvedToArtifactFile`. Consequently the round-up of bare
  `revealInFinder(abs_path)` never happened: the diff card, "open in system" and
  the project page still call it with no `canOpenExternal` gate (~8 sites).
- **Markdown linkify** (`MarkdownContent.tsx`, Streamdown): the custom `<a>`
  recognizes `valuz-file://` (**shipped**); a remark step deterministically
  linkifies path-like text in prose/inline-code under the session `cwd` prefix
  (**not shipped** — only links already written as `[label](href)` are rewritten;
  a bare path in prose stays plain text).
  The plan called linkify the reliable path and model output (§8) a bonus.
  **In the shipped code that is reversed**: the §8 output-format instruction is
  currently the only thing that makes a delivered file clickable.
- **Expired addresses**: a `kind=remote` address is short-lived, so a failed load
  can only recover by **re-resolving** — retrying the same URL is guaranteed to
  fail again. Renderers take an `onReload` for this (the PDF retry used to just
  remount the iframe; image/media had no retry at all).
- **`ArtifactViewer` content source swap**: renderers keep the existing registry;
  only the content source changes — instead of a backend content endpoint, fetch
  from the descriptor address (`valuz-local://` read on desktop, `fetch(url)` in
  cloud). A single `src` unifies rendering: `kind=remote` → `url`; `kind=local` +
  Electron → `valuz-local://<abs_path>`.
- **Electron `valuz-local://` custom protocol**: register in the desktop main
  process (`protocol.handle`) to serve local absolute paths to its own renderer
  (client-side, no network, honors principle 1). Lets `<img>/<iframe>/fetch` use a
  URL for local files too — uniform with cloud.
  On the workspace-root check the plan asked for: the shipped handler
  **does not do it**. It trusts the producer instead — `buildLocalFileUrl` is only
  ever called from `resolve-artifact.ts` with an already-resolved (hence
  `assert_owned`-validated) descriptor, never from model or markdown text. No
  exploitable path today, but the defense-in-depth layer this bullet promised
  does not exist; add the check or keep this as a recorded trade-off.

---

## 10. Consistency boundary (important)

"Degradation" does **not** mean frontend behavior diverges between OSS/overlay or
local/cloud. Separate *code/decision logic* from *rendered outcome*:

- **Code & logic: one path, no forks.** The `valuz-file://` → resolve →
  descriptor → render pipeline lives only in OSS. An overlay adds **no** frontend
  rendering branch — it binds `FileAddressResolverPort`, changing the descriptor's
  *content* (`url` vs `abs_path`), never the frontend's *behavior*. Local/cloud is
  one code path taking different `kind` cases.
- **Rendered outcome: consistent for every real combination.**

  | Client × project | descriptor | render | outcome |
  |---|---|---|---|
  | Desktop × local | `kind=local` | `valuz-local://` | full |
  | Desktop × cloud | `kind=remote` | presigned url | full |
  | Browser × cloud | `kind=remote` | presigned url | full |
  | (Browser × local) | `kind=local` | no `valuz-local://` | degraded (metadata + open-in-desktop) |

  The single degraded cell is a **browser platform limitation** (the sandbox
  cannot read a local path), not a design/edition inconsistency — it already
  exists in OSS today (webui vs desktop), and no real product path hits it (local
  projects are served to the desktop app; the browser talks to cloud projects).
  Cloud rendering is 100% identical between browser and Electron. Removing the
  cell would require a localhost file server, violating principle 1.

- **`capabilities` has two sources**: backend-derived (file-intrinsic —
  `preview_kind`, `can_download`, `can_preview`) and client-derived
  (`can_open_external` true only when `kind=local && platform.isElectron`; local
  rendering needs `valuz-local://`). The backend cannot know the client is
  Electron, so the frontend finalizes those via `usePlatform()`. Still uniform
  logic, not an edition branch.

---

## 11. Extension-contract impact

New **stable** surface to freeze (breaks overlays if changed silently):

- `FileAddressResolverPort.to_address(owner_user_id, abs_path) -> ResolvedAddress`
  + `ResolvedAddress` shape.
- `valuz-file://<absolute_path>` scheme (three-slash canonical).
- `POST /v1/files/resolve` request/response (the descriptor).
- Derived `ref` field on the artifacts list response.

Breaking change to record:

- Removal of `raw-files` and the `read_file` content behavior (§7).

Register these in the compatibility/OpenAPI surface alongside this doc.

---

## 12. Non-goals / later

- Presign caching + TTL tuning (default 15 min, no cache; optimize later).
- Large-file/range download, resumable transfer.
- Skills/KB tree adoption of the resolver (adds their prefixes to
  `owner_allowed_roots`).
- Object-storage CORS/IAM hardening (deployment concern, not OSS logic).

---

## 13. Relationship to `artifact-file-viewer.md`

This doc **amends** that one:

- Keeps: `ArtifactViewerShell`, renderer registry, `preview_kind`, capabilities,
  URL state, "one shell many renderers", "never scatter type checks".
- Reverses: §5.2's "return bytes/text through a Valuz API". Content now comes from
  a resolved address the client fetches directly.
- Removes: the byte/content endpoints in §7 of that doc for project files.
- Adds: the resolver port, `valuz-file://` identity, prose linkify, and the
  local/cloud-agnostic rendering path.
