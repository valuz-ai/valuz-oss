# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Artifacts: `skill` kind and a bytes delivery form** — `ArtifactKind.SKILL`
  labels an installable skill package, and `DeliveryRequest.content_bytes`
  records opaque bytes (an archive, an image) that arrive as content rather
  than as a file. Bytes are stored as a file snapshot only, never inline. This
  is the artifacts-side groundwork for versioning skill-creator output; the
  skills module owns the rest.
- **Skill versions** — every skill saved through the library (skill-creator
  confirm, staging-panel sync into the user library) is recorded as a version:
  a deterministic zip of the skill directory delivered as a `kind=skill`
  artifact in a reserved per-user scope under `<data_dir>/skill-versions/`.
  The head's `version_no` is the truth and the host writes it into the
  SKILL.md frontmatter `version:` before saving. Content the library held that
  was never saved through it (hand edits, imports) is captured as a baseline
  version before being overwritten — nothing is destroyed unrecorded.
  `GET /v1/skills/{id}/versions`, `GET …/versions/{rev}/files?path=` and
  `POST …/versions/{rev}/restore` (restore = a new version on top of the
  history). `valuz_skill_index.artifact_id` links a skill folder to its
  lineage (migration 0044). Local backup now includes `skill-versions/`.
- **skill-creator submissions are durable operations** — `submit_skill` now
  proposes a `skill.submit` operation (staged file list, tree hash, how the
  draft collides with the library, the version a save would create) and
  returns the operation envelope; the review card confirms/cancels through
  `/v1/operations/{id}`, so its state survives a refresh. Confirmation
  refuses a draft edited after submission (`OPERATION_STALE`); a slug that
  collides with a library skill the draft was not prepared from needs the
  user's decision (`{"mode": "new_version"}` or `{"mode": "rename",
  "new_slug"}`); cancel removes the staged draft. New `prepare_skill_edit`
  tool seeds staging from a library skill so the save becomes its next
  version; `list_skills` reports scope / version / editability; the
  skill-creator SKILL.md now checks the library before writing. Operations
  gained `decision` on confirm, an optional cancel hook, and run handlers
  with commits deferred (`infra.db.defer_commits`). The legacy
  `/v1/skills/submissions/*` endpoints are deprecated and kept one release.
- **Skill panel proportions** — the skill-creator panel's "saved in this
  conversation" list is a real section (rows with a version badge, one click
  to the skill) instead of a strip of fine print, and its empty state no
  longer says "nothing generated yet" directly under the skills that session
  just saved — saving empties staging, so that is exactly when the two
  contradicted each other. The detail page's version list reads at the same
  scale as the rest of that sidebar.
- **Skill versions in the UI** — the review card now renders from the
  `skill.submit` record, so a reloaded page shows "saved as v2" or
  "discarded" instead of falling back to "waiting for files"; it names the
  version a save would create, and when the slug collides with a library
  skill the draft was not prepared from it asks the user to pick between
  saving as that skill's next version and saving under a new name. The
  skill detail page gains a version list with restore, the skill-creator
  panel lists what this conversation already saved, and saved-skill
  archives no longer appear in a session's generated files (session
  artifacts now carry `kind`).

### Fixed

- **A skill file in any non-Latin script could not be previewed** — the skill
  detail viewer decided "binary" by the share of printable-ASCII characters,
  so a Chinese `SKILL.md` (most of them) scored near zero and rendered as
  `[Binary file - cannot preview]`. The backend already decodes tolerantly and
  always returns a string, so the test is now the density of replacement and
  control characters, i.e. whether the decode produced text.
- **Skill-creator required an agent** — the composer lets a conversation run
  agentless on a picked runtime + model, but the skill-creator launcher
  refused to start without an agent and forwarded only provider/model, so a
  chosen runtime was dropped. It now follows the composer: a named agent is
  bound, an explicit brain runs agentless on exactly that brain, and only a
  launch that chose nothing falls back to the default assistant. The
  capability rides the always-on baseline, so an agentless session authors
  skills fine.
- **Skill version endpoints mounted at the application root** — `routes/skills.py`
  carries no router prefix (every path is written in full), so the three
  version routes' relative paths landed on `/{skill_id}/versions` instead of
  `/v1/skills/{skill_id}/versions`. Because the auth dependency still ran,
  the stray paths answered 401 while the documented ones 404'd. A test now
  pins the mounted paths.
- **Restoring a backup could silently empty every data directory** — the
  pre-restore safety snapshot ran retention pruning into the same destination,
  which could delete the very version being restored; the apply step then
  mirrored the missing payload as an empty directory and reported success.
  The safety snapshot no longer prunes, every restore target is validated
  against the manifest before anything on disk is touched (a missing payload
  fails the whole restore and leaves live data alone), recorded symlinks are
  recreated on restore, and a manifest newer than the app is refused.
- **Backup coverage caught up with the data-dir layout** — the source list was a
  hardcoded four-name tuple that never learned about Agent Plugins
  (`plugins/`, `plugins-data/`), the DeepAgents checkpoint store, DeepSeek
  Harness state, or knowledge-base roots routed by a host resolver. Sources are
  now resolved through `FsRegistry`, exclusions are explicit with a reason, and
  a registry tripwire test fails on any new data directory the backup has not
  taken a position on. First-run preflight sizes the whole payload instead of
  the DBs alone.

## [0.5.1] - 2026-08-28

### Added

- **Plan mode, native on every runtime that has one** — a "Plan mode" toggle in
  the composer, `PATCH /v1/sessions/{session_id}/mode` on the host, `mode` on
  both session DTO shapes, and live `session.mode_changed` reconcile, so a
  runtime-driven exit turns the chip off without a refetch. Claude gets the
  working loop from the host route alone (#1059 @jiaoqsh); codex goes native
  through the app-server's experimental `collaborationMode` — plan turns are
  sticky, forced to a read-only sandbox, and republish the model's proposed
  plan as a pinned plan card whose approve-and-run button PATCHes the mode back
  and sends the execution turn, while `request_user_input` always parks as the
  clarifying-questions card (#1064 #1069 @jiaoqsh); and dsh runs the same loop
  through the first-party plan plugin, where approval continues the *same* turn
  natively instead of restarting it (#1077 @jiaoqsh). deepagents stays a 400 by
  design. Ships `docs/design/session-modes.md`, referenced from six code sites
  but never present in this repo.
- **Playbook detail page, and a page can declare its layout** — a playbook is a
  versioned document that runs on demand, so `/playbooks/:playbookId` is
  deliberately its own page rather than a share of the automation detail; back
  returns to wherever you came from. `AppShell` takes `rightPanelDefaultSize`
  and pages declare `setMasterDetailLayout(true)`, replacing a hardcoded path
  list an overlay edition could never extend. Also: the composer mode toggle
  falls back to icons in a narrow composer, the import dialog stops reloading on
  every render and lets a name clash be resolved by editing the new name, and a
  renamed project publishes at once (#1063 @St0neWan9).
- **Builtin resources are a declaration, not code constants** —
  `BuiltinResourceDeclarationPort` reads a packaged manifest that edition
  overlays merge over, `PluginSource` gains `builtin` with a `deletable` column
  (migration 0042), boot syncs `resources/bundled_plugins/` (new: `office` —
  docx/xlsx/pptx), and seeds plus the agent-pack listing read the port.
  Marketplace index requests now carry `distribution=`, the parameter the index
  actually reads — `channel=` was silently ignored server-side, so every edition
  received the oss composition. OSS behaviour is byte-identical
  (#1082 @St0neWan9).
- **An open preview follows the agent's edits** — a document open in the preview
  pane is a live view, not a snapshot. The turn-end hook refreshed the file tree
  and the artifact list but never the open document, so the agent would rewrite
  the file on screen and the reader kept the old bytes (#1072 @St0neWan9).
- **Copying an agent goes through the create form** — the detail header opened a
  yes/no confirmation instead, and the shared textarea's `field-sizing-content`
  made the instructions box open short for a blank create and tall for a
  seeded copy; `field-sizing-fixed` restores `rows={8}` either way
  (#1081 @St0neWan9).
- **An update download says it is downloading** — every update surface titled
  itself "update available" for the whole download, the one thing the user
  already knows once the bar is moving. The toast, the modal, and the standalone
  update window get a third state, the toast puts the version back in that title
  (row 2 is the progress bar while downloading, so it had nowhere else to go),
  and the download icon moves off a hardcoded blue onto the brand token
  (#1087 @St0neWan9).
- **Clear the project chip without opening its menu** — hovering swaps the
  chevron for a clear action that drops straight back to a temporary
  conversation (#1068 @St0neWan9).

### Changed

- **Operation confirmation cards are one shape** — playbook and automation cards
  align on shared semantic surfaces and standard button actions, the playbook
  prompt renders as markdown in the standard wide dialog behind a compact
  details icon, a failed automation proposal can be retried, legacy proposal
  triggers are normalized instead of rejected, the kernel skips post-run checks
  for confirmation cards, and workspace resources refresh once a confirmation
  resolves (#1037 #1063 @St0neWan9).
- **A self-published marketplace card says what it is** — every item we publish
  ourselves carried an "official" pill, which names a provenance the reader
  cannot act on; the card now names the item type, while anything ingested keeps
  naming the store it came from (#1057 @St0neWan9). The type words are plainer
  and a plugin dialog no longer says "plugin" twice, in a chip and a pill that
  could never say anything else (#1058 @St0neWan9).
- **A large document preview renders a screenful at a time** — cost tracks DOM
  nodes, and a spreadsheet flattened to markdown builds one per cell: 16,000
  cells cost 3,274 ms against 261 ms of prose at equal size. The preview now
  windows (#1071 @Ready22Race).
- **A finished run refreshes only the project that owned it** — the sidebar
  effect was keyed on the whole running set, so one agent working re-read every
  project times the number of execution targets, a dozen `/v1/runs` calls in the
  same tick several times a turn (#1079 @St0neWan9).

### Fixed

- **Desktop updates download the differential again** — the cache purge deleted
  the very file `electron-updater` diffs the next release against
  (`update.zip` / `installer.exe` / `package.7z`), so every update on every
  platform pulled the whole ~600 MB package and MacUpdater logged the fallback
  every single time (#1056 @St0neWan9).
- **An occupied skill name costs one link, not the session** — a real directory
  the harness did not write is left alone by design, but the create that
  followed raised `FileExistsError` and killed every turn of the project, with a
  retry that never worked (#1070 @Ready22Race).
- **An agent slug stays ASCII** — a slug is a machine handle that rides in URL
  segments, dispatch parameters and, since shared agents, an HTTP header, which
  httpx encodes as ASCII. A CJK-named agent's share died on every run with an
  opaque 502; in production 2 of 10 shares carried a non-ASCII slug and both
  were dead (#1062 @Ready22Race).
- **Copying an agent no longer leaks an internal key** — `copy_agent` marked
  provenance with `_source` in the create payload, which was forwarded whole
  into the managed mutation and rejected as an undeclared field, so every copy
  of every agent answered 422 (#1080 @St0neWan9).
- **An agent that holds the whole library says so in `/`** — an all-available
  agent deliberately persists an empty explicit skills list and resolves the
  owner's enabled library at session creation, but the composer read only the
  explicit list, so slash discovery looked empty while direct slash execution
  worked. The member summary now carries the resource policy, and explicit
  agents stay on their bound skills (#1086 @Ready22Race).
- **A project cwd no longer gets a dead `.valuz/root` marker** — nothing in the
  backend, kernel, frontend, or any overlay ever read it, and on a
  user-picked folder it showed up as untracked in `git status` with nothing to
  say what wrote it (#1083 @Ready22Race).
- **The agent library is a union over machines** — fanning out over every
  execution target listed the same account's agents once per target; only
  `device:*` targets hold a library this machine has never seen, and a target
  nobody asked for is no longer announced as degraded (#1067 @St0neWan9).
- **A large knowledge document no longer freezes the tab** — the preview
  endpoint is bounded (a 755 KB spreadsheet parses into 1.05 MB of markdown),
  and the blob on disk deliberately stays whole so `doc_read` never tells the
  agent it read a document it had only seen part of (#1065 @Ready22Race).
- **A document cut at the 5 MiB cap says so** — the citation preview provider
  destructured only `content`, so the reader asserted `truncated: false` and
  presented a truncated document as complete (#1061 @Ready22Race).
- **The knowledge document detail gets its width, its viewer, and its clicks
  back** — the detail asked for the wide side in units the resizable shell no
  longer read (#1066 @Ready22Race); changing the requested panel width remounted
  the panel group, and with it the page inside it, throwing a click on a
  document back to the list of knowledge bases (#1073 @Ready22Race); and the
  windowing had replaced the shared file viewer, taking the preview/source
  toggle, the reading column and the truncation notice with it — the windowing
  now lives inside the viewer, which every artifact markdown preview gets
  (#1076 @Ready22Race).
- **A parse attempt time that is epoch milliseconds renders as a time** — a
  numeric string goes down the date-*string* parser, so the parse history showed
  the raw number next to "parse failed"; both shapes are accepted now
  (#1078 @Ready22Race).

### Docs & Chore

- **The release build passes `--install-links` explicitly** — a tag failed on
  all four platforms with `EUSAGE` because npm read the vendored dsh runtime
  lockfile as out of sync; the lockfile is written in the install-links shape
  and the setting lived only in the vendor dir's `.npmrc`, which npm does not
  always pick up (#1084 @St0neWan9).
- **A scratch perf harness and its build are out of the tree** — #1073 was
  staged with `git add -A` and brought 1,301 files along, one of which
  repointed the web UI's `index.html` at the scratch entry and broke the real
  build (#1075 @Ready22Race).

## [0.5.0] - 2026-08-25

### Added

- **Playbooks** — a reusable, agent-native instruction library. A Playbook is a
  prompt-only Definition with an immutable Version chain and an append-only Run
  history, optionally associated with a Project. The global Playbook library
  groups by project and offers an expandable instruction editor, status
  controls, immutable-history reuse, guarded deletion, and current-project
  management from the right panel; the agent gets full parity through
  list/get/list_versions/create/update/set_status tools. Automations can pin an
  exact Playbook definition version, and the relationship is shown in the API,
  MCP, UI, and tool cards. Ships with reusable Project / Evidence / Playbook
  facades for editions and migration 0038 (#1001 #1002 @St0neWan9).
- **Playbooks are a resource kind** — `playbook` joins `ResourceKind` with
  `list` and `get`, mirroring `automation`, so overlays can sync or share one
  through the facade instead of reaching into `modules.playbooks`. The
  definition's current version is the portable body; the version chain and run
  history stay local (#1003 @St0neWan9).
- **Programmatic Tool Calling (PTC)** — an opt-in code face over the Valuz Data
  connectors on all four runtimes. With the Settings switch on and a qualifying
  data connector in the session, the model can write a program that imports
  generated, typed wrapper functions and chains N tool calls plus computation
  in one `execute_code` run; only the program's stdout and created-file list
  return to the model context, so raw payloads never ride through the
  conversation. Native tool schemas stay untouched and a managed prompt policy
  teaches the dispatch rule. Includes the execute_code kernel with loopback
  proxy and codegen, per-turn convergence, the dsh bridge to kernel ToolDefs
  via `/mcp/toolkit`, a frozen self-exec fallback so the packaged app is its
  own interpreter, and one private `.ptc/{runs,work}` cwd namespace
  (#1049 @jiaoqsh).
- **An attachment belongs to a turn, not a session** — `session_id` is nullable,
  files are filed under the attachment's own id, and a turn names the ids it
  claims. `POST /v1/sessions/reservations` mints an id and writes nothing, so
  uploads and parsing start while the person is still typing and the session
  is created at Send. On a cloud project this removes the ~3.5s sandbox wait
  that used to sit between attaching a file and anything happening
  (#1009 #1012 @Ready22Race).
- **Conversation extension points for overlay editions** — generated-UI actions
  are routed to a host action sink (`registerGenUIActionSink` /
  `dispatchGenUIAction`) so a click inside a rendered workbench no longer dies
  silently (#1004 @St0neWan9); a `conversation.selection-actions` slot floats
  overlay-registered actions over a selected span of an assistant reply, each
  segment carrying `data-assistant-message-id` (#1005 @St0neWan9); and the slot
  context receives `insertDraft` so an action can stage a request in the same
  composer without auto-sending (#1006 @St0neWan9).
- **Optional chat/task composer mode for embedded hosts** — `ConversationView`
  / `ComposerPane` accept `onSendTask(goal)`; when provided, the composer's mode
  toggle renders and a task-mode Send hands the draft to the callback. Absent,
  behaviour is byte-identical (#1011 @St0neWan9).
- **A deployment can decide parser routing, not only the account** — a managed
  deployment provides and pays for parsing as an operator capability, so
  routing no longer depends on a per-user settings row that the person cannot
  see and that the login flow never wrote (#1008 @Ready22Race).
- **The agent can open a document it found** — `doc_read(document_id, offset?)`
  returns the parsed markdown, so a `doc_search` hit no longer sends the agent
  to a different corpus that answers "not found" for a document that plainly
  exists (#1046 @Ready22Race).
- **Edition-provided project activity source** — `useActivityFeed` consults an
  edition-registered source first for project-scoped feeds (head page,
  pagination, and head poll alike), so an edition-injected project reached
  through a narrow proxy grant shows its real history instead of an empty one.
  No source, or a source that declines, keeps the stock path
  (#1051 @St0neWan9).

### Changed

- **Both post-run checks share one situational gate** — Citation/Audit and
  Task Coverage keep independent user toggles, but a turn that attempted
  `generate_ui`, brought in no external information, or produced no assistant
  prose now skips both, and internal runs disable both.
  `should_run_task_coverage()` gains the previously missing has-assistant-text
  condition (#1021 @St0neWan9).
- **A built-in connector can be disconnected** — disconnecting switches it off
  instead of deleting it, so a built-in whose grant died no longer sits
  "connected" with every action greyed out and every call answering 401.
  Observed in production on `valuz-search` / `valuz-data` after a credential
  rotation (#1048 @St0neWan9).
- **The knowledge document detail is redesigned** — the detail takes the wider
  side (3:7) while a document is open and closes with its page instead of
  outliving it in the layout; one meta strip (type, size, import time, index
  status) replaces ~15 stacked rows; the parsed result renders through the
  system file viewer (`ArtifactRenderer`); actions move onto the title row as
  ghost icon buttons, including a new "view the original file" action; and
  re-index refreshes the tree in place instead of resetting the page
  (#1032 #1033 #1034 @Ready22Race).
- **The sidebar mascot is gone** — the illustration anchored at the bottom of
  the nav was covered by the links as soon as the project list grew, which read
  as a layout bug. The `mascotSrc` prop chain that only fed it is removed; the
  conversation empty state keeps its own (#1042 @St0neWan9).

### Fixed

- **A send claims exactly the files it consumed** — the claimed ids were read
  from a render value captured before the upload landed, then from a function
  updater that React need not run synchronously, and creating the session
  re-keyed the hook's load so every staged row was dropped. The composer's
  staging set is now split from a session's history, `markPendingConsumed`
  reads from a ref and returns what it stamped, a turn that names nothing says
  so, and one claim feeds both the bubble and the bind
  (#1015 #1017 #1018 #1022 @Ready22Race).
- **An unfinished parse no longer stops the person** — the "submit anyway?"
  dialog is removed; the turn binds the attachment regardless of parse state
  and ships `source_path` when the extract is not ready (#1016 @Ready22Race).
- **Staged uploads go to the backend the session will run on** — a file
  attached in a cloud project silently landed on the local backend, into a
  database the claiming turn could never read; all three `useSessionAttachments`
  call sites now pass the base URL (#1013 @Ready22Race).
- **A composer holds its own files, and hands them over explicitly** — staging
  is owner-scoped on the server, so a file attached in the quick chat appeared
  in a project chat's composer too. Each composer now tracks what it attached
  by id, and the one cross-page custody transfer (project draft →
  `/conversation/new`) carries the rows explicitly instead of relying on the
  leak (#1023 #1024 @Ready22Race).
- **The receiving conversation shows the files it was sent with** — the
  optimistic turn seeded `attachments: []`, the page loaded attachments on
  mount before the bind, an in-flight file had no home between staging and
  bound, and a conversation arriving after a post-then-navigate never read
  again. The handoff now carries the rows, the panel refreshes when a turn
  starts, and a sent-but-unconfirmed file has somewhere to be
  (#1007 #1020 #1026 #1027 @Ready22Race).
- **Panel drafts pre-select and pin their host project** — an embedded panel
  declaring `createDefaults.projectId` was overwritten by the fresh-draft
  bootstrap and showed a temporary conversation instead; a host-pinned real
  project now also locks the location bar (#1014 @St0neWan9).
- **A document's calls go to the library that owns it** — the detail poll, its
  preview refetch, and `reindex` did not carry the library id, so on a cloud
  library the panel froze on a swallowed 404 and re-index never showed new
  content (#1028 @Ready22Race).
- **The reindex dispatcher is awaited** — a broker-backed dispatcher had to
  answer "mine" before it knew, on a throwaway loop that closed before the
  publish, and the caller's `return` stood in-process parsing down for good
  (#1030 @Ready22Race).
- **Binding a knowledge base no longer switches its retrieval off** — four
  calls in `_format_kb_scope` kept a stale one-argument shape after the
  datastore went owner-scoped; the first `kb` binding raised, the caller
  swallowed, and the whole per-turn `<additional-context>` announcement died
  with it (#1035 @Ready22Race).
- **"Open the original file" works for a library the user pointed at** — a
  knowledge base's own `root_path` was missing from the owner allowlist, so
  every document in it resolved as `forbidden`; the detail panel also stops
  scrolling the shell (#1052 @Ready22Race). A file that resolves but does not
  exist now produces an error instead of handing a dead path to the OS
  (#1053 @Ready22Race).
- **Runtime-context markers are materialized for the runtime, and named when
  they are not** — `materialize_runtime_context` was applied only to the
  session handed to the factory, so the model credential was filled while every
  MCP header shipped the literal 40-character placeholder and the host gate
  answered 403 (#1044 @Ready22Race). The turn now checks for unfilled markers
  on both the bound and unbound paths, reports which contributor answered, and
  the kernel says what it filled and when a cached runtime reused instead
  (#1039 #1040 #1041 #1043 @Ready22Race).
- **The internal MCP gate's refusals name themselves** — all three 403 exits
  answered a bare `Forbidden`; each now logs its discriminating facts and the
  rejected credential's first 8 characters, which distinguish every credential
  shape in play without being secret (#1036 #1038 @Ready22Race).
- **Claude MCP hook outputs are normalized** — `PostToolUse` replacements keep
  the MCP content-block outer shape, and mappings, scalars, JSON `null`, and
  plain arrays are serialized into text blocks, so an empty `kb_search` /
  `docs_list` result no longer makes Claude Code call `Array.reduce` on an
  object (#1045 @homeant).
- **An async parser backend without a scheduler still runs** — a cloud parser
  that is async-implemented without being ASYNC_POLL has no `_scheduler`, and
  the sync worker thread raised on every PDF instead of driving it on its own
  loop (#1047 @Ready22Race).
- **Organization library actions are handled consistently** — organization
  skills, agents, and connectors are selectable without duplicate panes,
  connector cloud slugs survive download and materialize removable local
  copies, cloud-only connectors stay out of Added after deletion, and lists
  refresh in place after synchronization (#1050 @homeant).
- **`valuz-runs-refresh` also refetches the project list** — an agent share
  landing after boot stayed invisible in the sidebar until an unrelated
  navigation happened to refetch projects (#1031 @St0neWan9).
- **The playbook list quiet-degrades when a host refuses it** — a shared-agent
  or remote project toasted a load failure on `device.offline` /
  `shared_agent.path_not_allowed`; the list now degrades to empty while
  user-initiated actions keep their toasts (#1029 @St0neWan9).
- **Dialogs use the surface background token**, with a design-contract
  regression test (#1019 @St0neWan9).

### Docs & Chore

- **The attachment custody transfer is pinned by a test** — three regressions
  came from re-keying the staging set without enumerating its readers; the
  census is now recorded and the single cross-page handoff has a test
  (#1025 @Ready22Race).

## [0.4.4] - 2026-08-22

### Added

- **Valuz Data built-in connectors** — the built-in Reportify search/stock
  endpoints are replaced by Valuz Data search and full-data MCPs, seeded as
  non-deletable built-ins without starting OAuth in OSS. Existing connector and
  agent bindings migrate from `valuz-stock` to `valuz-data`, Cloud-managed
  credential families stay isolated from local OAuth group sharing, and
  citation/tool metadata is aligned with Valuz Data while accessible source
  labels are preserved (#996 @St0neWan9).
- **A built-in connector says it is built-in** — Disconnect renders disabled
  with an explanatory notice instead of looking enabled and silently doing
  nothing, and a Built-in badge sits beside the connector's name
  (#999 @St0neWan9).
- **The composition root can hand the parser registry a plugin** — `register()`
  is a second door beside the `valuz.parser_plugins` entry-point group. An
  overlay that ships as source on `PYTHONPATH` installs no distribution
  metadata, so entry-point discovery could never see it and every routing
  decision naming it was quietly demoted to `light_local` — with no exception,
  no log line, and no missing module (#992 @Ready22Race).
- **The desktop window remembers its size and position** across restarts, and
  an explicit centred origin is now computed on every platform. macOS was
  previously let through on the assumption that it clamps — it does, but
  clamping is not placement, and a 1440x900 window opened flush against the top
  of the work area (#986 @St0neWan9).

### Changed

- **The Task Coverage protocol stays out of the transcript** — its tool calls
  and results are kept private, its assistant text is held until terminal
  classification, no-gap and meta responses are dropped, and only genuine
  supplement text is published (#983 @St0neWan9).
- **The create-KB dialog stops offering a scan nobody runs** — on a backend
  whose managed root receives documents only through the API, the
  "auto-discover new files" checkbox is hidden and `auto_discover: false` is
  sent, rather than creating knowledge bases with a checked-by-default hidden
  option and then rendering the scan as enabled. Every current caller keeps
  today's behaviour (#989 @Ready22Race).

### Fixed

- **Claude MCP source metadata survives the content-only bridge** — configured
  Claude MCP servers route through one transparent in-process proxy so
  result-level `dev.valuz/source-metadata` and `structuredContent` reach
  citations; the canonical compacted projection is retained beside the private
  Citation sidecar so generic JSON pointers inflate against the projection they
  were minted from; root-scoped Collection hashes stay independent of
  `_valuz_evidence` transport fields; an omitted declared `itemsPointer` is
  normalized; and a cited chunk's display title/URL is enriched from richer,
  already-validated metadata for the same provider and document identity
  (#983 @St0neWan9).
- **A budget stop is no longer claimed when it never happened** — a Codex turn
  still reporting `in_progress` was mapped onto
  `BudgetExhausted(reason="max_turns")`, a ceiling that runtime does not have.
  Borrowing the type was free while a budget stop rendered as nothing; since
  0.4.3 states it in plain words, it is not. A real budget stop is also no
  longer rendered silently (#988 @Ready22Race).
- **An attachment appears the moment it is attached, not five seconds later** —
  a placeholder row goes into local state first and the server row swaps in
  when the upload lands, instead of waiting out sandbox allocation (~3.6s on
  cloud) and the upload itself (~1.3s). The chip is removed if the upload
  fails, and all of them are removed if session creation throws — a chip that
  outlives its upload promises a turn that cannot carry it (#997 @Ready22Race).
- **A knowledge-base upload shows that it is running, and the list keeps its
  statuses live** — one `uploading` state now covers both the header button and
  the drop overlay (whose "processing" copy was unreachable dead code), and the
  tree polls while documents are still parsing instead of going stale for
  minutes (#993 @Ready22Race).
- **DeepAgents dead-end virtual paths fall through to host paths** — under
  `virtual_mode`, an out-of-workspace absolute such as `/Users/x/test` resolved
  to `<cwd>/Users/x/test` and listed empty while the shell tool read the same
  directory freely. The resolution ladder gains one purely additive branch;
  `virtual_mode` stays on, so virtual artifact namespaces and the Windows path
  virtualizer keep working (#998 @jiaoqsh).
- **The Codex MCP-secret guard is diagnosable and correctly scoped** — the
  residue check now matches only the value part of each override line, so a
  benign header value that happens to be a substring of a dotted key path no
  longer trips it; it probes raw, TOML-escaped, and urlencoded shapes, so URL-
  and args-borne residues are caught even when byte-shifted; and a refusal
  carries redacted diagnostics (origin, `secret_env` key, value length,
  SHA-256 prefix, matched key prefixes) instead of a blind message
  (#990 @jiaoqsh).
- **AskUserQuestion cards** — a lone header is hoisted into the card title
  (#985 @St0neWan9), and a header pill that accompanies a question is stacked
  above it rather than crowding the same line (#984 @St0neWan9).

### Docs & Chore

- **`make dev` vendors the DeepSeek Harness runtime closure** — `scripts/dev.sh`
  fetches it on demand with an idempotent freshness check, respects
  `VALUZ_DSH_RUNTIME_BIN` / `VALUZ_DSH_ROOT`, and fails open. Only
  `build-desktop.sh` fetched the closure before, so every dev checkout showed
  the runtime greyed out until the vendor script was found by hand
  (#991 @jiaoqsh).
- "A parsing attachment should hold the turn, not the person" (#994
  @Ready22Race) was reverted before this release (#995 @Ready22Race) and is not
  part of 0.4.4.

## [0.4.3] - 2026-08-21

### Added

- **The agent library is a union across execution targets** — on editions that
  reach more than one machine, the library lists every reachable machine's
  agents, grouped by where they came from, in the ALL tab too. A row keeps its
  own identity once two machines are listed (a slug is not a row id), an agent
  from elsewhere is selectable and stays selected, and a target that cannot be
  picked as a destination is still routable for the rows it owns
  (#957 @St0neWan9, #959 @St0neWan9, #960 @St0neWan9, #961 @St0neWan9,
  #963 @St0neWan9, #964 @St0neWan9, #965 @St0neWan9, #967 @St0neWan9).
- **An edition can contribute projects no execution target lists** — a narrow
  grant opens exactly one project on someone else's machine, and the list
  fan-out cannot ask that host for its projects; the project the agent works in
  now appears anyway (#968 @St0neWan9).
- **Agent rows say where they came from** — the composer's agent lists carry the
  row's own tag, in the library's palette, so an agent from elsewhere states
  what it is rather than where it sits (#970 @St0neWan9, #973 @St0neWan9,
  #974 @St0neWan9, #975 @St0neWan9).
- **A read-only detail page for an agent someone shared with you** — it reads
  like a local agent's header (names, not ids), a rule separates the header from
  the activity list, and an overlay can inject sections beneath it
  (#976 @St0neWan9, #977 @St0neWan9, #978 @St0neWan9, #979 @St0neWan9,
  #980 @St0neWan9).
- **Remote-desktop execution targets** — a distinct glyph for a remote desktop
  target, plus an edition-aware directory chooser (#931 @St0neWan9).
- **Message index rail** — a rail pinned to the left of the transcript for
  jumping between messages in a long conversation (#934 @St0neWan9).
- **`deepseek_harness` derives per protocol** — any non-subscription channel
  speaking the chat-completions wire offers it, mirroring how Codex derives for
  any Responses-wire channel, instead of only the `deepseek` provider kind
  (#966 @jiaoqsh).
- **MCP source provenance in citations** — reads `dev.valuz/source-metadata`
  while preserving the legacy `cn.valuz/citation-source` descriptor, and fails
  closed when the two conflict (#972 @St0neWan9).
- **Opaque per-turn runtime context** — a runtime-context port whose marker
  values materialize only in the runtime's session copy, leaving persisted
  session snapshots untouched (#947 @homeant); a task's title is snapshotted
  into its lead and member execution sessions so billing attribution needs no
  extra lookup (#950 @homeant).
- **`CLAUDE_CODE_MAX_CONTEXT_TOKENS`** — the declared context window is exported
  to the Claude Agent runtime (#941 @jiaoqsh).
- **Date-partitioned managed workspace directories** — managed project and chat
  workspaces move off a flat `<project_root>/<project_id>` layout to dated,
  unguessable directories (#943 @Ready22Race).

### Changed

- **The project grid is the knowledge grid** — the project list renders the same
  tile cards, on the same measured grid, as the knowledge page; both size from
  one shared rule (#981 @St0neWan9). The marketplace's single-agent card footer
  lines up with the team card the same way (#945 @St0neWan9).
- **Each runtime owns its compaction threshold** — the host declares the context
  window and stops dictating when to compact (#946 @jiaoqsh).
- **Task creation returns as soon as the task is registered** — the lead starts
  behind the response instead of holding the HTTP request for the whole bring-up
  (#937 @Ready22Race).

### Fixed

- **Turns silently dropped when the runtime budget was exhausted** — messages
  near the end of a long conversation produced no output, no error and no label
  (#971 @tutu).
- **Usage accounting per turn** — Claude's `usage_update` carries one turn rather
  than the running total, and every model request in a turn is counted with each
  token bucket counted once (#955 @jiaoqsh, #956 @jiaoqsh).
- **Sessions** — a user message is recorded for a turn that never started
  (#951 @Ready22Race); the runtime context is carried on fork and prepare, not
  only on run (#952 @Ready22Race); a one-time reconciliation backfills the
  legacy project-session index before activity and history reads
  (#958 @zhourongyu); `task_id` rides the session detail so "Fork from here" is
  decided by one predicate and stays hidden inside a task's sessions
  (#939 @Ready22Race, #940 @Ready22Race).
- **A swallowed interrupt no longer fails a later completed turn** as
  `CancelledError` (#936 @jiaoqsh).
- **In-workspace Windows absolute paths** are accepted by the DeepAgents file
  tools (#969 @jiaoqsh).
- **i18n** — each request is answered in the language it asked for
  (#942 @St0neWan9); route labels are i18n keys (#954 @St0neWan9); the page-title
  fallback stops passing the branding app name through `t()` (#935 @St0neWan9).
- **The degraded re-probe could stop for the rest of the session** (#938
  @St0neWan9).
- **Editing an agent's instructions** is no longer reverted by a background
  re-fetch while you type (#944 @St0neWan9).
- **The desktop splash progress advances continuously** instead of jumping
  (#948 @St0neWan9).
- **The dead "Switch model" action** is gone from the conversation error card
  (#949 @Ready22Race).

### Docs & Chore

- **Release pipeline** — CI purges the CDN after overwriting a live manifest
  (#930 @St0neWan9), a purge failure can no longer fail the release
  (#953 @St0neWan9), a recovery workflow rebuilds the single-arch versioned mac
  manifests (#933 @St0neWan9), and the rollback runbook is corrected so
  rollback is actually safe (#932 @St0neWan9).
- Agents test fixture satisfies `ExecutionTarget` (#962 @St0neWan9).

## [0.4.2] - 2026-08-18

### Added

- **Agent Plugins** — plugins are a first-class install unit per the Agent
  Plugins 1.0.0 spec (`plugin.json` + `skills/` + optional `mcp.json`): a
  skills-only plugin is a "skill suite", one with MCP servers a "plugin with
  connectors". New `/v1/plugins` API (preview / install from zip, directory,
  URL or market item / enable / disable / update / reference-counted uninstall /
  export / memberships), `.claude-plugin` / `.codebuddy-plugin` compat readers
  that materialize the normalized layout, marketplace item type `plugin`
  (`market:plugin:<slug>`, `composition` filter, source `plugin`), the
  `/plugins` library page, market tabs and plugin badges on skill / connector
  cards (#908 @St0neWan9).
- **One resource page for plugins, skills and connectors** — the three library
  surfaces share a single page and consistent headers instead of three
  near-identical layouts (#925 @St0neWan9).
- **DataService credential rotation without a restart** — the kernel picks up a
  rotated credential in place, so re-keying no longer costs a process cycle
  (#923 @Ready22Race).
- **Per-session gateway headers** — DeepAgents and Claude Agent forward
  `X-Valuz-Session-Id` to the gateway (via `ANTHROPIC_CUSTOM_HEADERS` for Claude),
  so gateway-side logs can be traced back to a session (#919 @homeant,
  #921 @homeant).
- **Model selection hints in the pickers** — the hint that explains why a model
  is or is not selectable now renders where the choice is made (#902 @homeant).
- **Close a document preview with the platform shortcut** — Cmd+W on macOS,
  Ctrl+W elsewhere (#900 @St0neWan9).
- **RedSkill marketplace source** — `MarketplaceSource` accepts `redskill` (the
  Xiaohongshu RedSkill store the commercial control plane now ingests) and the
  market card's source pill labels it (#907 @St0neWan9).
- **Marketplace infinite scroll** — the market list loads the next page as you
  reach the end instead of behind a "load more" button (#927 @St0neWan9).

### Changed

- **Reusable desktop network egress capability** — the Electron-owned egress
  manager moved into its own workspace package and now exposes versioned
  renderer/main contracts, edition policy injection and capability negotiation,
  so overlay desktops can reuse the same network path without copying the
  runtime (#909 @zhourongyu).
- **Post-run checks are gated on external tools** — citation verification and
  Task Coverage only run when the turn actually brought external information
  into the answer. A session may expose host tools over MCP as an
  implementation detail, and those local calls no longer trigger an expensive
  post-run model pass (#901 @St0neWan9).
- **Marketplace tab order** — the top-level tabs now read agents → plugins →
  skills → connectors (plugins moved next to agents) (#918 @St0neWan9).
- **Marketplace `source` is an open string** — where a market item comes from is
  data the index grows over time; the client no longer validates it against a
  closed enum (which made the whole skills tab fail the moment the index
  published a source an older build had not heard of). Unknown sources render
  with a generic pill, unknown badges are dropped, and an index page is parsed
  item by item so a row this build cannot render (new `type` / `install_target`)
  is skipped instead of failing the page. Plugin-package members are labelled
  source `plugin` (was `pluginmarket`) (#911 @St0neWan9).
- **Agents page default view** — the view switcher lists "All agents" first and
  opens on it by default; "By project" is the second tab (#906 @St0neWan9).
- **Only `X-Valuz-Session-Id` is forwarded** — the companion `Session-Title`
  header was dropped from both the DeepAgents and Claude Agent paths; a title is
  user content and does not belong in a transport header (#921 @homeant).

### Fixed

- **Window title fallback no longer goes through i18n** — on routes without a
  registered label the layout used the branding app name as an i18n key
  (`[i18n] missing translation for key "Valuz Team"` on every navigation);
  the literal product name is now used directly (#935 @St0neWan9).
- **Codex no longer leaks MCP secrets into process state** — five related fixes:
  secrets are kept out of the process argv (#913) and the app-server argv (#914),
  the tool shell is isolated from runtime secrets (#915), referenced secrets are
  excluded from that shell (#916), and login-shell secret restoration is blocked
  so a user's profile cannot put them back (#917 @zhourongyu).
- **Safe desktop network ownership changes** — activity checks and confirmed
  interrupts use the memory-only desktop control capability instead of
  owner-scoped user APIs, and a late task race rolls the selected mode back
  rather than restarting an active backend (#910 @zhourongyu).
- **Incompatible egress contracts are rejected** — a renderer and main process on
  mismatched contract versions now fail the negotiation instead of proceeding on
  assumptions (#912 @zhourongyu).
- **Task crash backstop no longer outraces delivery** — the backstop that exists
  to cover a crashed member could fire before that member's result was
  delivered, failing work that had in fact completed (#922 @Ready22Race).
- **Task manifest attribution** — the fourth manifest call site now attributes
  like the other three, and the window is no longer defaulted when a caller did
  not state one (#924 @Ready22Race).
- **A2UI catalog array shapes** — array element object shapes are expanded in the
  catalog, so the compiler sees the fields it is expected to bind (#926
  @St0neWan9).
- **Project pages survive overlay routes** — an overlay route no longer replaces
  the project page underneath it (#899 @St0neWan9).
- **Model selection hints stay on the option rows** — they were showing on the
  collapsed trigger, where the choice is not being made (#903 @St0neWan9).
- **Desktop close shortcut** — with no preview open, Ctrl+W on Windows/Linux fell
  through to closing the only window and quit the app; it is now a no-op there,
  while macOS keeps Cmd+W's window-close meaning and an open preview still closes
  first everywhere (#904 @St0neWan9).
- **Desktop window controls** — a maximized window on Windows/Linux showed two
  outward arrows ("enlarge") where Windows draws the restore glyph; the control
  now draws `ChromeRestore` — a square in the lower-left with a second square's
  edges behind it (#898 @St0neWan9).

### Docs & Chore

- **The quality gates are green again** — ruff, eslint, the design audit, the
  module boundary contract and both test suites (backend 4313, frontend 1191)
  all pass. Two of the design audit's own rules were misfiring: token/theme files
  were counted as their own debt, and PR references written in code comments
  parse as valid hex colours, inflating the colour count on their own. The remaining overage
  was paid down with exact-equivalent design tokens, so no rendered pixel
  changes. Ten cross-module datastore imports now go through the owning module's
  service or a new `ports/effective_resource_sources` (#928 @St0neWan9).

## [0.4.1] - 2026-08-14

### Added

- **DeepSeek Harness (dsh) — a fourth kernel runtime** — the harness joins Claude
  Agent, Codex Agent and Valuz Agent as a selectable runtime (#894 @jiaoqsh), and
  its turns are traced into Langfuse like the others (#895 @jiaoqsh).
- **Unified desktop model network egress** — an Electron-main egress manager gives
  supported model clients the desktop's proxy/PAC routing through loopback model
  ingress, without touching tool shells, MCP servers or browser traffic; Settings
  offers Valuz-managed vs. model-client-managed connections with live status and
  redacted diagnostics (#833 @zhourongyu).
- **Session and message fork** — fork a session or an individual message across all
  three runtimes (#871 @jiaoqsh), with pending state and duplicate-click
  suppression on every fork entry point (#881 @jiaoqsh).
- **Docker self-hosting stack for OSS** — run the workstation headless from
  compose (#862 @St0neWan9).
- **Optional Langfuse tracing for agent turns** (#834 @jiaoqsh), plus a tracing
  extra and `.env` loading in the dev launcher (#836 @jiaoqsh).
- **A pluggable knowledge base** — `DocsRuntimePort` is genuinely swappable
  (#843), with a KB root resolver extension point and a knowledge-base kind
  column (#844), pre-authorized cross-owner scope injection for `search_docs`
  (#845), a bindable document-retrieval runtime (#851), and reindex dispatch plus
  shared-scope contribution seams (#852 @Ready22Race).
- **A2UI standalone theme-aware component system** (#858 @St0neWan9), normalized
  component data contracts (#883 @St0neWan9), and a round of GenUI runtime and
  research-reading UX refinements (#896 @St0neWan9).
- **GenUI generation past the output-token cap** — a capped generation continues
  and the parts are merged (#816 @St0neWan9), with A2UI generation and rendering
  hardened overall (#870 @St0neWan9).
- **GenUI blocks aligned with OpenUI semantics** — chart series palette tokens
  that end multi-series colour collision (#846), bar charts, `AspectRatio` and
  `VisualFirstCard` (#848), and surfaces, hovers and chart chrome (#853
  @hanjixin). The A2UI renderer stack is now lazy-loaded, cutting the main bundle
  by 43% (#850 @hanjixin).
- **Citation & evidence** — search retrieval metadata is validated (#814), an
  anchor-verified claim normalizer supports bounded partials (#817), and
  substantive search summaries register as derived evidence (#819 @St0neWan9).
- **Durable task actor delivery** — actor messages survive a process boundary
  (#875), and a doorbell wakes parked actors while `finish_task` parks its
  members (#884 @Ready22Race).
- **v10 resource control seams** for runtime control (#798 @homeant).
- **Codex for every DeepSeek model** — the per-model allowlist is retired
  (#873 @jiaoqsh).
- **Host extension surface** — a project add-menu slot (#864 @hanjixin),
  conversation turn/title slots that get the scroll their mode needs (#837
  @St0neWan9), and `session_id` threaded into `_kernel_for` so task session ids
  reach kernel metadata (#859 @Ready22Race).

### Changed

- **Task coordination reworked around state instead of messages** — one lease per
  actor, and stopping stops being a message (#878); stopping becomes a state the
  actor reads (#882); `MailboxRegistry` is retired in favour of one message per
  drain with no buffer (#888); member probing is split out of coordination and
  the lead run parks on block (#889); the mailbox drain batch size is a required
  argument (#890); and membership is a query, orphans get adopted, and the lead
  reads its role first (#891 @Ready22Race).

### Fixed

- **Tasks** — a cross-process execution lease ensures exactly one process drives a
  task (#863) and the lease renewer is hardened while draining (#865); the lead no
  longer burns a model turn on a member it already handled (#867), "keep waiting"
  no longer re-runs the last prompt (#868), a session you no longer drive is not
  finalized (#872), the in-turn preempt and wait read the durable inbox and hear
  the doorbell (#876, #885), and buffer reclamation, a stopped lead's run row and
  a drifting deadline are corrected (#869, #886 @Ready22Race). The task detail
  page got its scroll back (#866 @Ready22Race).
- **Citations** — unsourced statements are marked instead of dropped (#818); a
  half-written binding no longer flashes `[blocked]` (#821); a document chunk may
  supersede its own provider summary (#822); unknowns are no longer reported as
  defects (#823); collection addresses and markers stay out of the reader's way
  (#824) and every marker lands on the statement it belongs to (#825); audit
  coverage and post-run verification are preserved (#828); precise evidence is
  preferred without truncation (#829); proven numeric conflicts are corrected
  (#830); sidecars are reconciled at turn completion (#815); and evidence audit
  and presentation improved overall (#835 @St0neWan9).
- **GenUI** — generation `max_tokens` plus an env-tunable harness tool timeout
  (#812), and a truncated generation's valid prefix is salvaged rather than
  discarded (#813 @St0neWan9).
- **Conversation** — the closing bubble carries the whole answer instead of its
  last segment (#874 @St0neWan9), the tool-output content block is unwrapped
  before being read (#877), the title slot receives the whole loaded transcript
  (#854), an edition can declare where its single backend runs (#849
  @Ready22Race), and share sits with the actions while the token readout trails
  them (#832 @St0neWan9).
- **Files & prompts** — a `valuz-file` ref with a surplus slash no longer resolves
  outside its own project (#892), and the file link is taught once, correctly,
  reverting the parser guesswork (#893 @Ready22Race).
- The runs sidebar's project window is scoped with a `project_id` filter
  (#831 @St0neWan9).
- Skill discovery works again under DeepAgents `virtual_mode` (#826
  @Ready22Race), and cloud skill discovery is isolated from the host home
  directory (#860 @homeant).
- The document owner is threaded into the `ASYNC_POLL` enqueue (#842
  @Ready22Race), and edition always-on MCP servers mount under every
  `api_prefix` (#855 @Ready22Race).
- The renderer build gets a raised Node heap (#811 @St0neWan9), `@a2ui/react`
  declares the React floor it actually requires (#820 @St0neWan9), and conflict
  markers committed into `files-api.ts` are removed (#887 @Ready22Race).

### Docs & Chore

- The release process now requires maintainer confirmation of the version number
  before tagging (#810 @St0neWan9).
- GenUI block styling/interaction standards, a component template and a CSS audit
  guard are codified (#857 @hanjixin).
- Test coverage for the full kernel summarization offload path (#827) and the
  docs offload guard's `doc_paths` fake (#847 @Ready22Race).
- Regenerated i18n key types so the checked-in types match the locale files.

## [0.4.0] - 2026-08-08

### Added

- **A2UI component system** — a standalone A2UI v0.9.1 component package with
  edition injection and per-call scope, a backend component registry port,
  typed schemas, analytical tables, provenance controls and a comprehensive
  chart family
  (#733, #745, #751, #771, #779, #782 @St0neWan9).
- **Generative UI live data** — data-binding mode (channel, resolver, grammar),
  the live-data host seam where refs start an edition host and pushes
  re-render, and shape-disambiguated slots with `$host` pass-through
  (#772, #773, #800 @St0neWan9).
- **Artifacts as versioned deliverables** — delivered artifacts are versioned
  instead of overwritten (#732, #740 @Ready22Race); generated UI is recorded
  as a deliverable bound to a host, with host-scoped version history,
  single-revision reads, timestamped generation receipts, hosted regeneration
  appending to the host's lineage, and `deliver_artifacts` announcing
  bound-page revisions like a generation
  (#770, #790, #791, #795, #803, #804, #806 @St0neWan9).
- **Citations & evidence quality** — a complete document research and quality
  flow: claim audits before publication, layered quality policies, layered
  evidence resolution with MCP source metadata, task-coverage enforcement, and
  a risk-based audit sidecar (#690, #691, #692, #700, #724, #741 @St0neWan9).
- **Valurion** — a built-in system agent contract, seeded portably and
  guaranteed even for empty agent libraries
  (#662, #663, fixes #664, #665 @St0neWan9).
- **Document preview pane** — documents open in a resizable split pane with
  tabs, on conversation, task and project surfaces alike
  (#697, #698, #699 @St0neWan9).
- **Notifications** — the drawer gained a History tab, a clear-all action and
  instant (optimistic) dismiss (#725 @St0neWan9); completed tasks are notified,
  and the `notification.created` extension event carries the unread count
  (#714, #715 @zhourongyu).
- **Token usage** — conversation and task token usage is surfaced in the UI
  (#789 @zhourongyu).
- **Auto-compaction at the model's real window** — runtimes compact against the
  provider-declared `max_input_tokens` instead of a guess (#739 @jiaoqsh).
- **Turn-phase latency markers** — `turn_phase` events across all three
  runtimes (#764 @jiaoqsh), with the conversation header split into named
  phase counters and one continuous turn timer (#674, #675 @Ready22Race).
- **Bundled skills from system roots** — skill packages resolve from read-only,
  explicitly declared system roots (#766 @Ready22Race).
- **Host extension surface** — a resource copy menu slot (#680 @homeant), two
  host action slots plus a session-scoped slot and a per-message leading slot
  with its role (#744, #746, #747, #756, #757 @Ready22Race), research-workspace
  extension points (#755 @St0neWan9), overlays can suppress a host surface
  (#762 @Ready22Race), and a host-scoped capability policy decides task
  coverage per surface (#801 @St0neWan9).
- **Project composer parity** — sending from the project composer works the way
  "New chat" does (#682 @Ready22Race).
- **Cloud sessions** — SSE follows a session that moves to a different sandbox,
  the host's `sandbox_status` lifecycle is translated onto the stream, and a
  host-observation channel was trialled and dropped
  (#652, #653, #655, #656 @Ready22Race).
- **Codex runtime for deepseek-v4-flash** (#760 @jiaoqsh).
- **Login-shell PATH** — the backend merges the user's login-shell PATH so
  GUI-launched apps resolve user-installed tools (#687 @jiaoqsh).

### Changed

- **Local document parsing now runs on anydoc instead of MarkItDown** — legacy
  Office (`.doc` / `.ppt` / `.xls`), macro-enabled variants, OpenDocument, RTF
  and EPUB parse locally for the first time; spreadsheet output drops pandas
  artifacts; docx conversion is ~25× faster; and the knowledge-base ingestion
  gate now mirrors the parser (RTF no longer indexed as raw markup, `.htm` and
  extension-less text files ingested, unsupported uploads rejected with a
  reason). See
  [docs/design/local-parser-anydoc-migration.md](docs/design/local-parser-anydoc-migration.md)
  (#761 @Ready22Race).
- **Performance** — the task plan snapshot is no longer quadratic and only the
  last one is read (#769 @Ready22Race); turns stop walking the skills mount
  (#793 @Ready22Race); session creation no longer content-hashes every skill
  (#794 @Ready22Race).
- **Citations internals** — the audit is decoupled from task coverage
  (#730 @St0neWan9).
- **Frontend structure** — `ConversationPage.tsx` split into `conversation/`
  modules (#735 @St0neWan9).

### Fixed

- **Tasks** — the live task UI, model-facing text and actor loop are repaired
  and tested end to end (#651); the lead's goal is no longer overwritten on
  wake-up and agent playbooks are de-duplicated (#668); stale member
  summaries, poisoned plan nodes and dropped activity rows are gone and the
  per-poll queries are indexed (#753); a lost plan write no longer tells the
  lead its subtask key vanished (#759); a task nobody is minding is now
  surfaced (#768); a deleted session no longer leaves its run row and the tool
  gates gained tests (#776) (all @Ready22Race).
- **Project send handoff** — the composer no longer freezes while a cloud
  session is created, the pending send releases when its echo arrives, the
  handoff carries every composer option, waits for the project binding and
  draft bootstrap, survives reloads without replaying, never flashes the
  new-chat welcome, keeps the optimistic turn alive past the landing refresh,
  and renders the header while the session is still being minted
  (#676, #677, #679, #683, #684, #686, #688, #689, #694, #695, #696
  @Ready22Race; attachment chips clear after the handoff #736 @St0neWan9).
- **Conversation & stream** — runtime assistant messages are preserved on the
  stream (#723 @St0neWan9); assistant output no longer merges into the user
  bubble on turn-scoped message ids (#742 @St0neWan9); the bound agent owns
  the session's brain (#731 @Ready22Race); session capabilities converge
  inside `run_turn` (#710 @Ready22Race); bundled skills landing mid-session
  converge too (#763 @Ready22Race); the session token total below the composer
  is dropped (#805 @St0neWan9).
- **Citations hardening** — collection snapshots replay and unpack correctly,
  values resolve with temporal context, claim-local dimensions win, value
  labels are not metrics, evidence coverage and discovery item paths are
  bounded, chunk-level document evidence is restored, discovery results stay
  non-citable, the bundled evidence protocol is restored, local claims bind
  semantically in batch, and structured evidence audits are hardened
  (#702, #704, #706, #707, #709, #717, #719, #721, #722, #796, #807, #808
  @St0neWan9); task coverage accepts categorical value labels and enforces
  silent no-op completion (#718, #743 @St0neWan9).
- **Generative UI** — container-query breakpoints (#726 @hanjixin); adaptive
  dashboard polish and responsive card widths restored
  (#670, #672, #678 @yy83000812); the `components` argument is scoped on the
  repo boundary and A2UI sub-item refs resolve (#749); the slot grammar says
  the binding IS the refresh, the slot shape is the source's, and the
  generation's host resolves from the turn (#774, #775, #778); block sizing
  under containment is corrected (Waterfall bar cap, avatar fallback,
  page-header Cluster, slot-cluster zeroing, root width)
  (#780, #783, #785, #787, #788); one breathing element in the generation tail
  (#784); segment summaries truncate instead of wrapping (#797)
  (all @St0neWan9).
- **Artifacts** — a file-stored bound document is served, not null
  (#781 @St0neWan9).
- **Skills** — CRLF `SKILL.md` frontmatter parses (#673 @St0neWan9);
  filesystem-hostile characters are kept out of skill names and directories
  (#703, #705 @Ready22Race); system skill roots must be declared, never
  inferred (#767 @Ready22Race); the bundled copy no longer deletes before it
  writes (#792 @Ready22Race); project skill catalogs route by origin
  (#737 @Ready22Race).
- **Notifications** — a completed task no longer announces itself as blocked ("Task blocked")
  (#758 @Ready22Race); oversized bodies (multi-KB provider error dumps) are
  clamped at every layer instead of breaking the toast, drawer and history
  (#738 @St0neWan9).
- **Desktop & packaging** — mac sidecar entitlements are re-applied explicitly
  on re-sign (#681, #799 @jiaoqsh); packaged image OCR actually starts and a
  broken rapidocr bundle surfaces instead of silently substituting
  (#750, #752 @Ready22Race); the committed `backend/.venv` symlink that broke
  `make dev` on fresh clones is removed (#754 @Ready22Race).
- **Runtimes & MCP** — one broken MCP server no longer kills the whole turn
  (#685 @jiaoqsh); a declared tool timeout is honoured in every runtime
  (#765 @St0neWan9); a legacy kernel DB is recovered and MCP projections
  serialize (#701 @St0neWan9).
- **Files & channels** — `/v1/files/resolve` routes to the backend that owns
  the entity (#654); vanishing-file batch failures, expired-address recovery
  and chat-binding routing are fixed (#657) (both @Ready22Race).
- **Providers** — cloud deployments skip `subscription_models.local.json`
  (#786 @Ready22Race).
- **App chrome** — the degraded-target banner pins to the top of the main
  panel (#802 @St0neWan9); Agent export lives in the copy menu
  (#693 @homeant); the artifact renderers are behind `t()`
  (#660 @Ready22Race).

### Docs & Chore

- The file-address design doc is marked shipped and corrected against the code
  (#659), three dead file symbols are deleted and project-path arithmetic
  unified (#658), and the runtime test doubles the citation work outgrew are
  refreshed (#708) (all @Ready22Race).
- The log file path setting is unified (`VALUZ_LOG_FILE_PATH`)
  (#727 @homeant).
- The artifact schema correction ships as migration 0030 (#777 @St0neWan9).

## [0.3.6] - 2026-07-29

### Added

- **IM channels** — agents can be bound to chat channels and answer in them:
  agent ↔ channel bindings, a WeCom AI-bot runtime, Feishu long-connection
  replies, and channel session routing (#629, #631 @hanjixin).
- **Channel ↔ project binding and project default lead** — a chat binds to one
  project, a project names the agent that leads its tasks, and a guided card
  plus in-chat commands set the binding when the bot joins a group
  (#636 @St0neWan9).
- **Factory model defaults** — the out-of-the-box runtime / model / provider /
  effort defaults moved behind a replaceable `ModelDefaultsPort`, overridable
  per build via `VALUZ_DEFAULT_*` (#627 @St0neWan9).

### Changed

- **Tasks** — a deep two-pass hardening of the lead/member subsystem: plan
  writes go through a single authorized door, every plan and task-status write
  is a `plan_version` compare-and-set, plan nodes gained a real transition
  table, actor-lifecycle races were closed, and the seven live task endpoints
  are now in `api/openapi.yaml` (#639, #643 @Ready22Race).
- **Generative UI** — better model handling, corrected DeepSeek thinking state,
  and stronger prompt instructions (#635, #637 @hanjixin).
- **Generative UI performance** — ephemeral helper sessions are stripped to
  bare completions and their reasoning streams to the tool card, cutting tens
  of seconds of scaffolding off every `generate_ui` call (#638 @jiaoqsh).

### Fixed

- **Channels** — bindings load by row owner instead of the ambient local
  identity (#634 @St0neWan9); channel reads are no longer cached
  (#640 @St0neWan9).
- **Projects** — group rows update from the mutation rather than a refetch
  (#641 @St0neWan9); the project chat panel refreshes after binding changes
  (#642 @St0neWan9).
- **Desktop** — orphaned backend sidecars are reclaimed and a parent-process
  watchdog stops them outliving the app (#628 @St0neWan9); update-toast errors
  are humanized and offer a retry (#625 @St0neWan9).
- **Knowledge base** — the document detail panel renders Markdown previews and
  keeps its rebuild/delete actions visible in a narrow panel
  (#624 @yy83000812).
- **Onboarding** — Firecrawl dropped from the default assistant connectors
  (#626 @St0neWan9).
- **Settings** — the memory empty placeholder is centered (#630 @St0neWan9).
- **Generative UI** — the redundant title spinner is gone from the card
  (#632 @St0neWan9).

### Docs & Chore

- **Tests** — the backend suite's 40-failure order dependency is fixed
  (#621 @Ready22Race).
- **Docs** — the COS auto-update feed path is corrected to the `<edition>/`
  prefix (#622 @St0neWan9); the OpenUI generative-UI design and plan are
  consolidated into English (#633 @hanjixin).

## [0.3.5] - 2026-07-27

### Added

- **Streaming resilience** — the in-flight turn is recovered on reconnect from
  a live-partial state snapshot instead of event replay, and live-partial drops
  are reported explicitly rather than degrading silently (#617 @Ready22Race).
- **Document reader** — DocumentReaderView for reading fetched documents
  (#615 @St0neWan9).
- **Connector catalog** — editions can contribute their own catalog entries
  (#613 @St0neWan9).
- **Resources** — organization import plus skill and connector detail action
  slots; cloud-only catalog rows stay importable (#610 @homeant).
- **Providers** — Claude Opus 5 added to the subscription model list
  (#614 @jiaoqsh).
- **DeepAgents runtime** — the checkpoint backend can be pinned per deployment
  (#605 @Ready22Race).

### Changed

- **Memory** — injection frozen into `Session.instructions` once per session,
  instead of once per user message (#618 @jiaoqsh).

### Fixed

- **Conversations** — the header no longer goes quiet while background work is
  still running (#619 @Ready22Race); a first empty roster no longer claims
  nothing is configured (#616 @St0neWan9); replayed `session.update` frames can
  no longer revive a finished turn (#609 @Ready22Race).
- **Sessions** — todos panel no longer blank on re-open: the detail endpoint
  kept its todos and unconditional panel writes were removed (#612 @jiaoqsh).
- **Tasks** — dispatch gets the chat-parity provider fallback and a roster
  pre-flight (#606 @jiaoqsh).
- **Skills** — bundled pack materialization now emits a sync notification
  (#607 @homeant).
- **Packaging** — the frozen backend runs in forced UTF-8 mode, with utf-8
  markers on migrations (#608 @St0neWan9).
- **i18n** — missing connector empty-state strings added (#604 @St0neWan9).

## [0.3.4] - 2026-07-23

### Added

- **Local data backup** — versioned snapshots of the client's local data with
  scheduling, version browsing, and restore (#572 @Ready22Race).
- **Per-entity execution location** — automations, knowledge bases, and library
  resources each carry their own execution target (#597 @hanjixin).
- **Automation runtime seam** — deployments can bind an
  `AutomationRuntimePort`, dispatch and execute persisted automation runs
  through stable facades, and initialize the canonical executor in a headless
  worker context. The default binding preserves the existing in-process runner
  and failure monitor (#570 @homeant).
- **Lifecycle unit of work** — exposed to overlays so edition code can join the
  host transaction (#579 @homeant).
- **Async sandbox credential verifier port** — managed editions can bind a
  database/cache/identity-service verifier without changing the wire contract
  (#581 @homeant).
- **Task finalization events** — `task.finalized` published on terminal writes,
  and the memory-review sandbox released with it (#564 @Ready22Race).
- **Codex thread items** — `imageView` and `contextCompaction` surfaced in the
  transcript (#571 @jiaoqsh).
- **Claude runtime** — env-gated `skipWebFetchPreflight` CLI setting
  (#600 @jiaoqsh).

### Changed

- **Storage** — uniform `RuntimeStore` dual-write across the kernel and the
  host durable data plane (#578 @Ready22Race).
- **Tasks** — host-resident task module finalized: de-duplication, explicit
  seams, dead-code purge, and type tightening (#594 @Ready22Race).
- **Frontend event stream** — session-lifetime stream plus a queue-drain busy
  gate replaces the per-view wiring (#593 @Ready22Race).
- **Composer catalog extension** — editions can register one generic adapter
  for model and agent catalog loading while OSS keeps its single-backend
  module-default behavior (#595 @homeant).
- **Providers** — the composer's 1+N provider fan-out replaced with one gated
  list request (#573 @jiaoqsh).
- **Frontend design spec** — remaining spec violations corrected across the app
  (#562 @yy83000812).

### Fixed

- **Conversations** — follow-up turns rendered nothing because a stale-cursor
  replay closed the send-path stream; list fan-out now times out instead of
  hanging (#589 @St0neWan9); waiting pill stuck on running turns and queued
  messages invisible or flickering (#590 @Ready22Race); a resolved turn is
  classified from the authoritative `run_turn` result rather than a lagging
  mirror re-read (#587 @Ready22Race); conversation history stays stable while
  models load (#596 @St0neWan9).
- **List surfaces** — resilient to a degraded cloud target (#588 @St0neWan9)
  with follow-ups for abort, re-probe, banner gating, and the list cache
  (#591 @St0neWan9).
- **Events** — subagent events attributed with `parent_tool_use_id` so they no
  longer shred the lead's live stream (#565 @Ready22Race); streaming deltas
  coalesced per flow instead of per type (#566 @Ready22Race).
- **Execution location** — automation/KB probe gap closed and docs health
  fanned out (#599 @hanjixin); the managed project's origin is recorded for
  remote conversations (#601 @St0neWan9).
- **Composer** — models (#586 @homeant) and agents (#592 @homeant) load from
  the selected service.
- **MCP** — FastMCP DNS-rebinding 421s stopped on the built-in servers
  (#574 @Ready22Race); built-in FastMCP servers made stateless for
  multi-replica deploys (#575 @Ready22Race).
- **Notifications** — unread badge cleared when a conversation is opened
  (#576 @Ready22Race); badge redesigned around a single unread signal with
  corrected sizing/optical alignment and a focus-visible sheet close
  (#602 @St0neWan9).
- **Runtime** — bg-busy marker released on terminal `task_updated` pushes
  (#568 @Ready22Race); marker-only `error_during_execution` classified as
  interrupted (#598 @jiaoqsh).
- **Memory** — review runs inside the source session's sandbox instead of a new
  one (#577 @Ready22Race).
- **Database** — `pool_pre_ping` + `pool_recycle` enabled on non-SQLite engines
  (#569 @Ready22Race).
- **Boot** — a source backend refuses to run on the packaged app's data dir
  (#563 @St0neWan9).
- **Agents** — runtime validated at the API boundary (#560 @St0neWan9).
- **Skills** — skill-service request unit of work committed (#580 @homeant).
- **Frontend** — public asset paths resolved via `BASE_URL` for the browser
  router (#561 @hanjixin); the border around the HTML artifact preview iframe
  dropped (#567 @St0neWan9).

### Docs & Chore

- Deps: `openai-codex` 0.1.0b3 → 0.144.4, the first stable release
  (#583 @jiaoqsh); `claude-agent-sdk` 0.2.95 → 0.2.123 (CLI 2.1.215)
  (#585 @jiaoqsh).
- Tests: protocol-surface parity red on main fixed by excluding the host-side
  composite (#584 @Ready22Race).

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
