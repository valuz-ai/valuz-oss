# Per-task optional check policy

## Contract

Citation binding, citation verification and task coverage are independent,
optional checks. Their policy is scoped to a server-owned operation/run, not to
a product page or workspace. It never bypasses authentication, resource access,
confirmation, tool input validation or output schema validation.

`ports/capability_policy.py` defines:

- `OptionalCheckOverrides`: nullable strict booleans `citation_enabled`,
  `verification_enabled`, `task_coverage_enabled`. `None` means no opinion.
- `TaskCheckConfig`: version `1`, operation, origin (`chat`, `task`, `automation`),
  run identity, optional policy revision, exact Automation / Playbook run references,
  non-secret JSON configuration and explicit overrides.
- `TaskCheckContext`: the authenticated owner, actual session/project/task identity,
  optional untrusted `HostRef`, the copied config and policy-source provenance.
- `TaskCheckPolicyPort.resolve(context)`: asynchronous owner-scoped configuration
  lookup, registered in `ext.task_check_policies`. Providers must validate resource
  references under `context.user_id`. They receive independent config copies.

This is an internal service/edition contract. The public message API does not
accept raw policy overrides. Prompt text and host names are not policy authority.
No Graph reads/writes or Graph permissions are implied by a check decision.

## Resolution and persistence

For each independent input, precedence is:

1. Explicit service overrides / trusted operation configuration.
2. Registered task policy (first non-`None` value per check).
3. A still-registered legacy host policy evaluated for this input only.
4. Current owner preferences.

The `pre_turn` hook runs after kernel allocation and writes to that execution
kernel. `metadata.valuz.optional_check_snapshot` stores version, owner/session/
project/task identity, operation/run/revision, config, contributing policy sources
and all three resolved flags. The kernel copies a matching snapshot into each
message's metadata at turn start, preserving history when the session advances.
No credentials, raw prompts or unbounded resource contents belong in this snapshot.

Config objects are deep-copied at send, enqueue, task kickoff and hook boundaries.
Edits to caller objects or a provider's copy cannot mutate a pending operation.

## Input lifecycle

| Path | Policy behavior |
| --- | --- |
| New chat / synchronous send | New config/revision; current preferences and operation policy |
| Automation chat | Exact automation/run and pinned PlaybookRun; prepared action/config travels with the rendered prompt |
| Automation task | Prepared config is persisted on the TaskRow before kickoff |
| Queue item | Own JSON config in `QueuedInputRow.input`; restored at drain, including after process restart |
| Queue edit | New ordinary-conversation policy; attachments and host context retained |
| Queue promote / steer / pause-resume | Original unedited item config retained |
| Task lead / member continuation or recovery | Owner-scoped task config/revision is rechecked, matching resolved session snapshot retained |
| Revised task goal | New optional-policy revision and ordinary task operation; lead/member snapshots re-resolve at their next turn boundary |
| Fork / foreign / malformed snapshot | Does not qualify as a continuation; resolve from safe current inputs/preferences |

Task members share the task's explicit configuration. Each member resolves its
global-preference fallback at its own first allocated turn; matching continuations
retain that member's snapshot. There is no separate task-wide atomic preference
snapshot. Revising a goal changes only optional policy revision, not execution
lineage or task identity.

## Retirement and failures

The anonymous `valuz.task_coverage_host_override` metadata key is removed lazily
at successful convergence. It is never used as a fallback. A live unrelated
provider can still contribute a fresh decision; unrelated session metadata is
preserved. This is not a bulk deletion or migration of user data.

A provider failure grants no exemption: other valid providers/current preferences
still resolve the checks. Invalid queue/task configs and malformed snapshots are
ignored with safe defaults. In contrast, inability to read current preferences,
verify a task revision or persist the new policy raises `RequiredPreTurnError`.
The kernel-client boundary refuses model dispatch, and the existing turn driver
surfaces/finalizes the preparation failure. It must not continue with a stale
disabled snapshot. Credential/skill restamping remains best-effort.

The kernel independently classifies actual turn activity. Only pure structured
configuration delivery can skip optional post-run checks. Any research/tool
activity before or after configuration keeps them eligible. A task-coverage pass
with no substantive addition is silent; real additions and errors remain visible.
