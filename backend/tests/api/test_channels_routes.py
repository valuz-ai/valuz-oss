from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from valuz_agent.api.deps import get_channel_ingress_service
from valuz_agent.api.routes import channels as channels_routes


class _Uow:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeAgentChannelBindingDatastore:
    binding: Any = None

    def __init__(self, _db: object) -> None:
        pass

    async def get(
        self,
        *,
        user_id: str,
        platform: str,
        agent_slug: str,
    ) -> Any:
        if (
            self.binding is not None
            and self.binding.owner_user_id == user_id
            and self.binding.platform == platform
            and self.binding.agent_slug == agent_slug
        ):
            return self.binding
        return None

    async def list_enabled(self, *, platform: str, user_id: str | None = None) -> list:
        binding = self.binding
        if binding is None or binding.platform != platform or not binding.enabled:
            return []
        if user_id is not None and binding.owner_user_id != user_id:
            return []
        return [binding]

    async def get_enabled_by_channel_instance(
        self,
        *,
        platform: str,
        channel_instance_id: str,
    ) -> Any:
        if (
            self.binding is None
            or self.binding.platform != platform
            or self.binding.channel_instance_id != channel_instance_id
            or not self.binding.enabled
        ):
            return None
        return self.binding

    async def upsert(
        self,
        *,
        user_id: str,
        platform: str,
        agent_slug: str,
        channel_instance_id: str,
        bot_id: str,
        secret_ref: str | None,
        enabled: bool,
        bot_name: str | None = None,
        ws_url: str | None = None,
    ) -> Any:
        self.__class__.binding = SimpleNamespace(
            id="binding-1",
            owner_user_id=user_id,
            platform=platform,
            channel_instance_id=channel_instance_id,
            agent_slug=agent_slug,
            bot_id=bot_id,
            secret_ref=secret_ref,
            enabled=enabled,
            bot_name=bot_name,
            ws_url=ws_url,
        )
        return self.__class__.binding


class _FakeSupervisor:
    def status_for(self, _agent_slug: str) -> SimpleNamespace:
        return SimpleNamespace(status="stopped", connected=False, last_error=None)

    async def restart(self) -> None:
        return None


def test_feishu_url_verification_uses_bound_agent_secret(
    monkeypatch,
) -> None:
    _FakeAgentChannelBindingDatastore.binding = SimpleNamespace(
        id="binding-1",
        owner_user_id="u1",
        platform="feishu",
        channel_instance_id="feishu-main",
        agent_slug="developer",
        bot_id="cli_app_1",
        secret_ref="channel/feishu/developer",
        enabled=True,
        bot_name=None,
        ws_url=None,
    )
    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(
        channels_routes.secret_store,
        "get",
        lambda user_id, ref: (
            json.dumps({"verification_token": "verify-token", "encrypt_key": ""})
            if user_id == "u1" and ref == "channel/feishu/developer"
            else None
        ),
    )
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[get_channel_ingress_service] = lambda: object()

    response = TestClient(app).post(
        "/v1/channels/feishu/feishu-main/callback",
        json={
            "type": "url_verification",
            "token": "verify-token",
            "challenge": "challenge-code",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-code"}


def test_update_feishu_binding_stores_token_payload(
    monkeypatch,
) -> None:
    _FakeAgentChannelBindingDatastore.binding = None
    saved: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(
        channels_routes.secret_store,
        "get",
        lambda user_id, ref: saved.get((user_id, ref)),
    )
    monkeypatch.setattr(
        channels_routes.secret_store,
        "put",
        lambda user_id, ref, value: saved.__setitem__((user_id, ref), value),
    )
    monkeypatch.setattr(channels_routes, "feishu_supervisor", _FakeSupervisor())
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).put(
        "/v1/channels/feishu/bindings/developer",
        json={
            "enabled": True,
            "agent_slug": "developer",
            "app_id": "cli_app_1",
            "app_secret": "app-secret",
            "verification_token": "verify-token",
            "encrypt_key": "encrypt-key",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "channel_instance_id": "feishu-main",
        "owner_user_id": "u1",
        "agent_slug": "developer",
        "app_id": "cli_app_1",
        "has_app_secret": True,
        "has_verification_token": True,
        "has_encrypt_key": True,
        "connected": False,
        "connection_status": "stopped",
        "connection_error": None,
    }
    assert json.loads(saved[("u1", "channel/feishu/developer")]) == {
        "app_secret": "app-secret",
        "verification_token": "verify-token",
        "encrypt_key": "encrypt-key",
    }


def test_update_feishu_binding_requires_app_secret_for_new_binding(
    monkeypatch,
) -> None:
    _FakeAgentChannelBindingDatastore.binding = None
    saved: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(
        channels_routes.secret_store,
        "get",
        lambda user_id, ref: saved.get((user_id, ref)),
    )
    monkeypatch.setattr(channels_routes, "feishu_supervisor", _FakeSupervisor())
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).put(
        "/v1/channels/feishu/bindings/developer",
        json={
            "enabled": True,
            "agent_slug": "developer",
            "app_id": "cli_app_1",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "App Secret is required"


def test_test_feishu_binding_probes_credentials_and_reports_status(
    monkeypatch,
) -> None:
    _FakeAgentChannelBindingDatastore.binding = SimpleNamespace(
        id="binding-1",
        owner_user_id="u1",
        platform="feishu",
        channel_instance_id="feishu-main",
        agent_slug="developer",
        bot_id="cli_app_1",
        secret_ref="channel/feishu/developer",
        enabled=True,
        bot_name=None,
        ws_url=None,
    )
    saved = {
        ("u1", "channel/feishu/developer"): json.dumps({"app_secret": "app-secret"})
    }
    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(
        channels_routes.secret_store,
        "get",
        lambda user_id, ref: saved.get((user_id, ref)),
    )
    monkeypatch.setattr(channels_routes, "feishu_supervisor", _FakeSupervisor())
    probes: list[tuple[str, str]] = []

    async def fake_check(app_id: str, app_secret: str) -> tuple[bool, str | None]:
        probes.append((app_id, app_secret))
        return True, None

    monkeypatch.setattr(channels_routes, "_check_feishu_credentials", fake_check)
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).post("/v1/channels/feishu/bindings/developer/test")

    assert response.status_code == 200
    assert response.json() == {
        "credential_ok": True,
        "error": None,
        "connected": False,
        "connection_status": "stopped",
        "connection_error": None,
    }
    assert probes == [("cli_app_1", "app-secret")]


def test_test_feishu_binding_404_without_binding(monkeypatch) -> None:
    _FakeAgentChannelBindingDatastore.binding = None
    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).post("/v1/channels/feishu/bindings/developer/test")

    assert response.status_code == 404


def test_create_feishu_chat_creates_binds_and_returns_the_join_link(monkeypatch) -> None:
    """Creating the group here sidesteps adding a bot to an existing group —
    a client menu that is absent or disabled in plenty of setups. The bot is
    the creator, so the share link is how the person joins."""
    _FakeAgentChannelBindingDatastore.binding = SimpleNamespace(
        id="binding-1",
        owner_user_id="u1",
        platform="feishu",
        channel_instance_id="feishu-main",
        agent_slug="developer",
        bot_id="cli_app_1",
        secret_ref="channel/feishu/developer",
        enabled=True,
        bot_name=None,
        ws_url=None,
    )
    saved = {
        ("u1", "channel/feishu/developer"): json.dumps({"app_secret": "app-secret"})
    }
    bound: list[dict] = []

    class _FakeChatBindings:
        def __init__(self, _db: object) -> None:
            pass

        async def upsert(self, **kwargs):
            bound.append(kwargs)
            return None

    async def fake_create(*, app_id: str, app_secret: str, name: str):
        assert (app_id, app_secret) == ("cli_app_1", "app-secret")
        return "oc-new", f"https://applink.feishu.cn/client/chat/chatter/add_by_link?{name}"

    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(channels_routes, "ChannelChatBindingDatastore", _FakeChatBindings)
    monkeypatch.setattr(
        channels_routes.secret_store, "get", lambda user_id, ref: saved.get((user_id, ref))
    )
    import valuz_agent.integrations.feishu_long_connection as feishu_mod

    monkeypatch.setattr(feishu_mod, "create_feishu_chat", fake_create)

    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).post(
        "/v1/channels/feishu/chats",
        json={"name": "研究群", "project_id": "proj-a"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["external_chat_id"] == "oc-new"
    assert body["share_link"].endswith("研究群")
    assert bound[0]["project_id"] == "proj-a"
    assert bound[0]["external_chat_name"] == "研究群"


def test_list_chat_bindings_reads_the_database_and_sorts_by_name(monkeypatch) -> None:
    """The panel loads on every project open, so it answers from stored rows
    alone — hanging it on live Feishu calls made it slow enough for the client
    to give up, which renders as an empty panel. Order is by name, the same
    order the picker shows."""
    rows = [
        SimpleNamespace(
            channel_instance_id="feishu-main",
            external_chat_id="oc-2",
            project_id="proj-a",
            external_chat_name="研究群",
            default_agent_slug=None,
            created_by_valuz=True,
        ),
        SimpleNamespace(
            channel_instance_id="wecom-aibot-main",
            external_chat_id="oc-1",
            project_id="proj-a",
            external_chat_name="Alpha",
            default_agent_slug=None,
            created_by_valuz=False,
        ),
    ]

    class _FakeChatBindings:
        def __init__(self, _db: object) -> None:
            pass

        async def list_all(self, *, user_id: str) -> list:
            return rows

    async def exploding_list(**_kwargs):  # pragma: no cover
        raise AssertionError("the panel must not call Feishu")

    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(channels_routes, "ChannelChatBindingDatastore", _FakeChatBindings)
    import valuz_agent.integrations.feishu_long_connection as feishu_mod

    monkeypatch.setattr(feishu_mod, "list_feishu_chats", exploding_list)

    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    body = TestClient(app).get("/v1/channels/chat-bindings").json()

    assert [row["external_chat_name"] for row in body] == ["Alpha", "研究群"]
    assert [row["platform"] for row in body] == ["wecom_aibot", "feishu"]
    assert [row["created_by_valuz"] for row in body] == [False, True]


def test_delete_feishu_chat_refuses_a_group_valuz_did_not_create(monkeypatch) -> None:
    """Unbinding is the most this side may do to a group it does not own — the
    bot is not its owner, and dissolving somebody's group by mistake is not
    recoverable."""
    _FakeAgentChannelBindingDatastore.binding = SimpleNamespace(
        id="binding-1",
        owner_user_id="u1",
        platform="feishu",
        channel_instance_id="feishu-main",
        agent_slug="developer",
        bot_id="cli_app_1",
        secret_ref="channel/feishu/developer",
        enabled=True,
        bot_name=None,
        ws_url=None,
    )
    deleted: list[str] = []

    class _FakeChatBindings:
        def __init__(self, _db: object) -> None:
            pass

        async def get(self, **_keys):
            return SimpleNamespace(
                channel_instance_id="feishu-main",
                external_chat_id="oc-1",
                project_id="proj-a",
                external_chat_name="别人的群",
                default_agent_slug=None,
                created_by_valuz=False,
            )

        async def delete(self, **_keys) -> bool:  # pragma: no cover
            raise AssertionError("must not unbind on a refused delete")

    async def fake_delete(**kwargs):  # pragma: no cover
        deleted.append(kwargs["chat_id"])

    async def fake_list(*, app_id: str, app_secret: str):
        from valuz_agent.integrations.feishu_long_connection import FeishuChat

        # Owned by a person — the guard must refuse.
        return [FeishuChat(chat_id="oc-1", name="别人的群", bot_owned=False)]

    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(channels_routes, "ChannelChatBindingDatastore", _FakeChatBindings)
    import valuz_agent.integrations.feishu_long_connection as feishu_mod

    monkeypatch.setattr(feishu_mod, "delete_feishu_chat", fake_delete)
    monkeypatch.setattr(feishu_mod, "list_feishu_chats", fake_list)
    monkeypatch.setattr(
        channels_routes.secret_store,
        "get",
        lambda user_id, ref: json.dumps({"app_secret": "app-secret"}),
    )

    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).delete("/v1/channels/feishu/chats/oc-1")

    assert response.status_code == 409
    assert deleted == []


def test_delete_feishu_chat_works_without_a_binding(monkeypatch) -> None:
    """A Valuz-created group can be unbound — or never bound, if the binding
    write failed after the group was made. Those orphans are exactly what needs
    cleaning up, so deleting must not require a binding row."""
    _FakeAgentChannelBindingDatastore.binding = SimpleNamespace(
        id="binding-1",
        owner_user_id="u1",
        platform="feishu",
        channel_instance_id="feishu-main",
        agent_slug="developer",
        bot_id="cli_app_1",
        secret_ref="channel/feishu/developer",
        enabled=True,
        bot_name=None,
        ws_url=None,
    )
    deleted: list[str] = []

    class _NoChatBindings:
        def __init__(self, _db: object) -> None:
            pass

        async def get(self, **_keys):
            return None

        async def delete(self, **_keys) -> bool:  # pragma: no cover
            raise AssertionError("nothing to unbind")

    async def fake_delete(**kwargs):
        deleted.append(kwargs["chat_id"])

    async def fake_list(*, app_id: str, app_secret: str):
        from valuz_agent.integrations.feishu_long_connection import FeishuChat

        return [FeishuChat(chat_id="oc-orphan", name="孤儿群", bot_owned=True)]

    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(channels_routes, "ChannelChatBindingDatastore", _NoChatBindings)
    monkeypatch.setattr(
        channels_routes.secret_store,
        "get",
        lambda user_id, ref: json.dumps({"app_secret": "app-secret"}),
    )
    import valuz_agent.integrations.feishu_long_connection as feishu_mod

    monkeypatch.setattr(feishu_mod, "delete_feishu_chat", fake_delete)
    monkeypatch.setattr(feishu_mod, "list_feishu_chats", fake_list)

    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).delete("/v1/channels/feishu/chats/oc-orphan")

    assert response.status_code == 204
    assert deleted == ["oc-orphan"]


def test_channel_reads_are_not_cacheable(monkeypatch) -> None:
    """These reads follow a write the caller just made, so a cached copy shows
    the state before the change — indistinguishable from a view that never
    refreshed. One session was observed serving two requests while the panel
    had been loaded many times."""

    class _FakeChatBindings:
        def __init__(self, _db: object) -> None:
            pass

        async def list_all(self, *, user_id: str) -> list:
            return []

    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(channels_routes, "ChannelChatBindingDatastore", _FakeChatBindings)

    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).get("/v1/channels/chat-bindings")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
