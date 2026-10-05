"""PluginContext helpers: every contribution is undoable and order-safe."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from valuz_agent.infra.middleware_registry import MiddlewareOrder, MiddlewareRegistry
from valuz_agent.infra.module_registry import ModuleRegistry
from valuz_agent.plugin_host import BackendPluginBase, PluginHost, UnknownPortError


class _Ext:
    def __init__(self) -> None:
        self.billing = "noop"
        self.cache = "file"
        self.specs: list[str] = []
        self.always_on_mcp_specs: list[Any] = []


def _plugin(pid: str, apply: Any, **attrs: Any) -> BackendPluginBase:
    class _P(BackendPluginBase):
        def apply(self, ctx: Any, config: Any) -> None:
            apply(ctx)

    _P.id = pid
    for key, value in attrs.items():
        setattr(_P, key, value)
    return _P()


def _host(*plugins: Any, ext: Any = None, **kw: Any) -> tuple[PluginHost, Any]:
    ext = ext or _Ext()
    host = PluginHost(
        plugins,
        extensions=ext,
        module_registry=kw.pop("module_registry", ModuleRegistry()),
        middleware_registry=kw.pop("middleware_registry", MiddlewareRegistry()),
        **kw,
    )
    return host, ext


# -- ports ------------------------------------------------------------------


def test_bind_restores_the_previous_binding_on_dispose() -> None:
    host, ext = _host(_plugin("p", lambda c: c.ports.bind("billing", "A")))
    host.load_all()
    assert ext.billing == "A"
    host.unload("p")
    assert ext.billing == "noop"


def test_stacked_binds_unwind_to_the_original() -> None:
    host, ext = _host(
        _plugin("one", lambda c: c.ports.bind("billing", "A"), provides=("one",)),
        _plugin("two", lambda c: c.ports.bind("billing", "B"), needs=("one",)),
    )
    host.load_all()
    assert ext.billing == "B"
    host.unload("one")  # cascades: two first, then one
    assert ext.billing == "noop"


def test_an_out_of_order_dispose_does_not_clobber_a_later_binding() -> None:
    disposers: dict[str, Any] = {}
    host, ext = _host(
        _plugin("one", lambda c: disposers.setdefault("one", c.ports.bind("billing", "A"))),
        _plugin("two", lambda c: c.ports.bind("billing", "B")),
    )
    host.load_all()
    disposers["one"]()  # early dispose of the *earlier* binding
    assert ext.billing == "B"


def test_binding_an_unknown_port_is_an_error_not_a_new_attribute() -> None:
    def apply(ctx: Any) -> None:
        with pytest.raises(UnknownPortError, match="biling"):
            ctx.ports.bind("biling", "A")
        with pytest.raises(UnknownPortError):
            ctx.ports.append("nope", 1)

    host, ext = _host(_plugin("p", apply))
    host.load_all()
    assert host.get("p").status == "active"
    assert not hasattr(ext, "biling")


def test_an_unhandled_unknown_port_fails_the_plugin() -> None:
    host, _ = _host(_plugin("p", lambda c: c.ports.bind("biling", "A")))
    host.load_all()
    assert host.get("p").status == "failed" and "biling" in (host.get("p").error or "")


def test_has_names_and_get_mirror_the_container() -> None:
    seen: dict[str, Any] = {}

    def probe(ctx: Any) -> None:
        seen["has"] = (ctx.ports.has("billing"), ctx.ports.has("nope"))
        seen["names"] = ctx.ports.names()
        seen["get"] = ctx.ports.get("cache")

    host, _ = _host(_plugin("p", probe))
    host.load_all()
    assert seen["has"] == (True, False)
    assert "billing" in seen["names"] and seen["get"] == "file"


def test_append_removes_exactly_what_it_added_and_can_dedupe() -> None:
    spec = "spec-a"

    def apply(ctx: Any) -> None:
        ctx.ports.append("specs", spec)
        ctx.ports.append("specs", spec, unique=True)  # no second copy, nothing to undo
        ctx.mcp_always_on({"name": "x"}, unique=True)

    ext = _Ext()
    ext.specs.append("preexisting")
    host, _ = _host(_plugin("p", apply), ext=ext)
    host.load_all()
    assert ext.specs == ["preexisting", "spec-a"]
    assert ext.always_on_mcp_specs == [{"name": "x"}]
    host.unload("p")
    assert ext.specs == ["preexisting"] and ext.always_on_mcp_specs == []


def test_restore_on_dispose_covers_helpers_that_mutate_ext_directly() -> None:
    def apply(ctx: Any) -> None:
        ctx.ports.restore_on_dispose("billing", "specs")
        ext.billing = "legacy-bound"
        ext.specs.append("legacy-spec")

    ext = _Ext()
    host, _ = _host(_plugin("p", apply), ext=ext)
    host.load_all()
    assert ext.billing == "legacy-bound"
    host.unload("p")
    assert ext.billing == "noop" and ext.specs == []


# -- routes / middleware ----------------------------------------------------


def test_routes_include_registers_then_unregisters() -> None:
    registry = ModuleRegistry()
    router = APIRouter()

    @router.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    host, _ = _host(
        _plugin("p", lambda c: c.routes.include(router, "/v1/x", ["x"], name="x")),
        module_registry=registry,
    )
    host.load_all()
    assert registry.registered_names == ["x"]

    app = FastAPI()
    registry.apply(app)
    assert TestClient(app).get("/v1/x/ping").json() == {"ok": True}

    host.unload("p")
    assert registry.registered_names == []


def test_routes_include_defaults_the_name_from_tags_then_plugin_id() -> None:
    registry = ModuleRegistry()
    host, _ = _host(
        _plugin("one", lambda c: c.routes.include(APIRouter(), "/a", ["tagged"])),
        _plugin("two", lambda c: c.routes.include(APIRouter(), "/b")),
        module_registry=registry,
    )
    host.load_all()
    assert registry.registered_names == ["tagged", "two"]


def test_routes_include_tolerates_a_registry_without_unregister() -> None:
    class _Bare:
        def __init__(self) -> None:
            self.names: list[str] = []

        def register(self, name: str, *a: Any, **k: Any) -> None:
            self.names.append(name)

    bare = _Bare()
    host, _ = _host(
        _plugin("p", lambda c: c.routes.include(APIRouter(), "/a", name="a")), module_registry=bare
    )
    host.load_all()
    host.unload("p")  # must not raise
    assert bare.names == ["a"]


def test_middleware_add_goes_through_the_ordered_registry_and_back() -> None:
    registry = MiddlewareRegistry()

    class _M(BaseHTTPMiddleware):
        async def dispatch(self, request: Any, call_next: Any) -> Any:
            return await call_next(request)

    host, _ = _host(
        _plugin("p", lambda c: c.middleware.add(_M, MiddlewareOrder.AUDIT, flag=True)),
        middleware_registry=registry,
    )
    host.load_all()
    assert registry.registered_count == 1
    app = FastAPI()
    registry.apply(app)
    assert app.user_middleware[0].cls is _M and app.user_middleware[0].kwargs == {"flag": True}
    host.unload("p")
    assert registry.registered_count == 0


# -- facets / app phase -----------------------------------------------------


def test_facets_are_plain_labels_the_plugin_consults() -> None:
    log: list[str] = []

    def apply(ctx: Any) -> None:
        if ctx.wants("ports"):
            log.append("ports")
        if ctx.wants("routes"):
            log.append("routes")
        log.append(ctx.role)

    host, _ = _host(_plugin("p", apply), role="worker", facets=("ports",))
    host.load_all()
    assert log == ["ports", "worker"]


def test_after_app_runs_in_load_order_with_an_undoable_app_api() -> None:
    order: list[str] = []

    class _Mid(BaseHTTPMiddleware):
        async def dispatch(self, request: Any, call_next: Any) -> Any:
            return await call_next(request)

    def late(ctx: Any) -> None:
        ctx.after_app(lambda api: order.append("late"))

    def early(ctx: Any) -> None:
        def after(api: Any) -> None:
            order.append("early")
            api.add_middleware(_Mid)
            api.set_state("flag", 1)
            router = APIRouter()

            @router.get("/late")
            def _late() -> str:
                return "ok"

            api.include_router(router, prefix="/p")
            api.mount("/sub", FastAPI())

        ctx.after_app(after)

    host, _ = _host(
        _plugin("late", late, needs=("early-svc",)),
        _plugin("early", early, provides=("early-svc",)),
    )
    host.load_all()
    app = FastAPI()
    host.attach_app(app)

    assert order == ["early", "late"]
    assert [m.cls for m in app.user_middleware] == [_Mid]
    assert app.state.flag == 1
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/p/late", "/sub"} <= paths

    host.unload("early")
    assert app.user_middleware == []
    assert not hasattr(app.state, "flag")
    assert "/p/late" not in {getattr(r, "path", None) for r in app.routes}


def test_an_optional_after_app_failure_is_recorded_and_rolled_back() -> None:
    def apply(ctx: Any) -> None:
        ctx.ports.bind("billing", "A")

        def after(api: Any) -> None:
            raise RuntimeError("late failure")

        ctx.after_app(after)

    host, ext = _host(_plugin("p", apply))
    host.load_all()
    host.attach_app(FastAPI())
    assert host.get("p").status == "failed"
    assert ext.billing == "noop"


# -- hook bus (ctx.hooks / ctx.commands) -------------------------------------------


def test_hooks_and_commands_belong_to_the_plugin_and_unwind() -> None:
    import valuz_agent.boot.kernel  # noqa: F401 — sys.path for ``src``
    from src.core.hooks import TOOL_CALL, command_registry, hook_registry

    async def handler(ctx: Any, event: Any, next_: Any) -> Any:
        return await next_()

    async def tally(session: Any, args: str) -> str:
        return args

    def apply(ctx: Any) -> None:
        ctx.hooks.on(TOOL_CALL, handler, matcher={"tool.kind": "shell"})
        ctx.commands.register("tally-plugin-test", tally, description="count")

    host, _ = _host(_plugin("hooking-plugin", apply))
    host.load_all()
    try:
        owners = {(h["owner"], h["event"]) for h in hook_registry.owners()}
        assert ("hooking-plugin", TOOL_CALL) in owners
        spec = command_registry.get("tally-plugin-test")
        assert spec is not None and spec.owner == "hooking-plugin"
    finally:
        host.unload("hooking-plugin")
    assert all(h["owner"] != "hooking-plugin" for h in hook_registry.owners())
    assert command_registry.get("tally-plugin-test") is None
