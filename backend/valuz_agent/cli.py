"""Typer-based valuz-agent CLI — backend-internal management commands only.

These are implementation-level entry points, not the user-facing product
CLI. The product CLI lives at root ``cli/`` (Go, ``valuz``) per
``docs/STRUCTURE.md``. User-facing surfaces (``valuz schedule``,
``valuz install-autostart``, ``valuz doctor``) are now provided by the Go
CLI; this file keeps only the commands the backend itself needs to expose:

- ``serve`` — run uvicorn (used by dev scripts and the packaged
  ``valuz-server`` entrypoint).
- ``reset-providers`` — restore provider rows to a known-good state.
- ``cleanup-seed-agents`` — delete legacy official team-role agents that were
  seeded before onboarding-driven creation (keeps the system default-assistant;
  skips any agent still deployed into a project).

There is intentionally no ``init-db``: the host schema is owned by Alembic
(``run_host_migrations``), which the app applies automatically at startup. A
``create_all``-based bootstrap would lay down tables without an
``alembic_version_host`` stamp, diverging from the migration chain. Tests
build their schema directly via ``Base.metadata.create_all`` in fixtures.
"""

from __future__ import annotations

import asyncio
import os

import typer
import uvicorn

from valuz_agent.infra.config import settings

app = typer.Typer(name="valuz-agent", add_completion=False)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
    headless: bool = typer.Option(
        False,
        "--headless",
        help=(
            "Mark this process as an always-on backend launched by a "
            "service supervisor (launchd / systemd / etc.). The flag is "
            "consumed by external supervisors and probe logic in the "
            "product CLI; the running uvicorn server behaves the same."
        ),
    ),
) -> None:
    """Start the Valuz Agent backend server."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if headless:
        os.environ["VALUZ_HEADLESS"] = "1"
    uvicorn.run(
        "valuz_agent.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


@app.command(name="reset-providers")
def reset_providers_cmd(
    drop_table: bool = typer.Option(
        False,
        "--drop-table",
        help="Drop and recreate ``valuz_provider`` (fixes stale schema "
        "from older code, not just stale row data).",
    ),
) -> None:
    """Reset the provider table to a known-good state."""
    from valuz_agent.boot.schema import run_host_migrations
    from valuz_agent.infra.database import async_engine
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.providers.datastore import ProviderDatastore
    from valuz_agent.modules.providers.service import ProviderListItem, reset_providers

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # Ensure the host schema exists via the SAME path as app startup — Alembic
    # upgrade head (idempotent; a no-op against an already-current DB). Using
    # ``Base.metadata.create_all`` here would build tables without an
    # ``alembic_version_host`` stamp, which the next ``serve`` boot would wipe.
    run_host_migrations()

    async def _run() -> list[ProviderListItem]:
        from valuz_agent.infra.local_identity import resolve_local_user_id

        async with async_unit_of_work() as db:
            ds = ProviderDatastore(db)
            return await reset_providers(
                ds,
                resolve_local_user_id(),
                drop_table=drop_table,
                engine=async_engine if drop_table else None,
            )

    providers = asyncio.run(_run())

    typer.echo(f"Reset complete — {len(providers)} provider(s) now in valuz_provider:")
    for ch in providers:
        marker = " (default)" if ch.is_default else ""
        typer.echo(
            f"  • {ch.id:<14} provider={ch.provider_kind:<10} "
            f"model={ch.default_model or '—':<20}{marker}"
        )


@app.command(name="cleanup-seed-agents")
def cleanup_seed_agents_cmd() -> None:
    """Delete legacy official team-role agents seeded before onboarding-driven
    creation. Keeps the system ``default-assistant``; skips any agent still
    deployed into a project (``delete_agent`` raises for those). Safe to re-run.
    """
    from valuz_agent.boot.schema import run_host_migrations
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.agents.service import AgentService
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    run_host_migrations()

    keep = {"default-assistant"}

    async def _run() -> tuple[list[str], list[tuple[str, str]]]:
        deleted: list[str] = []
        skipped: list[tuple[str, str]] = []
        async with async_unit_of_work() as db:
            connector_svc = ConnectorService(
                datastore=ConnectorDatastore(db),
                secrets=None,  # type: ignore[arg-type]
            )
            svc = AgentService(db=db, connector_service=connector_svc)  # type: ignore[arg-type]
            for row in await svc.list_agents(source="official"):
                if row.slug in keep:
                    continue
                try:
                    await svc.delete_agent(row.slug)
                    deleted.append(row.slug)
                except Exception as exc:  # noqa: BLE001 — report + keep going
                    skipped.append((row.slug, type(exc).__name__))
        return deleted, skipped

    deleted, skipped = asyncio.run(_run())
    typer.echo(f"Cleanup complete — deleted {len(deleted)} legacy official agent(s):")
    for slug in deleted:
        typer.echo(f"  • {slug}")
    if skipped:
        typer.echo(f"Skipped {len(skipped)} (deployed or protected):")
        for slug, why in skipped:
            typer.echo(f"  • {slug} ({why})")


if __name__ == "__main__":
    app()
