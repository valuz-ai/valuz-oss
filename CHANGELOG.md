# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.4]: https://github.com/valuz-ai/valuz-oss/compare/v0.1.2...v0.1.4
