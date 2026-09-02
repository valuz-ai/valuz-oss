"""Where the skill routes actually mount.

``routes/skills.py`` builds its router with ``APIRouter(tags=[...])`` and no
prefix, so every path in the file is written in full. A relative path there
mounts at the APPLICATION root — and because the auth dependency still runs,
it answers 401, which reads as "alive" from outside while the documented
path returns 404. That is exactly how the version endpoints shipped
unreachable: the service-level tests passed because they never went through
HTTP.

So this pins the mounted paths themselves.
"""

from __future__ import annotations

from valuz_agent.api.routes import skills as skills_routes

VERSION_PATHS = {
    "/v1/skills/{skill_id}/versions",
    "/v1/skills/{skill_id}/versions/{revision_id}/files",
    "/v1/skills/{skill_id}/versions/{revision_id}/restore",
}


def _mounted_paths() -> set[str]:
    return {route.path for route in skills_routes.router.routes}  # type: ignore[attr-defined]


def test_version_endpoints_mount_under_the_documented_prefix() -> None:
    assert VERSION_PATHS <= _mounted_paths()


def test_no_skill_route_mounts_at_the_application_root() -> None:
    """Every path in this router starts with a known top-level segment.

    A path that does not is a relative path someone wrote assuming a router
    prefix that isn't there — it would squat on the application root."""
    stray = sorted(
        path for path in _mounted_paths() if not path.startswith(("/v1/skills", "/v1/projects"))
    )
    assert not stray, f"routes mounted outside the skills namespace: {stray}"
