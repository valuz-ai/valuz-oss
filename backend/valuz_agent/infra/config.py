import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode


class Settings(BaseSettings):
    app_name: str = "valuz-agent"
    # Host data root (``~/.valuz-oss``). This field is the single source of
    # truth for the data-dir LOCATION, but application code must NOT read it
    # directly — go through ``infra.fs_registry.fs_registry``: ``data_dir()``
    # when a write will follow (it ensures the dir exists), or the non-creating
    # ``resolve(*parts)`` for a read/probe / a path handed to another component.
    # The registry is the one FS boundary a future sandbox/relocation has to
    # change. Direct ``settings.data_dir`` reads are sanctioned ONLY in this
    # file (self-derivation of the paths below), in ``fs_registry`` itself, and
    # in ``boot.migrate_data_dir`` (the one-time root relocation).
    # May contain {user_id} when the deployment mounts per-user config roots.
    # OSS defaults to the root itself, without a user-id subdirectory.
    data_dir: Path = Path.home() / ".valuz-oss"
    db_filename: str = "valuz.db"
    # The kernel's own SQLite file — sessions / messages / events, its
    # langgraph checkpoint tables, and the kernel ``alembic_version``. Kept
    # in a SEPARATE file from the host ``valuz.db`` (sibling in ``data_dir``)
    # so it can be handed to a sandboxed/remote kernel that owns it
    # exclusively, and so dev (in-process) and dev-sandbox share one history.
    # See ``kernel_db_url`` for the resolution order.
    kernel_db_filename: str = "kernel.db"
    debug: bool = False

    # Explicit DATABASE_URL — when set, overrides the default SQLite path.
    # Accepts postgresql://... for multi-user deployments.
    database_url: str | None = None

    # Explicit override for the kernel database URL (e.g. a Postgres DSN, or
    # a custom SQLite path). When unset, the kernel still gets its OWN file —
    # ``data_dir/kernel_db_filename`` — for the local SQLite default; it only
    # shares the host database when ``database_url`` itself is set (a server
    # deployment where host + kernel deliberately co-locate). The host always
    # reaches kernel state through the ``KernelClient`` seam, never by querying
    # kernel tables on its own engine. Override with ``VALUZ_KERNEL_DATABASE_URL``.
    kernel_database_url: str | None = None

    # Kernel transport mode — which ``KernelClient`` implementation the
    # host binds at import. ``inprocess`` (default) drives the kernel's
    # route functions in this process; ``http`` addresses a kernel
    # running as a separate process at ``kernel_url`` (bare subprocess,
    # sandbox, or remote), authenticated by ``kernel_token``. Override
    # with VALUZ_KERNEL_MODE / VALUZ_KERNEL_URL / VALUZ_KERNEL_TOKEN.
    #
    # ENV CONTRACT (two sides, one secret): the standalone kernel
    # *server* reads ``KERNEL_AUTH_TOKEN`` from its own process env and
    # refuses to start without it (unless KERNEL_ALLOW_UNAUTHENTICATED=1);
    # the *host* sends ``VALUZ_KERNEL_TOKEN`` as the bearer. Whoever
    # provisions the kernel process must set both to the same secret —
    # see tests/adapters/test_http_kernel_client_subprocess.py for the
    # canonical wiring.
    kernel_mode: str = "inprocess"
    kernel_url: str = "http://127.0.0.1:8400"
    kernel_token: str | None = None

    # The kernel data-service store tier is configured purely via the
    # environment, loaded at boot — ``KERNEL_STORE`` (local|pg|remote) +
    # ``VALUZ_DURABLE_DATABASE_URL`` (pg) / ``VALUZ_DATA_API_*`` (remote), read
    # directly by the kernel's ``AppConfig``, the boot DataService binding, and
    # the sandbox provisioner. There is no host-DB / GUI config surface.

    @property
    def is_http_kernel(self) -> bool:
        """True when the kernel runs as a SEPARATE process (subprocess /
        sandbox / remote) and the host drives it over HTTP. Boot must then
        skip the in-process kernel bootstrap — migrations, store/orchestrator
        singletons, kernel router mounting, and orphan scans — because the
        standalone kernel owns all of that (see
        ``docs/design/kernel-sandbox-deployment.md`` §B.6 / B2–B5)."""
        return self.kernel_mode == "http"

    # ── Backend self-URL ─────────────────────────────────────────────
    # Where the host's own FastAPI is reachable from inside the same
    # process / container. Used to inject the in-process docs MCP server
    # URL into the kernel's ``session.mcp_servers`` so the agent's MCP
    # client (running in the kernel runtime) can call back into the host
    # for ``doc_search`` / ``list_doc_scope``. Override with
    # ``VALUZ_BACKEND_BASE_URL`` (e.g. ``http://127.0.0.1:18080``) when
    # the launcher pins a custom port.
    backend_base_url: str = "http://127.0.0.1:8000"

    # ── Global API prefix ────────────────────────────────────────────
    # Optional base path(s) prepended to the whole public HTTP surface — the
    # host's own routers, the overlay's ``module_registry`` routes, and the
    # in-process kernel routers. Lets the backend sit behind a shared-host
    # ingress that namespaces it by path (e.g. istio routing ``/valuz-backend``
    # to this service without a rewrite). Empty (default) → routes served at
    # their native paths; behaviour unchanged. ``["/valuz-backend"]`` → the
    # entire surface moves under that base; ``["", "/valuz-backend"]`` (env
    # ``,/valuz-backend``) → served at BOTH so native/internal callers keep
    # working while the ingress sees the prefixed surface. The internal
    # ``/internal/mcp/*`` mounts are reached server-side via ``backend_base_url``
    # and stay at fixed native paths — never prefixed. Override with
    # ``VALUZ_API_PREFIX``; accepts a JSON list or a comma-separated string,
    # each entry normalised to ``""`` or ``"/segment"``.
    api_prefix: Annotated[list[str], NoDecode] = []

    @field_validator("api_prefix", mode="before")
    @classmethod
    def _normalize_api_prefix(cls, v: object) -> list[str]:
        if v is None or v == "":
            return []
        items = v.split(",") if isinstance(v, str) else list(v)  # type: ignore[arg-type]
        out: list[str] = []
        for item in items:
            seg = str(item).strip().rstrip("/")
            if seg and not seg.startswith("/"):
                seg = "/" + seg
            if seg not in out:  # dedup, preserve order; keep "" (native) entries
                out.append(seg)
        return out

    # Deployment profile controls startup behavior that depends on ownership
    # assumptions. ``local`` keeps local-owner bootstrap semantics for desktop/
    # single-instance clients. ``cloud`` skips owner-scoped startup mutations
    # (official seed locale push, local identity seeding) that can leak
    # synthetic owner ids into shared multi-user backends.
    deployment_type: Literal["local", "cloud"] = "local"

    @field_validator("deployment_type", mode="before")
    @classmethod
    def _normalize_deployment_type(cls, value: object) -> str:
        if isinstance(value, str):
            return value.strip().lower()
        raise ValueError("deployment_type must be 'local' or 'cloud'")

    # Whether process startup may initialize owner-scoped user content under the
    # local install owner. OSS desktop keeps this enabled. Multi-user overlays
    # with request-bound identity disable it and initialize user content after
    # login with the authenticated user_id.
    initialize_user_content_on_startup: bool = True

    # Custom URL scheme the desktop shell registers (Electron
    # ``setAsDefaultProtocolClient`` — see
    # frontend/apps/desktop/src/main/deep-link-utils.ts ``DEEP_LINK_PROTOCOL``).
    # The connector OAuth callback hands its result back to the running app via a
    # ``<scheme>://connector-oauth?...`` deep link. Keep in sync with the
    # frontend constant; override with ``VALUZ_DEEP_LINK_PROTOCOL`` for an
    # edition that ships under a different scheme.
    deep_link_protocol: str = "valuz-oss"

    # Explicit override for the shared secret guarding the internal MCP
    # endpoints (the ``X-Valuz-Internal`` header). When unset the token is
    # DERIVED from the stable local owner id (see ``internal_mcp_token``) so it
    # survives process restarts. Set this (env
    # ``VALUZ_INTERNAL_MCP_TOKEN_OVERRIDE``) to pin an explicit value. It's a
    # localhost-only guard against cross-origin / cross-user access to the
    # host's internal MCP tools.
    internal_mcp_token_override: str | None = None

    # Hard cap on attachments per session — counts local uploads and
    # KB-sourced references together. Both the multipart upload route
    # and the KB-attach route reject requests that would push the
    # session past this; the desktop UI greys out the attachment menu
    # entries once the count is reached. Override with
    # ``VALUZ_MAX_SESSION_ATTACHMENTS``.
    max_session_attachments: int = 20

    # Whether CLI-subscription model channels (Claude Pro·Max via ``claude
    # /login``, Codex via ``codex /login``) are offered. These authenticate
    # out-of-band against a **local** CLI keychain, so they only make sense
    # where that keychain exists — the desktop app and local/LAN headless runs.
    # A shared multi-user server (no per-user CLI keychain) sets this False so
    # the subscription templates are not surfaced in the providers list.
    # Override with ``VALUZ_SUBSCRIPTION_LOGIN_ENABLED``.
    subscription_login_enabled: bool = True

    # ── Installation identity ────────────────────────────────────────
    # Where the locally-generated owner id (int32) is persisted. Lives
    # OUTSIDE the business tables so a DB clean-up rebuild never loses it
    # (see ``infra.local_identity.resolve_local_user_id``). Assigned once
    # on first install from a device fingerprint and stable thereafter.
    installation_filename: str = "installation.json"

    # ── Logging paths ────────────────────────────────────────────────
    # ``infra.logging.configure_logging`` writes structured JSON lines
    # to ``log_file`` via a RotatingFileHandler so the desktop ``服务``
    # panel can display + offer "open in editor" without depending on
    # whichever shell launched the process. Logs are process-wide, not
    # user-owned data, so this deliberately does not derive from data_dir.
    # Override with VALUZ_LOG_DIR for cloud deployments that template
    # VALUZ_DATA_DIR by user. ``log_dir`` is created on first write — we don't
    # ``mkdir`` here so the field stays pure.
    log_dir: Path = Path.home() / ".valuz-oss" / "logs"
    log_filename: str = "backend.log"

    @property
    def log_file(self) -> Path:
        return self.log_dir / self.log_filename

    # Optional legacy skill-creator staging directory. May contain
    # ``{user_id}``; when unset it lives under ``data_dir(user_id)``.
    # Override with VALUZ_USER_SKILL_STAGING_DIR.
    user_skill_staging_dir: Path | None = None

    # Canonical user skill library directory. May contain ``{user_id}`` for
    # shared/cloud deployments.
    user_skills_dir: Path = Path.home() / ".agent" / "skills"

    @property
    def internal_mcp_token(self) -> str:
        """Shared secret gating the host's internal MCP endpoints
        (``/internal/mcp/*``), sent in the ``X-Valuz-Internal`` header.

        Derived deterministically from the stable local install owner id so it
        survives process restarts. Sessions bake this token into their stored
        ``mcp_servers`` headers, and the recovery/resume path replays those
        stored sessions — a per-boot random token would 403 every pre-restart
        session's internal-MCP calls (harness / docs / automations /
        connectors), breaking task recovery. ``internal_mcp_token_override``
        (env ``VALUZ_INTERNAL_MCP_TOKEN_OVERRIDE``) still takes precedence for
        tests and explicit configuration.
        """
        if self.internal_mcp_token_override:
            return self.internal_mcp_token_override
        # Lazy import avoids a config <-> local_identity import cycle
        # (local_identity imports ``settings``).
        from valuz_agent.infra.local_identity import resolve_local_user_id

        owner = resolve_local_user_id()
        return hashlib.sha256(b"valuz-internal-mcp\x00" + owner.encode("utf-8")).hexdigest()

    # ── User-facing project root ───────────────────────────────────
    # Base directory for user-visible projects (not hidden).
    # Defaults to ~/Valuz; override with VALUZ_USER_PROJECT_ROOT.
    # May contain {user_id} when the deployment mounts per-user workspaces.
    user_project_root: Path = Path.home() / "Valuz"

    # ── Browser feature (chrome-devtools-mcp) ──────────────────────
    # A dedicated, persistent Chrome profile the managed browser uses —
    # an ISOLATED profile (never the user's everyday Chrome) so a
    # full-access agent's blast radius is contained to whatever the user
    # logs into HERE. Sibling of docs/secrets under data_dir. See
    # docs/design/browser-feature-*.md.
    browser_profile_subdir: str = "browser-chrome"
    # Daemon mode: "managed" launches a visible Chrome on the dedicated
    # profile; "attach" connects to a user-launched Chrome at
    # ``browser_attach_url`` (started with --remote-debugging-port).
    # Override with VALUZ_BROWSER_MODE.
    browser_mode: Literal["managed", "attach"] = "managed"
    browser_attach_url: str = "http://127.0.0.1:9222"
    # Pinned chrome-devtools-mcp version. The CLI is invoked via
    # ``npx -p chrome-devtools-mcp@<ver> chrome-devtools``; pinning keeps
    # the bundled skill's command vocabulary in sync. GA vendors the bin
    # and sets VALUZ_CDT_PATH instead of npx.
    chrome_devtools_version: str = "1.2.0"

    model_config = {"env_prefix": "VALUZ_"}


settings = Settings()
