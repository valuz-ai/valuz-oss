# Agent Channel Routing

This note captures the first channel design for Feishu, WeCom, and DingTalk.
The core product rule is: a channel belongs to an agent identity; a project is
resolved at message time from the projects where that agent is deployed.

## JiuwenSwarm Alignment

JiuwenSwarm splits channel ingress into three layers that are worth keeping:

| JiuwenSwarm role | Valuz target |
|------------------|--------------|
| `docs/zh/A2A.md` as integration truth | this design doc plus future `docs/zh/channels.md` |
| `a2a_connect.py` as protocol service and Message mapper | `modules/channels/adapters/*` for A2A, Feishu, WeCom, DingTalk |
| `app_gateway.py` as env/config registration point | FastAPI boot/deps create channel services from host config |
| `message_handler.py` as E2A bridge to AgentServer | channel service calls `SessionService` / `KernelClient` only after routing |
| `channel_manager.py` as channel registry and outbound dispatcher | future channel manager stores adapter instances and dispatches replies |

The important carry-over is the boundary: platform adapters map external
requests into a normalized host message; they do not own project/session policy.
Routing policy lives in `AgentChannelResolver`.

## Ownership Model

- `channel_instance` represents one external bot/account/member installation:
  Feishu app bot, WeCom app bot, or DingTalk robot/app.
- A channel instance binds to an agent source slug, not to a project.
- Project targeting comes from `valuz_project_member`: the mentioned agent looks
  up its live project placements via the source agent slug.
- Platform adapters do not decide session policy. They only verify callbacks,
  deduplicate event ids, parse mentions, and normalize payloads.

## Normalized Routing Input

Every platform callback should become a `ChannelMentionContext`:

- `channel_instance_id`
- `external_chat_id`
- `external_thread_id`
- `request_id`
- `external_message_id`
- `external_user_id`
- `mentioned_agent_slug`
- optional explicit project hint from text or structured command
- whether the message is a top-level mention or a continuation/reply; quoted or
  referenced messages count as continuations
- optional explicit session intent hints from text, such as "continue/继续/刚才"
  or "new session/新开/另起"

The resolver also receives the agent's current `AgentPlacement` rows and an
optional `ChannelThreadBinding` for the external thread.

Use E2A's naming discipline for the normalized object:

| Concept | Channel field | Notes |
|---------|---------------|-------|
| gateway request id | `request_id` | generated if the platform has no request id; used for response correlation |
| platform message id | `external_message_id` | never substitute this for `request_id` |
| platform thread/session | `external_thread_id` | A2A `context_id`, Feishu thread/root message, or chat id fallback |
| business params | `params` in the adapter layer | text should normalize to `query`/`content`; files stay in `files` |
| overflow platform data | `channel_context` in the adapter layer | only for fields that do not deserve first-class normalized names |

## Routing Key

Thread/session continuity is keyed by:

```text
(channel_instance_id, external_chat_id, external_thread_id, agent_slug, project_id)
```

This prevents one group chat from accidentally mixing sessions across agents or
across projects.

If a platform has no thread id, use `external_chat_id` as the thread component.
The key is represented in code as `ChannelRouteKey`.

## Decision Rules

1. If the agent has no live placements, return `not_deployed`.
2. If the user names an explicit project and that agent is deployed there, open
   a new agent-bound session in that project.
3. If the user explicitly asks for a new session, do not reuse a saved binding.
4. If the inbound message is a continuation/reply, or a top-level mention with
   an explicit continue hint, and the external thread is bound to an idle/created
   session for the same agent and project, reuse it.
5. If that matching bound session is `running`, enqueue the message into the
   session input queue and return `queue_session`.
6. If a recent binding is supplied and the message has a continuation hint,
   apply the same reuse-or-queue rule to that session.
7. If exactly one live placement exists, open a new session in that project.
8. If multiple live placements exist and no hint disambiguates them, ask the
   user to choose a project.
9. If a saved binding points at a project where the agent is no longer deployed,
   or at a session that is no longer directly runnable, do not reuse it.

## Platform Adapter Boundary

Feishu, WeCom, and DingTalk adapters should share the same host flow:

1. Verify signature/challenge and drop duplicate event ids.
2. Resolve the installed channel instance to an agent source slug.
3. Parse the mentioned member and external thread/message ids.
4. Extract optional project hints such as `项目: X` or `/project X`.
5. Call the channel resolver.
6. For `new_session`, call `SessionService.create_session(project_id, agent_slug=...)`.
7. For `reuse_session`, call `SessionService.send_message(session_id, ...)`.
8. For `queue_session`, call `SessionService.enqueue(session_id, ...)` and send a
   short channel acknowledgement instead of subscribing to the in-flight stream.
9. Persist/update the external thread binding and dispatch streamed replies back
   through the same adapter.

The adapter layer owns each platform's callback quirks; the resolver owns only
Valuz routing semantics.

## First WeCom Entry Points

This branch wires WeCom through FastAPI first. Feishu follows in the next
platform pass using the same adapter boundary.

| Platform | Endpoint |
|----------|----------|
| WeCom URL verification | `GET /v1/channels/wecom/{channel_instance_id}/callback` |
| WeCom message callback | `POST /v1/channels/wecom/{channel_instance_id}/callback` |

Initial WeCom configuration is environment-backed:

| Variable | Meaning |
|----------|---------|
| `VALUZ_WECOM_CHANNEL_INSTANCE_ID` | Optional expected WeCom instance id; defaults to path id |
| `VALUZ_WECOM_OWNER_USER_ID` | Valuz user that owns this env-backed channel instance |
| `VALUZ_WECOM_AGENT_SLUG` | Source agent slug bound to the WeCom app |
| `VALUZ_WECOM_TOKEN` | WeCom callback token |
| `VALUZ_WECOM_ENCODING_AES_KEY` | WeCom EncodingAESKey for echostr/message decrypt |
| `VALUZ_WECOM_CORP_ID` | Optional corp id check after decrypt |
| `VALUZ_WECOM_BOT_NAME` | Optional display name stripped from leading `@name` text |

Real callbacks now flow through `ChannelIngressService`:

1. Adapter verifies and normalizes the platform payload.
2. Placement reader finds projects where the bound source agent is deployed.
3. Thread binding store looks for a previous session for the same route key.
4. Resolver decides reuse/new/ask/not-deployed.
5. New sessions are created via `SessionService.create_session(agent_slug=...)`.
6. The inbound text is sent via `SessionService.send_message(...)`.
7. New sessions are persisted in `valuz_channel_thread_binding`.

The environment layer is deliberately temporary. A UI-backed channel settings
table should move tokens and AES keys into the secret store before this becomes
multi-user or cloud-facing.

Feishu adapter code may share the same normalized contracts, but the public
Feishu route should be added only after the WeCom path has been verified against
a real callback.

## A2A / E2A Mapping Notes

For an A2A ingress adapter:

- A2A `message.messageId` maps to `external_message_id`.
- A2A `contextId` maps to `external_thread_id` and participates in
  `ChannelRouteKey`, not directly to a Valuz kernel session id.
- A2A `taskId` can seed `request_id`; if absent, generate one.
- A2A `parts[].text` normalize into `params.query`; non-text parts normalize
  into `params.files`.
- A2A `metadata` should be copied only after extracting first-class normalized
  fields; residual keys belong in adapter-owned `channel_context`.

For Valuz, E2A is an architectural pattern rather than a new transport layer in
this branch. The inner execution boundary remains `SessionService` and
`KernelClient`; adding an E2A-shaped envelope should happen only if we need an
explicit public Gateway-to-Agent contract.

## Next Implementation Steps

1. Add host tables for channel instances and external thread bindings.
2. Add a `ChannelPlacementReader` service wrapper around project-member lookups.
3. Add adapter skeletons for Feishu, WeCom, and DingTalk that emit
   `ChannelMentionContext`.
4. Add outbound dispatch plumbing that can stream kernel events back to the
   correct channel adapter using `ChannelRouteKey`.

## Follow-up Design

Steps 1–4 above have landed. The questions that surfaced once the Feishu channel
carried real conversations — how a group comes to mean one project, and who
leads a task when the project holds a team — are answered in
[channel-project-binding-and-default-lead.md](channel-project-binding-and-default-lead.md)
(written in Chinese).
