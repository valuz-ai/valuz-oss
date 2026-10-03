"""PluginHost: ordering, failure semantics, unload, config, toggles."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from valuz_agent.plugin_host import (
    BackendPluginBase,
    DuplicatePluginError,
    InvalidPluginError,
    MissingNeedError,
    PluginCycleError,
    PluginHost,
    PluginHostError,
    PluginStartupError,
)


class _Ext:
    """Minimal stand-in for the OSS ``ext`` container."""

    def __init__(self) -> None:
        self.billing = "noop-billing"
        self.cache = "file-cache"
        self.always_on_mcp_specs: list[Any] = []


class _Registry:
    def __init__(self) -> None:
        self.entries: list[Any] = []

    def register(self, *args: Any, **kwargs: Any) -> object:
        entry = object()
        self.entries.append((entry, args, kwargs))
        return entry

    def unregister(self, entry: object) -> None:
        self.entries = [e for e in self.entries if e[0] is not entry]


def make_plugin(
    pid: str,
    *,
    needs: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    required: bool = False,
    apply: Any = None,
    config_cls: type[BaseModel] | None = None,
    log: list[str] | None = None,
) -> BackendPluginBase:
    class _P(BackendPluginBase):
        def apply(self, ctx: Any, config: Any) -> None:
            if log is not None:
                log.append(pid)
            if apply is not None:
                apply(ctx, config)

    _P.id = pid
    _P.needs = needs
    _P.provides = provides
    _P.required = required
    _P.Config = config_cls
    return _P()


def host_of(*plugins: Any, **kw: Any) -> PluginHost:
    return PluginHost(
        plugins,
        extensions=kw.pop("extensions", _Ext()),
        module_registry=kw.pop("module_registry", _Registry()),
        middleware_registry=kw.pop("middleware_registry", _Registry()),
        **kw,
    )


# -- ordering ---------------------------------------------------------------


def test_registration_order_is_kept_when_nothing_constrains_it() -> None:
    log: list[str] = []
    host = host_of(*(make_plugin(p, log=log) for p in "abcd"))
    host.load_all()
    assert log == ["a", "b", "c", "d"]


def test_needs_and_provides_reorder_activation() -> None:
    log: list[str] = []
    host = host_of(
        make_plugin("late", needs=("svc",), log=log),
        make_plugin("free", log=log),
        make_plugin("maker", provides=("svc",), log=log),
    )
    host.load_all()
    assert log == ["free", "maker", "late"]
    assert host.order() == ["free", "maker", "late"]


def test_a_need_satisfied_by_the_base_needs_no_provider() -> None:
    host = host_of(make_plugin("p", needs=("billing",)))
    host.load_all()
    assert host.get("p").status == "active"


def test_a_provider_still_orders_before_dependents_of_a_base_port() -> None:
    log: list[str] = []
    host = host_of(
        make_plugin("user", needs=("billing",), log=log),
        make_plugin("binder", provides=("billing",), log=log),
    )
    host.load_all()
    assert log == ["binder", "user"]


def test_missing_need_is_an_error() -> None:
    host = host_of(make_plugin("p", needs=("nowhere",)))
    with pytest.raises(MissingNeedError) as info:
        host.load_all()
    assert (info.value.plugin_id, info.value.need) == ("p", "nowhere")


def test_cycle_is_an_error() -> None:
    host = host_of(
        make_plugin("a", needs=("b-svc",), provides=("a-svc",)),
        make_plugin("b", needs=("a-svc",), provides=("b-svc",)),
        make_plugin("c"),
    )
    with pytest.raises(PluginCycleError) as info:
        host.order()
    assert sorted(info.value.members) == ["a", "b"]


def test_duplicate_ids_and_malformed_plugins_are_rejected() -> None:
    host = host_of(make_plugin("a"))
    with pytest.raises(DuplicatePluginError):
        host.register(make_plugin("a"))
    with pytest.raises(InvalidPluginError):
        host.register(object())  # type: ignore[arg-type]


# -- failure semantics ------------------------------------------------------


def test_an_optional_failure_is_rolled_back_logged_and_recorded() -> None:
    ext = _Ext()

    def boom(ctx: Any, config: Any) -> None:
        ctx.ports.bind("billing", "half-done")
        raise RuntimeError("kaput")

    host = host_of(make_plugin("flaky", apply=boom), make_plugin("fine"), extensions=ext)
    host.load_all()

    assert ext.billing == "noop-billing"  # rolled back
    flaky = host.get("flaky")
    assert flaky.status == "failed" and "kaput" in (flaky.error or "")
    assert host.get("fine").status == "active"


def test_a_required_failure_raises_and_unwinds_everything_loaded() -> None:
    ext = _Ext()
    host = host_of(
        make_plugin("first", apply=lambda ctx, _: ctx.ports.bind("cache", "mine")),
        make_plugin("must", required=True, apply=lambda ctx, _: 1 / 0),
        extensions=ext,
    )
    with pytest.raises(PluginStartupError) as info:
        host.load_all()
    assert info.value.plugin_id == "must"
    assert isinstance(info.value.__cause__, ZeroDivisionError)
    assert ext.cache == "file-cache"
    assert host.get("first").status == "disposed"


def test_a_dependent_of_a_failed_optional_provider_is_skipped_not_started() -> None:
    log: list[str] = []
    host = host_of(
        make_plugin("maker", provides=("svc",), apply=lambda c, _: 1 / 0, log=log),
        make_plugin("user", needs=("svc",), log=log),
    )
    host.load_all()
    assert log == ["maker"]
    assert host.get("user").status == "failed"
    assert "svc" in (host.get("user").error or "")


def test_a_required_plugin_with_an_unmet_need_aborts_startup() -> None:
    host = host_of(
        make_plugin("maker", provides=("svc",), apply=lambda c, _: 1 / 0),
        make_plugin("user", needs=("svc",), required=True),
    )
    with pytest.raises(PluginStartupError):
        host.load_all()


def test_rollback_runs_disposers_in_reverse_and_survives_a_broken_one() -> None:
    calls: list[str] = []

    def build(ctx: Any, config: Any) -> None:
        ctx.on_dispose(lambda: calls.append("first"))
        ctx.on_dispose(lambda: (_ for _ in ()).throw(RuntimeError("bad disposer")))
        ctx.on_dispose(lambda: calls.append("last"))
        raise ValueError("apply failed")

    host = host_of(make_plugin("p", apply=build))
    host.load_all()
    assert calls == ["last", "first"]
    assert "apply failed" in (host.get("p").error or "")


# -- unload / status --------------------------------------------------------


def test_unload_disposes_dependents_first_and_restores_ports() -> None:
    ext = _Ext()
    host = host_of(
        make_plugin("base", provides=("svc",), apply=lambda c, _: c.ports.bind("billing", "A")),
        make_plugin("child", needs=("svc",), apply=lambda c, _: c.ports.bind("cache", "B")),
        make_plugin("other"),
        extensions=ext,
    )
    host.load_all()
    assert (ext.billing, ext.cache) == ("A", "B")

    assert host.unload("base") == ["child", "base"]
    assert (ext.billing, ext.cache) == ("noop-billing", "file-cache")
    states = {i.id: i.status for i in host.list()}
    assert states == {"base": "disposed", "child": "disposed", "other": "active"}
    assert host.unload("base") == []  # idempotent


def test_list_reports_declarations_and_bound_ports() -> None:
    host = host_of(
        make_plugin(
            "p",
            needs=("billing",),
            provides=("x",),
            required=True,
            apply=lambda c, _: c.ports.bind("cache", "k"),
        )
    )
    host.load_all()
    (info,) = host.list()
    assert info.to_dict() == {
        "id": "p",
        "status": "active",
        "required": True,
        "needs": ["billing"],
        "provides": ["x"],
        "entitlement": None,
        "error": None,
        "hasConfig": False,
        "desiredEnabled": True,
        "boundPorts": ["cache"],
    }


def test_lifespan_hooks_come_back_unwrapped_in_load_order() -> None:
    def hook_a(app: Any) -> None: ...
    def hook_b(app: Any) -> None: ...

    host = host_of(
        make_plugin("b", needs=("a-svc",), apply=lambda c, _: c.lifespan(hook_b)),
        make_plugin("a", provides=("a-svc",), apply=lambda c, _: c.lifespan(hook_a)),
    )
    host.load_all()
    assert host.lifespan_hooks() == [hook_a, hook_b]
    host.unload("a")
    assert host.lifespan_hooks() == []


# -- config / toggles -------------------------------------------------------


class _Cfg(BaseModel):
    limit: int = 3
    label: str = "x"


def test_config_is_validated_and_passed_to_apply() -> None:
    seen: list[Any] = []
    host = host_of(make_plugin("p", config_cls=_Cfg, apply=lambda c, cfg: seen.append(cfg)))
    host.load_all({"p": {"limit": 9}})
    assert seen == [_Cfg(limit=9, label="x")]


def test_an_invalid_config_fails_the_plugin_not_the_host() -> None:
    host = host_of(make_plugin("p", config_cls=_Cfg), make_plugin("q"))
    host.load_all({"p": {"limit": "many"}})
    assert host.get("p").status == "failed"
    assert host.get("q").status == "active"


def test_config_schema_export_is_json_schema_2020_12() -> None:
    host = host_of(make_plugin("p", config_cls=_Cfg), make_plugin("q"))
    schemas = host.config_schemas()
    assert set(schemas) == {"p"}
    assert schemas["p"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schemas["p"]["properties"]["limit"]["type"] == "integer"


def test_backend_toggles_and_config_edits_are_always_restart_required() -> None:
    host = host_of(make_plugin("p", config_cls=_Cfg))
    host.load_all()
    assert host.set_enabled("p", False) == "restart-required"
    assert host.get("p").status == "active"  # nothing changed in the running process
    assert host.edit_config("p", {"limit": 5}) == "restart-required"
    with pytest.raises(ValidationError):
        host.edit_config("p", {"limit": "no"})
    with pytest.raises(PluginHostError):
        host.set_enabled("ghost", True)


def test_a_plugin_disabled_for_the_next_start_is_not_applied() -> None:
    log: list[str] = []
    host = host_of(make_plugin("p", log=log), make_plugin("q", log=log))
    host.set_enabled("p", False)
    host.load_all(disabled=("q",))
    assert log == []
    assert [i.status for i in host.list()] == ["disposed", "disposed"]


# -- migrations -------------------------------------------------------------


def test_migration_declarations_are_static_and_need_no_load() -> None:
    class _WithChain(BackendPluginBase):
        id = "m"

        def apply(self, ctx: Any, config: Any) -> None:
            raise AssertionError("must not be applied to read migrations")

        def migrations(self) -> list[str]:
            return ["chain-1"]

    host = host_of(_WithChain(), make_plugin("plain"))
    assert host.migration_chains() == [("m", "chain-1")]
