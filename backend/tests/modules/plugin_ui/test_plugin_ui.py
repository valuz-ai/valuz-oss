"""The UI bus: element trees, how providers combine, actions, pushes."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from valuz_agent.modules.plugin_ui import (
    InvalidTree,
    StaleAction,
    UiPushHub,
    UiRegistry,
    UiRequest,
    normalize_tree,
)

REQ = UiRequest(site="conversation.composer.dock", instance="s1", user_id="u1", session_id="s1")


# -- element trees ----------------------------------------------------------------------


def test_normalize_keeps_known_props_and_drops_the_rest() -> None:
    tree = normalize_tree(
        {
            "type": "Box",
            "props": {"direction": "row", "gap": 2, "onClick": "evil()"},
            "children": [
                "plain text",
                {"type": "Text", "props": {"text": "hi", "tone": "muted", "color": "red"}},
                {"type": "Button", "props": {"label": "Go", "action": "go", "variant": "outline"}},
                {"type": "Select", "props": {"action": "pick", "options": [{"value": "a"}, 3]}},
                {"type": "Link", "props": {"href": "javascript:alert(1)", "text": "x"}},
            ],
        }
    )
    assert tree["props"] == {"direction": "row", "gap": 2}
    assert tree["children"][0] == "plain text"
    assert tree["children"][1]["props"] == {"text": "hi", "tone": "muted"}
    assert tree["children"][3]["props"]["options"] == [{"value": "a", "label": "a"}]
    assert "href" not in tree["children"][4]["props"]


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "Script"},
        {"type": "Text", "children": ["x"]},
        {"type": "Image", "props": {"src": "file:///etc/passwd"}},
        42,
        {"type": "Box", "children": "not a list"},
    ],
)
def test_normalize_rejects_bad_trees(bad: Any) -> None:
    with pytest.raises(InvalidTree):
        normalize_tree(bad)


def test_normalize_bounds_size() -> None:
    with pytest.raises(InvalidTree):
        normalize_tree({"type": "Box", "children": [{"type": "Text"}] * 600})


# -- providers --------------------------------------------------------------------------


async def test_list_site_draws_each_provider_and_skips_failures() -> None:
    registry = UiRegistry()

    async def first(request: UiRequest, inner: Any) -> Any:
        return {"type": "Text", "props": {"text": f"one {request.session_id}"}}

    async def broken(request: UiRequest, inner: Any) -> Any:
        raise RuntimeError("bug")

    async def invalid(request: UiRequest, inner: Any) -> Any:
        return {"type": "Iframe"}

    async def nothing(request: UiRequest, inner: Any) -> Any:
        return None

    registry.provide(REQ.site, first, owner="a")
    registry.provide(REQ.site, broken, owner="b")
    registry.provide(REQ.site, invalid, owner="c")
    registry.provide(REQ.site, nothing, owner="d")
    items = await registry.render(REQ)
    assert [item["owner"] for item in items] == ["a"]
    assert items[0]["tree"] == {"type": "Text", "props": {"text": "one s1"}}
    assert registry.sites() == [{"site": REQ.site, "kind": "list"}]


async def test_single_site_chains_like_mods_next() -> None:
    registry = UiRegistry()
    site = "conversation.tool-call"
    request = UiRequest(site=site, instance="t1", user_id="u1")

    async def inner_wrap(request: UiRequest, inner: Any) -> Any:
        return {"type": "Box", "children": [inner, {"type": "Badge", "props": {"text": "inner"}}]}

    async def outer_wrap(request: UiRequest, inner: Any) -> Any:
        return {"type": "Box", "props": {"border": True}, "children": [inner]}

    async def passthrough(request: UiRequest, inner: Any) -> Any:
        return None

    registry.provide(site, inner_wrap, owner="inner", kind="single", priority=10)
    registry.provide(site, outer_wrap, owner="outer", kind="single", priority=0)
    registry.provide(site, passthrough, owner="quiet", kind="single", priority=5)
    [item] = await registry.render(request)
    assert item["owner"] == "outer"
    assert item["tree"] == {
        "type": "Box",
        "props": {"border": True},
        "children": [
            {
                "type": "Box",
                "children": [{"type": "Default"}, {"type": "Badge", "props": {"text": "inner"}}],
            }
        ],
    }
    with pytest.raises(ValueError):
        registry.provide(site, outer_wrap, owner="x", kind="list")


async def test_single_site_with_only_passthrough_draws_nothing() -> None:
    registry = UiRegistry()

    async def passthrough(request: UiRequest, inner: Any) -> Any:
        return None

    registry.provide("conversation.message.user", passthrough, owner="q", kind="single")
    assert (
        await registry.render(
            UiRequest(site="conversation.message.user", instance="m", user_id="u")
        )
        == []
    )


async def test_keyed_sites_and_owner_cleanup() -> None:
    registry = UiRegistry()

    async def pane(request: UiRequest, inner: Any) -> Any:
        return {"type": "Markdown", "props": {"text": "# council"}}

    registry.provide(
        "context-panel.tabs", pane, owner="p", kind="keyed", key="council", label="Council"
    )
    assert registry.sites() == [
        {"site": "context-panel.tabs", "kind": "keyed", "key": "council", "label": "Council"}
    ]
    [item] = await registry.render(UiRequest(site="context-panel.tabs", instance="x", user_id="u"))
    assert item["key"] == "council"
    assert registry.unregister_owner("p") == 1
    assert registry.sites() == []


async def test_actions_reach_the_drawer_and_stale_clicks_are_refused() -> None:
    registry = UiRegistry()
    clicks: list[tuple[str, Any]] = []

    async def render(request: UiRequest, inner: Any) -> Any:
        return {"type": "Button", "props": {"label": f"clicked {len(clicks)}", "action": "inc"}}

    async def on_action(request: UiRequest, action: str, value: Any) -> None:
        clicks.append((action, value))

    registry.provide(REQ.site, render, owner="counter", on_action=on_action)
    [first] = await registry.render(REQ)
    [second] = await registry.render(REQ)
    with pytest.raises(StaleAction):
        await registry.act(
            REQ, owner="counter", action="inc", value=None, generation=first["generation"]
        )
    await registry.act(REQ, owner="counter", action="inc", value=1, generation=second["generation"])
    assert clicks == [("inc", 1)]
    with pytest.raises(KeyError):
        await registry.act(REQ, owner="nobody", action="x", value=None, generation=99)


# -- pushes and the event streams ------------------------------------------------------------


async def test_push_hub_delivers_to_matching_streams_only() -> None:
    hub = UiPushHub()
    received: list[str] = []

    async def listen() -> None:
        async for push in hub.subscribe("u1", "s1"):
            received.append(push.event_type + ":" + push.payload.get("text", ""))
            return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    assert hub.push("u1", "toast", {"text": "other session"}, session_id="s2") == 0
    assert hub.push("u1", "toast", {"text": "hello"}, session_id="s1") == 1
    await asyncio.wait_for(task, 2)
    assert received == ["ui.toast:hello"]
    with pytest.raises(ValueError):
        hub.push("u1", "explode")


async def test_ui_pushes_ride_the_session_event_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    from valuz_agent.adapters import event_sse_adapter
    from valuz_agent.modules.plugin_ui import ui_push_hub

    async def no_history(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def idle(*_args: Any, **_kwargs: Any):  # noqa: ANN202
        await asyncio.Event().wait()
        yield None

    monkeypatch.setattr(event_sse_adapter.kernel_client, "get_events", no_history)
    monkeypatch.setattr(event_sse_adapter.kernel_client, "subscribe_session_events", idle)
    monkeypatch.setattr(event_sse_adapter.kernel_client, "subscribe_session_events_existing", idle)

    stream = event_sse_adapter.iter_events_sse("s-ui", "u-ui")

    async def first_ui_frame() -> dict[str, Any]:
        async for item in stream:
            if item.get("event", "").startswith("ui."):
                return item
        raise AssertionError("stream ended")

    reader = asyncio.create_task(first_ui_frame())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if ui_push_hub.push(
            "u-ui", "status", {"owner": "p", "text": "indexing"}, session_id="s-ui"
        ):
            break
    item = await asyncio.wait_for(reader, 5)
    await stream.aclose()
    frame = json.loads(item["data"])
    assert frame["event_type"] == "ui.status"
    assert frame["seq"] == 0 and frame["event_uid"] is None
    push_id = frame["payload"].pop("push_id")
    assert len(push_id) == 32
    assert frame["payload"] == {"owner": "p", "text": "indexing"}


async def _first_user_stream_frame(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any] | None:
    from valuz_agent.adapters import event_sse_adapter

    async def no_history(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def idle(*_args: Any, **_kwargs: Any):  # noqa: ANN202
        await asyncio.Event().wait()
        yield None

    monkeypatch.setattr(event_sse_adapter, "list_user_events_after", no_history)
    monkeypatch.setattr(event_sse_adapter.kernel_client, "subscribe_all_events_for", idle)
    monkeypatch.setattr(event_sse_adapter, "IDLE_HEARTBEAT_SECONDS", 0.05)
    stream = event_sse_adapter.iter_user_events_sse("u-announce")
    try:
        item = await asyncio.wait_for(stream.__anext__(), 5)
    finally:
        await stream.aclose()
    return None if item["event"] == "heartbeat" else item


async def test_user_stream_announces_plugin_ui_only_when_some_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from valuz_agent.modules.plugin_ui import ui_registry

    assert await _first_user_stream_frame(monkeypatch) is None  # heartbeat first

    async def draw(request: UiRequest, inner: Any) -> Any:
        return "hi"

    remove = ui_registry.provide("shell.notice", draw, owner="announcer")
    try:
        item = await _first_user_stream_frame(monkeypatch)
    finally:
        remove()
    assert item is not None and item["event"] == "ui.invalidate"
    frame = json.loads(item["data"])
    assert frame["payload"]["site"] == "*" and frame["seq"] == 0


# -- HTTP routes and the plugin API -------------------------------------------------------------


def test_routes_render_and_act_through_a_plugin() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from valuz_agent.api.deps import get_current_user_id
    from valuz_agent.api.routes.plugin_ui import router
    from valuz_agent.modules.plugin_ui import ui_registry
    from valuz_agent.plugin_host import BackendPluginBase, PluginHost
    from valuz_agent.infra.middleware_registry import MiddlewareRegistry
    from valuz_agent.infra.module_registry import ModuleRegistry

    clicks: list[Any] = []

    async def render(request: UiRequest, inner: Any) -> Any:
        return {
            "type": "Button",
            "props": {"label": f"{request.user_id}:{len(clicks)}", "action": "go"},
        }

    async def on_action(request: UiRequest, action: str, value: Any) -> None:
        clicks.append(value)

    class _Plugin(BackendPluginBase):
        id = "ui-test-plugin"

        def apply(self, ctx: Any, config: Any) -> None:
            ctx.ui.provide("conversation.composer.dock", render, on_action=on_action)

    class _Ext:
        always_on_mcp_specs: list[Any] = []

    host = PluginHost(
        [_Plugin()],
        extensions=_Ext(),
        module_registry=ModuleRegistry(),
        middleware_registry=MiddlewareRegistry(),
    )
    host.load_all()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "u-route"
    try:
        client = TestClient(app)
        assert {"site": "conversation.composer.dock", "kind": "list"} in client.get(
            "/v1/ui/sites"
        ).json()["sites"]
        body = {"site": "conversation.composer.dock", "instance": "s", "session_id": "s"}
        [item] = client.post("/v1/ui/render", json=body).json()["items"]
        assert item["owner"] == "ui-test-plugin"
        assert item["tree"]["props"]["label"] == "u-route:0"
        answer = client.post(
            "/v1/ui/action",
            json={
                **body,
                "owner": "ui-test-plugin",
                "action": "go",
                "value": 7,
                "generation": item["generation"],
            },
        ).json()
        assert answer["ok"] is True and clicks == [7]
        assert answer["items"][0]["tree"]["props"]["label"] == "u-route:1"
        stale = client.post(
            "/v1/ui/action",
            json={
                **body,
                "owner": "ui-test-plugin",
                "action": "go",
                "value": 8,
                "generation": item["generation"],
            },
        ).json()
        assert stale["ok"] is False and stale["stale"] is True and clicks == [7]
    finally:
        host.unload("ui-test-plugin")
    assert all(entry["site"] != "conversation.composer.dock" for entry in ui_registry.sites())


async def test_plugin_pushes_are_stamped_with_their_owner() -> None:
    from valuz_agent.infra.middleware_registry import MiddlewareRegistry
    from valuz_agent.infra.module_registry import ModuleRegistry
    from valuz_agent.modules.plugin_ui import ui_push_hub
    from valuz_agent.plugin_host import BackendPluginBase, PluginHost

    captured: dict[str, Any] = {}

    class _Plugin(BackendPluginBase):
        id = "pusher-plugin"

        def apply(self, ctx: Any, config: Any) -> None:
            captured["ui"] = ctx.ui

    class _Ext:
        always_on_mcp_specs: list[Any] = []

    host = PluginHost(
        [_Plugin()],
        extensions=_Ext(),
        module_registry=ModuleRegistry(),
        middleware_registry=MiddlewareRegistry(),
    )
    host.load_all()
    received: list[Any] = []

    async def listen() -> None:
        async for push in ui_push_hub.subscribe("u-push", None):
            received.append(push)
            return

    try:
        task = asyncio.create_task(listen())
        await asyncio.sleep(0)
        # A plugin cannot speak for another: ``owner`` is always its own id.
        assert captured["ui"].push("u-push", "toast", {"text": "hi", "owner": "someone-else"}) == 1
        await asyncio.wait_for(task, 2)
    finally:
        host.unload("pusher-plugin")
    assert received[0].wire_payload()["owner"] == "pusher-plugin"
    assert received[0].wire_payload()["text"] == "hi"
