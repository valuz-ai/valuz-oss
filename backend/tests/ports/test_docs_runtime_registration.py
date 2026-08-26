"""The docs retrieval runtime is bindable, and every entry point honours it.

``search_docs`` dispatching through ``DocsRuntimePort`` (valuz-oss#838) only
made the *call* runtime-agnostic — every construction site still built the
embedded runtime directly, so a deployment whose documents are indexed
elsewhere had nothing to bind. This adds the binding seam, mirroring
``set_file_address_resolver``.

The tests that matter here are the coverage ones: a runtime bound for the HTTP
surface but not for the agent's MCP tool (or for the background scan) would
make the tool and the UI disagree about what the library contains, and that
divergence is exactly the kind of bug that shows up as "the agent says the
document isn't there".
"""

from __future__ import annotations

import inspect

import pytest

from valuz_agent.ports.docs_runtime import (
    DocsHealthSnapshot,
    default_docs_runtime,
    get_docs_runtime,
    set_docs_runtime_factory,
)


class _Fake:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    async def search(self, query, doc_scope_ids, top_k=5, doc_paths=None):  # type: ignore[no-untyped-def]
        return []

    async def health(self) -> DocsHealthSnapshot:
        return DocsHealthSnapshot(provider_id=self.provider_id, status="healthy")

    @property
    def provider_id(self) -> str:
        return "test.fake"


@pytest.fixture(autouse=True)
def _restore_default():
    yield
    set_docs_runtime_factory(default_docs_runtime)


def test_default_is_the_embedded_baseline():
    from valuz_agent.integrations.docs_embedded import EmbeddedDocsRuntime

    assert isinstance(get_docs_runtime("u1"), EmbeddedDocsRuntime)


def test_default_scopes_previews_to_the_owner(monkeypatch, tmp_path):
    """Preview files live per owner wherever the data dir is templated, so the
    default is built per call rather than shared. (Single-tenant OSS resolves
    every owner to one root, which is why the template has to be set here.)"""
    from valuz_agent.infra import config as config_mod

    monkeypatch.setattr(config_mod.settings, "data_dir", str(tmp_path / "{user_id}"))

    a = get_docs_runtime("owner-a")
    b = get_docs_runtime("owner-b")

    assert a.preview_dir != b.preview_dir
    assert "owner-a" in str(a.preview_dir)


def test_a_bound_factory_replaces_the_default():
    set_docs_runtime_factory(_Fake)
    runtime = get_docs_runtime("u1")
    assert isinstance(runtime, _Fake)
    assert runtime.user_id == "u1"


@pytest.mark.parametrize(
    ("module", "func"),
    [
        ("valuz_agent.api.deps", "get_document_service"),
        ("valuz_agent.integrations.docs_mcp_server", "_build_doc_service"),
        ("valuz_agent.modules.docs.scheduler", None),
    ],
)
def test_every_construction_site_goes_through_the_seam(module, func):
    """A site left constructing the runtime directly silently ignores the
    binding — the failure this seam exists to prevent."""
    import importlib

    mod = importlib.import_module(module)
    source = inspect.getsource(getattr(mod, func) if func else mod)
    assert "get_docs_runtime" in source
    assert "EmbeddedDocsRuntime(" not in source
