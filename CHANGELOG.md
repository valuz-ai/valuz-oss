# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.3] - 2026-07-15

### Added

- **Generative UI** — inline OpenUI via the `generate_ui` MCP tool
  (#517 @hanjixin), token-by-token streaming (#531 @hanjixin), Valuz brand
  theming (#533 @yy83000812), and a shared fixed scratch cwd
  (#518 @hanjixin).
- **Connector group OAuth sharing** — authenticating one connector in a
  catalog group (e.g. Valuz search) automatically installs and authorizes all
  siblings in the same group; refresh-token rotation is serialized per group
  (#551 @St0neWan9).
- **Background tasks** — kernel wake-up turns between and during user turns,
  background-task running strip + idle watcher, runtime eviction yields to live
  background tasks, and bg_task kernel events translated onto the session SSE
  stream (#527 @jiaoqsh).
- **User-level control-plane SSE** — a single `GET /v1/stream` replaces five
  client-side polls; running/finished lists and the created→running bridge are
  stream-driven (#536, #540 @Ready22Race).
- **Notifications** — AskUserQuestion and run failures from non-task
  conversations surface in the notification ledger (#529 @Ready22Race);
  DB-poll SSE replaces the in-memory fan-out for multi-pod safety
  (#552 @Ready22Race).
- **Execution-location bar** — per-entity API base routing seam, origin
  badges, remote project creation, and a composer footer bar
  (#502 @St0neWan9).
- **Sandbox** — scope-aware allocation seam (session/task) + peek-only SSE
  live taps (#554 @Ready22Race); allocator `new_turn` hint
  (#554 @Ready22Race).
- **Marketplace** — sourced from the cloud market index (#494 @St0neWan9)
  with index-endpoint racing + OSS-only direct-source fallback
  (#497 @St0neWan9).
- **DeepAgents** — externalize checkpoint to COS-safe files in-sandbox
  (#524 @Ready22Race).
- **ADR-013** — kernel `/kernel/v1` default, `/_internal` loopback plane,
  market client `/cloud` paths (#519 @St0neWan9).
- **Runtime resource lifecycle hooks** — skill discovery and sync extension
  points (#513 @homeant).
- **Task attention & reliability** — unified notification ledger, task
  attention signals (#512 @Ready22Race).
- **Project composer** — locked location/project chips shown under the
  composer (#522 @St0neWan9).

### Changed

- **Marketplace performance** — failure memo on index client, ModelScope list
  cache, pooled connections (#499 @St0neWan9); endpoint resolved once at boot
  (#498 @St0neWan9); zh copy fixes (#496 @St0neWan9); card/filter/logging
  cleanups (#495 @St0neWan9).
- **Frontend design audit** — design spec violations gated in lint
  (#507 @yy83000812); design spec tags and asset store aligned
  (#510 @yy83000812).
- **Kernel SSE** — server-side `types=` filter on the global event stream
  (#548 @Ready22Race); SSE routes no longer poll `request.is_disconnected()`
  in-loop (#547 @Ready22Race).
- **Conversation polling** — P3 poll-removal + SSE disconnect hardening
  reverted due to runtime regressions; re-landed stream-driven in later PRs
  (#538 @Ready22Race).

### Fixed

- **Conversation reliability** — resume blank-transcript race healed with a
  bounded reconcile (#542 @Ready22Race); deterministic resume via ordered
  merge + hydration gate (#543 @Ready22Race); reconcile burst not re-fired on
  stream reconnects (#544 @Ready22Race); stream continuation segments after a
  mid-turn canonical seal (#546 @Ready22Race); send response must not clobber
  the optimistic running status (#549, #550 @Ready22Race); never fake turn
  completion when reconnects run out (#525 @St0neWan9); incremental transcript
  build + un-stick loading on stale status (#552 @Ready22Race).
- **SSE / streaming** — zombie SSE connections closed so the renderer no
  longer starves on pending requests (#508 @jiaoqsh); terminal task streams
  close instead of living forever (#508 @jiaoqsh); caller abort stays wired
  for streaming responses (#508 @jiaoqsh); control-plane cursor advances only
  on lifecycle events (#537 @Ready22Race).
- **Tasks** — lead attribution corrected on blocked/stopped events;
  skill-creator card surfaces in follow-up chat (#514 @Ready22Race); lead
  `await_members` pull-gap closed (#539 @Ready22Race); await window aligned
  to 600 s with the codex ceiling at 720 s (#541 @Ready22Race).
- **Activity** — backstop-reconcile running-runs list so a stuck "running"
  dot self-heals (#555 @Ready22Race).
- **Codex** — webSearch thread items surfaced as tool_use/tool_result events
  (#545 @jiaoqsh); 120 s MCP tool-call cap lifted for the harness toolkit
  (#541 @Ready22Race).
- **Skills** — same-slug official + user copies coexist in the index
  (#530 @St0neWan9); list refreshed after resource sync (#535 @homeant);
  PermissionError during directory scan caught and skipped
  (#557 @St0neWan9); skills materialized under their frontmatter name
  (#523 @St0neWan9).
- **Kernel** — every kernel SQLite engine hardened with `busy_timeout`
  (#505 @Ready22Race); background wake-up turns survive between and during
  user turns (#527 @jiaoqsh).
- **Sessions** — external connector credentials re-resolved on every turn
  (#522 @St0neWan9).
- **Memory** — review sessions share one fixed scratch cwd
  (#511 @jiaoqsh).
- **App** — stale event deliveries guarded from crossing a session switch
  (#506 @Ready22Race); entry scroll burst no longer yanks an early scroll-up
  (#509 @Ready22Race); footer-bar chip icons follow their text colour
  (#504 @St0neWan9); right panel revealed on footer-bar project pick
  (#503 @St0neWan9); new sessions surfaced in sidebar immediately
  (#505 @Ready22Race); Chromium 6-connections-per-host cap lifted for
  loopback (#505 @Ready22Race).
- **Dev** — dev data dir isolated from the packaged app; dev.sh teardown
  kills scoped (#500 @St0neWan9).
- **Build** — Vite `optimizeDeps.include` guarded against pnpm strict
  hoisting for xlsx (#558 @St0neWan9).
- **Alembic** — duplicate host revision 0020 linearized (#516 @Ready22Race);
  notification host migration linearized (#515 @homeant).
- **Artifacts** — renderers improved and PDF navigation added
  (#556 @zhourongyu).
- **GenUI** — foreground color commented out for readability
  (#520 @hanjixin); unused textAccentPrimary removed from OpenUI theme
  (#532 @hanjixin); all generated chart rows shown (#553 @yy83000812);
  desktop layout fixed (#533 @yy83000812).

### Docs & Chore

- Design docs: Task → Kernel migration design (#534 @Ready22Race);
  event-delivery unification design (#528 @Ready22Race).
- Test infra: env-level home sandbox + real-home leak tripwire
  (#526 @St0neWan9); desktop test isolation (#521 @yy83000812).

## [0.3.2] - 2026-07-11

### Added

- **Project worktrees** — project sessions can run isolated on git worktrees:
  session-level isolation with re-entry self-heal for historical worktree
  sessions (#422 @Ready22Race); worktrees for chat automations, a task-level
  worktree switch (lead + members share one worktree), and a worktree-scoped
  file tree (#429 @Ready22Race); friendly auto-names with a 6-hex suffix
  (#435 @Ready22Race).
- **Marketplace** — a SkillHub marketplace with skills, agent teams, and
  library entrypoints (#459 @zhourongyu), extended with ModelScope connectors
  (#485 @zhourongyu).
- **Local file links** — artifacts and file mentions in a conversation are now
  real, clickable links backed by a `valuz-file://` scheme and a
  `/v1/files/resolve` endpoint: resolver port + endpoint (#455 @Ready22Race),
  session-side file refs + linkify guidance (#456 @Ready22Race), local file
  links in conversation markdown (#457 @hanjixin), and the client-side
  resolver (#458 @Ready22Race) — hardened by a single-source URI codec and
  scheme fixes (#460, #467, #469 @Ready22Race).
- **Plan mode** — plan-mode cards render in conversations, and ExitPlanMode
  approvals work in long sessions (#439 @jiaoqsh).
- **New subscription models** — `claude-sonnet-5` in the Claude subscription
  list (#476, #478 @St0neWan9) and the GPT-5.6 family (sol / terra / luna) in
  the Codex subscription list (#480 @jiaoqsh).
- **Runtime ↔ model compatibility single-sourced** — provider rows now carry
  which runtimes each model supports, with kernel-reported runtime
  availability (#473 @Ready22Race).
- **Edition / overlay extension points** — a `sidebarFooter` slot
  (#443 @St0neWan9), edition-defined custom nav groups with the "project"
  group renamed "main" (#464 @St0neWan9), a connector OAuth access-token read
  seam (#465 @St0neWan9), a deployment-level instructions extension point
  (`InstructionsPort`) wired through task lead/member sessions
  (#474 @jiaoqsh), and owner threaded through provider ports (#449 @homeant).
- **Cloud / sandbox** — config-gated kernel startup (`KERNEL_CONFIG_WAIT`) for
  snapshot sandboxes (#446 @Ready22Race).
- **Desktop** — after a healthy start, the previous version's update package
  is purged (#418 @St0neWan9).

### Changed

- **Skills catalog performance** — the catalog is served from the DB index
  instead of a filesystem rescan (#434 @Ready22Race), no longer blocks the
  event loop (#431 @Ready22Race), reindexes on file-watch — dropping the
  skill-change SSE, with the frontend sharing one skill event stream
  (#471 @Ready22Race, #470 @hanjixin) — and reuses indexed official skills
  (#423 @homeant).
- **Desktop footprint** — the browser engine runs on Electron-as-node so the
  bundled Node.js is dropped (#448 @jiaoqsh), and app.asar is significantly
  smaller (#450 @hanjixin).
- **Frontend request layer** — a shared request cache dedupes polled fetches
  (#461 @hanjixin), with a test enforcing shared API request usage
  (#463 @hanjixin).
- **Host hot paths** — cheap fixes for polled endpoints and idle SSE streams
  (#479 @St0neWan9).
- **UI polish** — ask-question card tags (#421), dropdown menus (#430),
  sidebar + agent breadcrumbs (#436, #437), agent detail width (#438),
  automation detail layout (#451), agent card shadow token (#452), and
  connector/activity spacing (#462) (all @yy83000812).

### Fixed

- **Conversation reliability** — blank/frozen conversation pages are healed by
  reconciling the event stream on unexpected close, in-place conversation
  transitions, and root error/loading fallbacks (#491 @Ready22Race); a
  promoted session no longer skips its history load on reload
  (#483 @Ready22Race); the composer is released when a mode-wrapped turn ends
  (#484 @Ready22Race); follow-up sends route on the reconciled busy state
  (#486 @St0neWan9); new-conversation promotion state is preserved
  (#466 @hanjixin); attach+remove no longer leaves an undeletable empty
  session (#416 @St0neWan9).
- **SSE reconnects** back off exponentially, with a slow retry on 401/403
  (#487 @St0neWan9).
- **Tasks** — user-interrupt semantics, member await-liveness, and
  stopped-task resume (#428 @Ready22Race); `run_session_to_idle` finalizes an
  interrupted session as idle (#475 @St0neWan9).
- **Decision inbox** — reconciled against durable truth so pending questions
  can't vanish (#426 @zhourongyu).
- **Codex runtime** — special tool cards are no longer lost on
  slash-namespaced MCP tool names (#424 @Ready22Race); dotted MCP header/env
  overrides work, and a runtime interruption reports distinctly from a user
  cancel (#482 @Ready22Race).
- **Kernel** — ChatAnthropic no longer falls back to 4096 max_tokens for
  unknown models (#492 @jiaoqsh); the global-instructions preamble applies on
  the raw/no-agent session path (#488 @jiaoqsh).
- **Providers** — no more infinite enable loop on legacy seeded subscription
  rows (#420 @zhourongyu).
- **Skills** — builtin skills materialize into a per-user official-skills dir
  on remote sandboxes (#419 @Ready22Race).
- **Activity** — deleting a session removes it from the feed, no ghost rows
  (#417 @St0neWan9).
- **Agents** — AgentDetailView no longer crashes outside a project outlet
  (#440 @jiaoqsh).
- **Desktop / packaging** — startup no longer hard-fails at a fixed
  health-check deadline (#444 @St0neWan9); the kernel package ships in the
  backend distribution (#441 @homeant).
- **Sidebar** — the Chats section header stays visible with no history
  (#447 @St0neWan9).
- **Connectors** — detail panel rendering fixed (#427 @yy83000812).

### Docs & Chore

- Design docs: codex worktree survey + friendly worktree auto-names
  (#432 @Ready22Race), file address resolution (#454 @Ready22Race), and
  runtime/model compatibility single-sourcing (#468, #472 @Ready22Race).
- Deps: openai-codex bumped to 0.1.0b3 so codex installs natively on glibc
  Linux (#442 @Ready22Race); only `openai-codex` is maintained as the
  dependency, with the cli-bin prerelease hint moved to a uv constraint
  (#481 @jiaoqsh).

## [0.3.1] - 2026-07-03

### Added

- **Composer file input** — paste any file into the composer, not just images,
  alongside the existing drag-and-drop and file picker (#411 @St0neWan9).
- **Multi-tenant / cloud** — a per-owner Decision Inbox live tap for multi-tenant
  kernels (#387 @Ready22Race), `owner_user_id` threaded through the
  workspace-grant seam (#391 @Ready22Race), a config-gated managed-cwd → mount
  rewrite for multi-tenant cloud (#393 @Ready22Race), and file upload into
  cloud-managed projects & knowledge bases (#392 @hanjixin).

### Changed

- **Automation UX** — automation rows open the detail page on click, enabled
  automations sort first, and the detail page's back link returns to wherever you
  came from (a project's panel vs. the Automation list) (#405 @St0neWan9).
- **UI polish** — question-card shadow (#399 @yy83000812), role-tag styling
  (#400 @yy83000812), and meta-badge styling (#402 @yy83000812) normalized.
- Skill staging settings simplified (#410 @homeant).

### Fixed

- **Conversation** — interrupting a turn no longer blanks and reloads the
  transcript, preserves partial content, and shows a quieter cancelled hint
  (#388 @St0neWan9); a cancelled chat now reads one consistent "stopped" status
  (#389 @St0neWan9); the new-chat welcome no longer flashes while a conversation
  loads (#390 @St0neWan9).
- **Custom model channels** — editing a channel's endpoint/key now actually saves
  the new values (they were being reverted mid-edit), and the connection test is
  opt-in rather than an un-bypassable save gate (#406 @St0neWan9).
- **Composer / task detail** — the drag-to-upload highlight matches the input box
  (#414 @St0neWan9); long unbreakable event text (API-error JSON, paths) wraps
  instead of overflowing the task detail column (#407 @St0neWan9); the update
  toast keeps the version number visible (#394 @St0neWan9).
- **Tasks** — a task-event member agent name no longer occasionally shows as its
  slug or blank (#401 @Ready22Race).
- **Skills (owner scoping & migration)** — skill slug uniqueness scoped by user
  (#398 @homeant), skill filesystem roots scoped by owner (#403 @homeant),
  duplicate skill index slugs repaired during migration (#409 @homeant), and
  import previews persisted in a user temp dir (#412 @homeant).
- **Backend** — startup user-content initialization gated (#395 @homeant),
  managed project roots stored as absolute paths (#396 @homeant), and internal
  sub-apps (DataService + MCP) mounted under each `api_prefix` (#404 @Ready22Race).

### Docs & Chore

- Added a user temp-dir env example to the backend docs (#413 @homeant).
- Removed the Go tool-version pin (#386 @homeant).

## [0.3.0] - 2026-07-02

### Added

- **Multi-tenant / SaaS foundation.** A large body of work makes the host ready
  to serve many owners from one process: a new **DataService** durable data
  layer — the local `kernel.db` write-through to the host `valuz.db`, or a remote
  HTTP data service; env-driven and always-on (#354 @Ready22Race) — plus a
  host-wide `list_all_sessions` over the durable DataReader (#371 @Ready22Race),
  per-user kernel allocation through a `SandboxAllocatorPort` seam
  (#370 @Ready22Race), multi-tenant provisioning + authorization seams
  (#359 @Ready22Race), owner-parametrized data-service wiring + a per-owner
  verifier (#365 @Ready22Race), per-owner auth for the built-in MCP callbacks
  (#374 @Ready22Race), and an owner-scoped decision-inbox aggregator
  (#380 @Ready22Race).
- **Project export / import.** Projects export and import as a portable archive
  (#320 @hanjixin); agent and project export are unified into one `.valuzpack`
  format (#321 @St0neWan9); `ResourceLibrary` gains project + automation kinds
  (#327 @hanjixin).
- **Artifact file viewer** — open a generated artifact in an in-app viewer
  (#313 @zhourongyu).
- **Automation** — manual triggers with localized cadence and card/panel UX
  (#336 @St0neWan9), a fullscreen instruction editor + "Run now"
  (#339 @St0neWan9), and per-run input / a `get` action / background chat runs
  (#343 @St0neWan9).
- **Activity feed** — a unified cursor-paginated feed shared by the project home
  and the global activity list (#351 @St0neWan9); chats float to the top on new
  messages, in both the feed and the sidebar (#355 @St0neWan9).
- **Cloud / headless projects & KB** — managed cwd/root + multipart file upload
  (#348 @hanjixin), with `directoryFieldMode` plumbed through the Knowledge page
  (#350 @hanjixin).
- **Task detail** — render the `AskUserQuestion` card in the Lead follow-up chat
  (#367 @St0neWan9); open the task detail and conversations on their latest
  content (#378 @St0neWan9); collapse an over-long goal / event text to 20 lines
  (#379 @St0neWan9).

### Changed

- **Explicit owner identity throughout.** User id is now threaded explicitly
  across the host's service boundaries instead of an ambient context — service
  calls (#314 @homeant), background runs (#328 @homeant), the built-in MCP
  context + auth (#329 @homeant), agent slugs (#352 @homeant), knowledge-base
  roots (#353 @homeant), connector slugs (scoped + idempotent per user)
  (#337, #338 @homeant), and user-scoped local filesystem storage
  (#381 @homeant) — with `FsRegistry` as the single read/write data-dir boundary
  (#373 @Ready22Race).
- Provider onboarding + agent cleanup polish (#324 @zhourongyu).
- Project sidebar menu reordered and "open in directory" relabeled
  (#323 @St0neWan9).
- Project home renders its shell immediately and defers non-critical fetches
  (#349 @St0neWan9).
- Task-detail polish + wrap long unbreakable text (#347 @St0neWan9); a cancelled
  turn now shows a quiet grey line instead of an error card (#368 @St0neWan9);
  user messages chip only real skills + leading commands, never `/path` segments
  (#369 @St0neWan9).
- Running task rows read the same "running" label as chat rows in the activity
  feed (#377 @St0neWan9).
- Design pass toward Valuz v2.6 — UI refresh (#315 @yy83000812), button styles
  (#357 @yy83000812), badge styling (#362 @yy83000812), and shadow-1 card
  outlines (#364 @yy83000812).
- Composer attachments + error-card boundaries (#332 @hanjixin).

### Fixed

- **Owner-id threading fixes.** Complete the explicit user-id dependencies
  (#325, #326 @homeant); thread the caller's user id into harness tool service
  calls (#334 @Ready22Race) and `_member_agent_config` (#335 @Ready22Race); make
  the built-in harness tools usable — owner context + reachable endpoint
  (#333 @Ready22Race); thread user_id through `get_project_pack_service`
  (#330 @hanjixin), the parser setup-job routes to fix a 500 (#360 @Ready22Race),
  skill-path resolution on export (#366 @St0neWan9), and requires_action
  enrichment (#376 @St0neWan9).
- Windows upgrade boot crash + sidecar process-tree teardown
  (#319 @Ready22Race).
- Task stop/pause state consistency + RecoveryService extraction and a green
  test gate (#346 @Ready22Race); guard `await_members` against a
  "planned but never dispatched" hang (#372 @Ready22Race).
- Gate local skill indexing (#316 @homeant); skill empty-state translations
  (#344 @hanjixin).
- Preview generated conversation artifacts inline (#317 @zhourongyu); name the
  export download from the dialog's name input (#322 @St0neWan9); contain long
  error-card details (#340 @hanjixin); match the fullscreen instruction editor to
  the dialog size (#342 @St0neWan9).
- Follow-up chat — visible turns, streaming state, uniform composer background
  (#361 @St0neWan9).
- Activity empty-state alignment (#318, #363 @yy83000812).
- Skip schema bootstrap for cloud workers (#345 @homeant); fix quality gates +
  cloud creation flows (#356 @hanjixin); prime the kernel `sys.path` before
  importing `app.schemas` in the data reader (#375 @Ready22Race); support
  templated data + project roots (#383 @homeant); separate the log dir from the
  data dir (#384 @homeant).

### Docs & Chore

- Complete the 0.2.5 changelog (#311 @St0neWan9); require deriving the CHANGELOG
  from git, with English-only notes (#312 @St0neWan9); make artifact delivery an
  explicit strong rule in the handbook (#341 @Ready22Race).
- Fix test signature compatibility after the user-id context merge
  (#331 @homeant); use the corepack pnpm shim for frontend commands
  (#358 @yy83000812).

## [0.2.5] - 2026-06-27

### Added

- Automation runs are now a first-class concept across the app. A dedicated
  automation detail page (`/automations/:id`) shows execution history and the
  rendered instruction side by side; the automation list opens it on row click
  (the inline recent-runs section is gone). The activity overview and project
  detail both gain a 自动化 tab, and the 全部 tabs mark each automation row with
  a 自动化 chip instead of 对话/任务. Trigger cadence is localized (`每 30 分钟`
  / `Every 30 minutes` / `手动` / `Manual`) instead of a raw `1800s`. Project
  detail tabs also group rows by time bucket (今天/昨天/本周/更早), matching the
  activity list. (#307 @St0neWan9)
- Task trigger provenance — each task records and surfaces what spawned it
  (chat / agent / automation), shown both ways with the automation→task link.
  (#300, #297 @Ready22Race)
- Durable session input queue: enqueue follow-ups while a turn is running, and
  "Send now" to interrupt the current turn and dispatch immediately.
  (#284 @jiaoqsh, #299 @jiaoqsh)
- Post-completion follow-up chat with the task lead on the task-detail page.
  (#289 @zhourongyu)
- Automation propose→confirm card for creating automations from chat.
  (#273 @Ready22Race)
- Unified project + global automation action menus. (#295 @St0neWan9)
- Group the agent list by project deployments. (#306 @zhourongyu)
- `update_agent` host-toolkit tool to edit existing agents. (#286 @Ready22Race)
- Host-domain files moved onto an owner-scoped `AssetStore`. (#285 @homeant)
- Custom Responses-API channels can now drive the Codex runtime.
  (#290 @zhourongyu)
- `create_app(api_prefix=)` seam for shared-host path routing. (#301 @homeant)

### Changed

- Flatten the data dir to `~/.valuz-oss` with a safe one-time migration.
  (#276 @Ready22Race)
- Context-panel section heights + list-row menu actions; model-setup composer
  banner and Activity list column width. (#292, #278 @St0neWan9)
- Align the queued-inputs bar width with the composer. (#294 @St0neWan9)

### Fixed

- KB auto-discovery rescans failed on every tick with `OwnerContextUnsetError`:
  the scheduler called `load_routing_config` (owner-scoped settings reads)
  before any owner context was published. Each per-KB iteration now publishes
  the KB's owner on the `current_user_id` ContextVar (try/finally, mirroring the
  automation in-process runner) so the routing-config read and the rescan both
  resolve against the right user. (#308 @St0neWan9)
- Boot crashed with `MissingGreenlet` when `database_url` pointed at Postgres
  via the async driver (`postgresql+asyncpg://`): the kernel/host schema
  preflights built a **sync** engine and called `inspect()` on it, which can't
  drive an async DBAPI. The preflights (`ensure_kernel_schema_migratable` /
  `ensure_host_schema_migratable`) now reflect through an **async** engine and
  run off the event loop in the migration worker thread, so a Postgres DSN
  resolves to asyncpg cleanly. (#303 @homeant)
- Host migration `0007` (skill-library on/off toggle) crashed on Postgres with
  `column "library_enabled" is of type boolean but default expression is of
  type integer`: the `server_default` was a bare `text("1")`, which renders
  `DEFAULT 1` (an integer literal) and is rejected by Postgres' strict boolean
  typing. Switched both the migration and the `SkillIndexRow` model to
  `sa.true()`, which SQLAlchemy renders portably as `1` on SQLite and `true`
  on Postgres. (#304 @homeant)
- Resolve owner per work-item in background scanners instead of an
  ambient/device id. (#302 @homeant)
- Schema preflight never drops — preserve data, fail loud on any non-migratable
  DB; harden the `~/.valuz-oss` data-dir migration (preserve `user_id`, version
  compatibility, skill-reindex resilience). (#282, #280 @Ready22Race)
- Unwrap `ExceptionGroup` so a wrapped transport death stays resumable.
  (#288 @Ready22Race)
- Auto-materialize logged-in subscription channels. (#291 @Ready22Race)
- Present the CLI originator (`codex_exec`) to third-party gateways.
  (#293 @jiaoqsh)
- Spill over-long goal-mode task briefs to a doc; stabilize the internal MCP
  token across restarts. (#298 @Ready22Race)

### Docs & Chore

- Nudge agents to deliver finished files via `deliver_artifacts`.
  (#287 @Ready22Race)
- gitignore the local `.claude/` and `.agents/` agent-harness dirs and remove
  stray design-draft markdown that had been committed by accident. (#309
  @St0neWan9)

## [0.2.4] - 2026-06-25

### Added

- Natural-language agent creation — describe the agent you want in chat and the
  assistant scaffolds it. (#269 @Ready22Race)
- Per-agent skill picker and a global skill-library on/off toggle, plus a faster
  skill rescan. (#264 @Ready22Race)
- `project_instructions` tool and an XML-structured project system prompt.
  (#266 @Ready22Race)
- Built-in `deliver_artifacts` MCP tool and a generated-files section in the
  session panel. (#260 @Ready22Race)
- User-customizable global instructions for the background memory reviewer.
  (#262 @jiaoqsh)

### Changed

- Project home and Activity lists redesigned: a default "All" tab, creation
  time, the status pill at the right edge, and a hover overflow menu
  (rename / delete) on conversation rows. (#274 @St0neWan9)
- Automations panel rows are now editable inline and the create/edit dialog
  layout was polished. (#272 @St0neWan9)
- Agent creation surfaces the available runtimes and configured models and
  validates the runtime/model (brain) pair. (#271 @Ready22Race)
- Subscription models are gated on CLI login (composer detail only).
  (#253 @homeant)
- The skill-library on/off switch is stored as a `valuz_skill_index` column.
  (#265 @Ready22Race)
- Connectors gained dedicated columns for `args` / `oauth_metadata` and a
  separate OAuth table, dropping the ORM relationships. (#256 @homeant)
- Sidebar project / conversation list UX. (#258 @St0neWan9)
- Export the deep component paths consumed by the commercial overlay.
  (#263 @hanjixin)
- Kernel skill-materialize gained a self-diagnosing cycle guard. (f7f36e4)

### Fixed

- Kernel warm-runtime subprocesses (claude / codex) leaked; they are now
  bounded. (#261 @jiaoqsh)
- The automation MCP tool dispatch was broken. (#270 @Ready22Race)
- The i18n locale directory is resolved by a fixed relative path instead of a
  repo-marker walk. (#267 @homeant)
- The Agents page merges injected resource categories. (#259 @hanjixin)
- `submit_skill` resolves its staging directory via the session, not
  `ExecContext.workspace`. (#254 @Ready22Race)
- The Skill Creator mode badge no longer shows a "model" suffix. (#255 @Ready22Race)
- The boot-splash percentage drops its leading-zero padding. (#257 @St0neWan9)

### Docs & Chore

- The handbook emphasizes the automation MCP tool for scheduling.
  (#270 @Ready22Race)

## [0.2.3] - 2026-06-22

### Fixed

- macOS auto-update served the Intel (x86_64) build to Apple Silicon Macs. The
  two separate mac build jobs (arm64 + x64) each published an arch-specific
  `latest-mac.yml` to the release and the later one overwrote the other, so the
  published manifest listed only one architecture's artifacts. electron-updater
  on an Apple Silicon Mac then found no arm64 entry and fell back to the Intel
  build — which ran under Rosetta (slow) and tripped macOS's "Intel app" warning.
  A merge step now combines both arches into a single `latest-mac.yml`, so each
  Mac auto-updates to its native build; Apple Silicon users on the Intel 0.2.2
  update straight to the arm64 build here. (#240 @St0neWan9)

- The packaged desktop client looked for updates at the wrong COS feed prefix:
  `build-desktop.sh` stamped `app-update.yml` with
  `files.valuz.cn/valuz-<edition>/` while CI publishes manifests and artifacts
  under `files.valuz.cn/<edition>/`. Drop the `valuz-` prefix so the baked feed
  URL matches where artifacts land. (#252 @St0neWan9)

### Docs & Chore

- Route the auto-updater's logs to a file via electron-log
  (`~/Library/Logs/Valuz/main.log` on macOS, `%AppData%\Valuz\logs\main.log` on
  Windows). They previously went to the console, which a packaged app discards,
  so whether an update ran as a delta (vs a full download) and why was
  untraceable. (#241 @St0neWan9)

- Fix the Tencent COS publish pipeline for coscli v1.0.8: write the config to
  `~/.cos.yaml` in its schema, drop the unsupported `--force` flag, upload each
  artifact individually (its `--include` filter matched nothing at the release
  root), and check the repo out in the manifest-merge job. (#246 #249 #251
  @St0neWan9)

## [0.2.2] - 2026-06-22

### Features

- Composer agent/model selector and input-box menus redesigned into one
  consistent system: a unified dropdown with a "Default" entry that owns the
  runtime / model / reasoning-effort submenu, a collapsed agent roster, nested
  flyouts capped to a whole-row height at a consistent 12.5px, flyout direction
  that flips away from an open right-hand context panel, and read-only freezing
  in an existing conversation (the agent and its model menu freeze; a Default
  run still changes only the effort). Project conversations show the bound model
  on the agent button. (#223, #224, #225, #226 @St0neWan9)
- Sidebar projects are a multi-open accordion that nests each project's chats /
  tasks, with a separate "Chats" group for project-less conversations.
  (#221, #222 @St0neWan9)
- New-project dialog can deploy an initial team: a "deploy agents" multi-select
  (Valuz Helper pre-selected and listed first) so a project starts with members
  instead of empty. (#227 @St0neWan9)
- Desktop update flow shows a "Restarting…" state on the restart button.
  (#220 @St0neWan9)

### Fixed

- Agent packs: a skill whose slug was stored as a full path (Windows drive
  letters / POSIX absolute paths) no longer breaks `.valuzpack` import with
  "unsafe path in archive" — slugs are sanitized on export and legacy packs are
  rescued on import, with the zip-slip guard intact. (#234 @St0neWan9)
- Sidebar: the conversation list scrolls within the nav, so a long project /
  chat list no longer overflows into and overlaps the pinned resource footer;
  the show-more toggle aligns with its rows and the no-project chats list shows
  more before collapsing. (#233 @St0neWan9)
- Parser: bundle the magika model in the frozen build and record the parse-error
  reason, fixing Windows `.docx` parsing. (#231 @Ready22Race)
- Skills: bump deepagents to >=0.5.5 to fix a `SKILL.md` symlink-loop crash.
  (#228 @Ready22Race)
- Conversation: the AskUserQuestion card renders live — tool cards re-render when
  their streaming input changes. (#229 @jiaoqsh)
- Billing pre-check is channel-aware: `check_budget` takes the session's
  effective `provider_id`, so an empty wallet no longer blocks turns on channels
  it does not meter (a user's own direct API-key channel, org BYOK); the
  duplicated route-level pre-check was folded into the session service.
  (#232 @homeant)
- Desktop: the Windows taskbar / app icon fills its canvas. (#236 @St0neWan9)
- Desktop: the macOS update install hand-off reads as "preparing" instead of a
  second download. (#219 @St0neWan9)
- Build: pick the correct macOS Node archive and add a mirror fallback; retry a
  transient CDN 404 when vendoring Node. (#217, #218 @St0neWan9)

### Docs & Chore

- Frontend tests no longer collect duplicates through `node_modules` symlinks.
  (#230 @jiaoqsh)
- Changelog: English-only labels in the 0.2.1 entry. (#216 @St0neWan9)

## [0.2.1] - 2026-06-18

### Features

- Agent-driven managed browser: ships Node + `chrome-devtools-mcp` for the
  packaged desktop and exposes a friendly `chrome-devtools` CLI, so an agent can
  drive a real browser. (#206 @jiaoqsh)
- Live dynamic-workflow progress surfaced in the conversation for Claude
  multi-agent workflows. (#214 @jiaoqsh)
- Connector connection status surfaced: a red attention dot on the Connectors
  nav when a custom connector is configured but not connected, colored status
  pills (Connected / Connecting / Connection failed / Not connected) on the list
  rows, and the same dot + pills on the agent detail's Connectors tab.
  (#204, #205, #213 @St0neWan9)
- Default Firecrawl connector: swapped into the catalog (Chrome DevTools out);
  the onboarding Valuz Helper ships bound to valuz-search / valuz-stock /
  firecrawl and installs them into the Added group; the connectors list
  collapses to Added / Available. (#213 @St0neWan9)
- Discoverable OAuth is optional: a freemium MCP server (e.g. Firecrawl) that
  advertises OAuth but serves anonymous calls stays auth-free instead of being
  forced into a login. (#211 @homeant)
- ADR-011 `LLMProvider` extension point with backend-owned model labels.
  (#212 @homeant)

### Fixed

- Connectors: a stuck OAuth "connecting" now reads as Not connected; Firecrawl's
  transient anonymous 401 is tolerated on the probe and the create-time auth
  check; the onboarding deploy no longer crashes resolving an installed OAuth
  connector; the tool probe is cached per client session instead of
  reconnecting on every re-select. (#213 @St0neWan9)
- Connectors: thread `user_id` through the connectors MCP tools for multi-user.
  (#209 @St0neWan9)
- Activity overview: isolate per-session failures so one bad run can't blank the
  overview, and fix a TodoItem snapshot crash. (#207, #208 @jiaoqsh)
- DeepAgents: apply the recursion limit to the main graph, not just subagents.
  (#207, #210 @jiaoqsh)
- Desktop: keep failed automatic update-checks silent. (#203 @St0neWan9)

## [0.2.0] - 2026-06-17

### Features

- Scoped agent memory: a global + per-project memory with background
  auto-extraction, a task-finish trigger that graduates multi-agent lessons to
  project memory, and a per-target char budget surfaced in the review prompt.
  (#132, #138, #139 @jiaoqsh)
- Agent Pack format: portable teams with import/export, layered over a new
  `ResourceLibrary` facade. (#151, #135 @zhourongyu @homeant)
- Virtual built-in channels: no boot seed — built-ins are surfaced as templates
  and materialized on configure; platform "system" channels are hidden from
  Settings → Models. (#159, #160 @homeant)
- Kernel store split into its own `kernel.db` (sandbox-mountable). (#163 @Ready22Race)
- Local OCR upgraded to PP-OCRv6_medium (PaddlePaddle official ONNX). (#150 @Ready22Race)
- Conversation: render Codex `apply_patch` as a turn diff card. (#142 @Ready22Race)
- Skills: manual rescan button + periodic auto-scan. (#171 @Ready22Race)
- Automation: show live task status in the execution log instead of a frozen
  "success". (#157 @Ready22Race)
- Connectors: self-heal expired OAuth connectors (silent refresh), re-authorize
  only on hard failure. (#173 @homeant)
- Backend errors can carry an i18n key, rendered on send. (#145 @homeant)
- Settings: server-resolved model options with dumb-render model pickers. (#188 @homeant)

### Changed

- Migrations run in-place; boot-time table wipes are gone (existing data is
  preserved across upgrades). (#149 @Ready22Race)
- Native menu fully localized and follows the in-app language. (#148 @St0neWan9)
- Unified delete dialog for project / task / conversation; dialogs no longer
  auto-focus a button. (#144 @St0neWan9)
- Desktop dock icon centered with a lighter shadow. (#136 @St0neWan9)
- Solid buttons (default / destructive) align in height with outline / secondary
  buttons. (#177 @St0neWan9)
- Investment vertical globalized: agent pack rewritten for global equities,
  templates renamed off `china-*`, and the team stripped to a bare roster.
  (#168 @St0neWan9, #164 @zhourongyu)
- Tidier skill-library group labels and origin badges. (#175 @Ready22Race)

### Fixed

- Tasks: graceful actor-loop drain on shutdown (no finalize race); subprocess
  death is classified as resumable "interrupted", not a failure. (#133, #140 @Ready22Race)
- Skills: stale SKILL.md frontmatter + a detail-panel crash; deterministic
  boot-time indexing of bundled official skills; Windows directory junctions so
  materialization needs no admin; auto-scan runs once on start; stop
  valuz-project-docs from pre-empting local file reads. (#167, #169, #147, #176,
  #179 @St0neWan9 @Ready22Race @jiaoqsh)
- Providers: materialize a built-in subscription on set-default; stop warning on
  the healthy OAuth-subscription resolve path. (#165, #141 @St0neWan9 @Ready22Race)
- CLI login: run `/login` against the resolved binary; auto-refresh the
  subscription login badge after a terminal login; restore CLI detection +
  login guidance in onboarding. (#134, #155, #158 @hanjixin @St0neWan9 @zhourongyu)
- Automation: real kickoff duration + readable summary; cleaner task-kickoff run
  display. (#143, #154 @Ready22Race)
- Desktop: register `set_menu_locale` before creating the window. (#156 @Ready22Race)
- Bundled MCP connectors + onboarding channel UX. (#162 @zhourongyu)
- Task detail: open project files from the panel. (#178 @St0neWan9)
- Agent template dialog no longer auto-focuses its close button. (#166 @St0neWan9)
- DB: commit lock-retry is state-preserving; kernel event-loop no longer freezes
  in dev-sandbox. (#174, #161 @Ready22Race)
- `claude` runtime: treat `ResultMessage(is_error=True)` as an error, not
  end-turn. (#181 @jiaoqsh)
- Build: enforce `+x` on the Linux AppImage; pin `executableName` so the Linux
  deb ships a valid icon; pin the Linux arm64 runner to ubuntu-22.04-arm; ship a
  full hicolor icon set for the Linux deb. (#172, #180, #170, #186 @hanjixin)
- Desktop: the installed app is named "Valuz" on all platforms (the macOS bundle
  was "valuz-oss"). (#194 @St0neWan9)
- Tasks: mark API-errored sessions failed/recoverable instead of completed;
  anchor the live task card to the lead session's task id. (#183, #184 @Ready22Race)
- Onboarding: seed the Valuz helper on skip so the workspace is never empty. (#189 @zhourongyu)
- Model picker: restore display names for subscription / built-in models. (#190 @homeant)
- Updater toast is draggable and shows download progress instantly. (#191 @St0neWan9)
- Desktop: align the splash window controls with the TopBar on Linux/Windows. (#187 @hanjixin)

### Docs & Chore

- Memory subsystem Tier-1 hygiene cleanup. (#137 @jiaoqsh)
- Purge stale "0-migration / full-wipe" wording from the migration docs. (#152 @Ready22Race)
- CI: update the macOS runner version in the release workflow (fixes the x86_64
  build). (#185 @hanjixin)

## [0.1.7] - 2026-06-15

### Features

- Multi-user: owner-scope every read and stamp every write with an explicit user
  id, across host + kernel. (#101 @homeant)
- Sandbox: pluggable driver registry (overlay-ready seam); degrades to in-process
  execution when no driver is available. (#110 @Ready22Race)
- Automations: choose a chat or project target in the global create dialog. (#117 @Ready22Race)
- Attachments: hand un-parseable images to the runtime directly (native passthrough). (#120 @Ready22Race)

### Changed

- Activity overview polls `/v1/runs` every 10s instead of 2.5s — lighter
  background load for the running-runs badge and Activity page. (#105 @St0neWan9)
- Desktop: refreshed the dock / app-switcher icon — clean anti-aliased squircle,
  macOS-standard corner radius, soft drop shadow. (#130 @St0neWan9)
- Attachments: drop the "由模型识别" native-passthrough hint from the chip and file
  panel. (#128 @Ready22Race)

### Fixed

- Sessions: persist the turn-failure error so the reason survives a reload. (#116 @Ready22Race)
- Conversation: attaching a file no longer deselects the current agent. (#121 @Ready22Race)
- Desktop (Windows): main window no longer opens oversized, and the CLI-login
  terminal opens correctly. (#123 @hanjixin)
- Tasks: re-stamp the in-process MCP token on the task/actor turn path so
  relaunched tasks keep tool access. (#126 @Ready22Race)
- Packaging: bundle the built-in parser `plugins` package in the frozen build. (#124 @Ready22Race)
- Parser: log an actionable root cause when the registry has no `light_local`
  provider. (#125 @Ready22Race)
- KB search: ripgrep `--` must follow the `-e` patterns. (#115 @Ready22Race)
- DB: use a per-loop engine for background-thread `asyncio.run` (asyncpg). (#114 @homeant)
- HTTP kernel transport: implement `list_all_sessions` and seed the owner in
  background threads; quiet cross-owner decision hydration. (#111, #109 @Ready22Race)
- Logging: silence MCP OAuth-discovery 404s and per-connection server churn. (#122 @Ready22Race)
- Version: align the version surface across settings, build, and changelog. (#106 @hanjixin)

### Docs & Chore

- CI(release): assert `latest-*.yml` is generated on all four platform jobs. (#107 @hanjixin)
- API: log request-validation (422) failures with the field and path. (#112 @Ready22Race)
- Kernel: remove the cloud kernel-image build from OSS. (#108 @Ready22Race)
- Comments: monolingual cleanup of recently-added comments; docs-search comment tidy. (#119, #118 @Ready22Race)

## [0.1.6] - 2026-06-13

### Features

- In-app update notice: a compact bottom-left toast that detects, downloads (with
  inline progress), and restarts to apply an update — complements the standalone update
  window. (#71 @St0neWan9)
- Live tool streaming: tool input/output deltas stream into the UI as they arrive. (#77 @Ready22Race)
- Frameless window with custom controls on Windows and Linux. (#81 @hanjixin)
- Kernel sandbox: a minimal sandbox abstraction with a local macOS Seatbelt driver, plus
  no-restart dynamic path mounts via sandbox extensions. (#90, #97 @Ready22Race)
- Sealed host↔kernel seam: event-subscription API, HTTP transport, and boundary
  enforcement; the harness tool surface is unified as a host MCP server. (#85, #86, #91 @Ready22Race)

### Changed

- Native app menu is now localized (File / Edit / View / Window / …) and the Help → website
  link points to valuz.io. (#88 @St0neWan9)
- Centralize port extensions into an Extensions container; single `resolve_user_id` auth
  seam with explicit-identity context; SSE moved over fetch. (#80, #84, #89 @homeant)

### Fixed

- macOS auto-update now ships a zip so the updater can apply it; the Linux release upload
  matches the arch-suffixed `latest-linux-arm64.yml`. (#64, #63 @St0neWan9)
- Onboarding deploys team agents on the user's chosen runtime. (#73 @Ready22Race)
- Skill-creator chain repairs: staging validation, draft-first creation UX, YAML
  frontmatter parsing, and Windows absolute paths. (#68, #69, #70, #78 @Ready22Race)
- SSE: shield per-tick DB reads from client-disconnect cancellation; page
  `list_events_after` under the kernel's 1000-event cap. (#66, #93 @Ready22Race)
- Background tasks: fix silent connector loss and a KB-scheduler `LookupError`. (#92 @Ready22Race)
- `usePlatform()` fails open with a web-capabilities fallback. (#94 @hanjixin)
- Windows / build hardening: disable UPX for valuz-server, don't strip binaries, expand
  PyInstaller hidden imports, add codex to PATH, resolve the CLI-login binary path.
  (#67, #98, #79, #82, #83 @hanjixin)
- Raise the Claude runtime SDK stdout buffer above 1 MB. (#75 @jiaoqsh)
- `dev.sh` backend readiness probe uses the renamed projects endpoint. (#65 @Ready22Race)

### Docs & Chore

- Add GitHub issue / PR templates; translate the UI component spec to English. (#72 @jiaoqsh, #99 @hanjixin)

## [0.1.5] - 2026-06-10

### Features

- Full Windows platform support across the backend, CLI, and frontend — Valuz now builds
  and runs on Windows. (#53 @hanjixin)
- `/compact` is surfaced as a unified compaction event across runtimes. (#45 @jiaoqsh)
- New model channel: `claude-fable-5`, via claude-agent-sdk 0.2.95. (#46 @jiaoqsh)

### Changed

- De-projectized kernel: sessions are now self-sufficient (each embeds its `AgentConfig`
  snapshot + `cwd`) and reached through an API-shaped client seam; the kernel no longer owns
  project/agent tables, and the host owns project↔session scoping. Includes the
  workspace→project rename across the stack. (#50, #51 @Ready22Race)
- Subscription model catalogs are resolved live instead of being snapshotted into provider
  rows. (#47 @jiaoqsh)
- Consolidate the host DB migrations to a single 0001 baseline. (#55 @Ready22Race)
- `BillingPort` / `IdentityResolver` / `ResourceListEnhancer` ports are now async. (#49 @homeant)

### Fixed

- Restore connector→MCP resolution on the session-creation paths. (#55 @Ready22Race)
- Composer: let slash commands pass through the skill picker. (#52 @jiaoqsh)
- Disable the advisor tool behind custom gateways on the Claude runtime. (#48 @jiaoqsh)
- Windows/Linux autostart: add the `launchdPlistPath` helper. (#54 @hanjixin)
- Fix a `NameError` in `detect_system_timezone` (missing `sys` import). (#56 @Ready22Race)
- Add missing third-party packages to the PyInstaller hidden imports. (#57 @hanjixin)

### Docs & Chore

- Windows/macOS release pipeline: move mac/Windows to working runners (`macos-runner-x86`
  for Intel), drop the NSIS portable target, and add a `gh release upload` fallback so
  artifacts always land even when electron-builder refuses to publish to a >2h-old
  release. (#60 @hanjixin)
- Record the tag-driven desktop release process in `CLAUDE.md`. (#59 @St0neWan9)

## [0.1.4] - 2026-06-09

### Features

- Per-conversation model override: after selecting an agent, temporarily switch the
  runtime / model / reasoning effort for that conversation only — the agent itself is
  never modified and the choice is frozen at session creation. (#26 @St0neWan9, #30 @homeant)
- Model display names: dropdowns now show friendly labels (e.g. "Sonnet 4.6") grouped by
  provider, with runtimes filtered by protocol. (#30 @homeant)
- Windows packaging: NSIS installer and portable executable; the manual release workflow
  gains a per-platform selector. (#35 @hanjixin)
- Async conversation attachment upload through the configured parser, carrying the
  attachment's source/parsed paths through the kernel. (#18 @Ready22Race, #19 @jiaoqsh)
- Automations: user-selectable scheduling timezone (executed in UTC under the hood). (#13 @Ready22Race)
- Capabilities registry that gates the model-channel configure entries per edition. (#32 @homeant)
- `user_id` ownership column on all business tables (OSS resolves it to a device-derived
  local install id). (#33 @Ready22Race)

### Changed

- Settings → Model: show friendly model display names (e.g. "Sonnet 4.6") in the
  default-model picker and drop the redundant provider label beside it. (#43 @St0neWan9)
- Unified model and reasoning-effort dropdowns; pin the Valuz Assistant (小助手) to the
  top of the new-conversation agent list. (#25 @St0neWan9)
- Hardened GitHub skill import: bare/slash repo URLs, multi-select import, caps,
  provenance, and token handling. (#15 @Ready22Race)
- Refreshed the welcome-screen hero subtitle and privacy footnote copy. (#31 @St0neWan9)
- UI polish across onboarding, task-detail spacing/deliverables, and model-settings
  layout. (#34 @hanjixin)

### Fixed

- Packaged app: connector OAuth callback now targets the sidecar's actual port instead of a
  hardcoded :8000, fixing the ERR_CONNECTION_REFUSED on the redirect. (#42 @St0neWan9)
- Packaged app: onboarding's "enter example project" failed with a 500 because the frozen
  backend couldn't locate its i18n locale catalogs (raised "Cannot locate repo root"). The
  catalogs are now bundled and loaded from the bundle. (#39 @St0neWan9)
- Packaged app: the loader logo now renders, using a relative logo path. (2d97163 @St0neWan9)
- Windows release build: install pip-licenses into the backend venv instead of a flaky
  ephemeral overlay, fixing the third-party-notices step that aborted the build. (#38 @St0neWan9)
- Onboarding and startup screens are now draggable on the frameless desktop window. (#24 @St0neWan9)
- Offload local document parsing to a separate process so it no longer blocks the event
  loop; further event-loop and attachment-display follow-ups. (#27, #21 @Ready22Race)
- Keep opencv-python installable on Intel macOS (x86_64). (#28 @Ready22Race)
- Windows cross-platform build fixes: split Unix-only syscalls into platform-specific
  files; CI runner corrections. (#35 @hanjixin, #14 @hanjixin)
- Remove an incorrect translation on the delete-project confirmation, plus minor text
  fixes. (#16, #17 @hanjixin)

### Docs & Chore

- Add LICENSE (Apache 2.0 + additional terms) and bundle third-party license notices in
  the desktop app. (#22 @St0neWan9)
- Retire the kernel read-only / vendored model in the docs. (#20 @jiaoqsh)
- Rename the single-writer lock file, drop a dead helper, and correct the rationale. (#29 @Ready22Race)
- CI: Node.js 25 with dependency caching. (#14 @hanjixin)

[0.2.1]: https://github.com/valuz-ai/valuz-oss/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/valuz-ai/valuz-oss/compare/v0.1.7...v0.2.0
[0.1.7]: https://github.com/valuz-ai/valuz-oss/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/valuz-ai/valuz-oss/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/valuz-ai/valuz-oss/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/valuz-ai/valuz-oss/compare/v0.1.2...v0.1.4
