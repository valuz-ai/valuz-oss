"""``LLMModel.input_modalities`` — parsing, stamping, and row lookup.

The capability declaration (docs/design/model-capability in the commercial
repo) rides stored dict model entries
(``{"id": ..., "input_modalities": [...]}``) and ADR-011 contributed
channels, exactly like ``max_input_tokens``. Three-state: ``None`` = not
declared (consumers keep today's behavior); a declared list missing
``"image"`` = explicit negative capability runtimes gate image reads on.
These tests pin that the declaration parses, degrades safely on malformed
input, and survives the runtimes-stamping rebuilds.
"""

from __future__ import annotations

from valuz_agent.modules.providers.models import ProviderRow
from valuz_agent.modules.providers.schemas import LLMChannel, LLMModel
from valuz_agent.modules.providers.service import (
    _row_to_list_item,
    _stamp_contributed_runtimes,
    declared_model_input_modalities,
)


def _row(**kw: object) -> ProviderRow:
    base: dict[str, object] = dict(
        id="p1",
        name="Custom",
        provider_kind="compatible",
        source="user",
        auth_type="api_key",
        enabled=True,
        is_default=False,
        deletable=True,
        default_model=None,
        test_status="success",
        credential_source="secret_ref",
        protocol=None,
        model_ids=None,
        base_url="https://gateway.example.com/v1",
    )
    base.update(kw)
    return ProviderRow(**base)  # type: ignore[arg-type]


def test_dict_entry_declaration_parses_and_survives_stamping() -> None:
    row = _row(model_ids='[{"id": "valuz-pro-anthropic", "input_modalities": ["text"]}]')
    models = _row_to_list_item(row).models
    assert models[0].input_modalities == ("text",)
    # Stamping filled runtimes without dropping the declaration.
    assert models[0].runtimes


def test_bare_id_entry_has_no_declaration() -> None:
    row = _row(model_ids='["valuz-pro-anthropic"]')
    assert _row_to_list_item(row).models[0].input_modalities is None


def test_malformed_declaration_degrades_to_none() -> None:
    row = _row(
        model_ids='[{"id": "a", "input_modalities": "text"},'
        ' {"id": "b", "input_modalities": []},'
        ' {"id": "c", "input_modalities": [1, ""]}]'
    )
    assert all(m.input_modalities is None for m in _row_to_list_item(row).models)


def test_contributed_channel_stamping_preserves_declaration() -> None:
    ch = LLMChannel(
        id="valuz-channel",
        name="Valuz Cloud",
        provider_kind="system",
        source="system",
        deletable=False,
        is_default=False,
        credential_source="system_managed",
        compatible_protocols=["anthropic"],
        models=[LLMModel(id="valuz-lite-anthropic", input_modalities=("text", "image"))],
    )
    stamped = _stamp_contributed_runtimes(ch)
    assert stamped.models[0].input_modalities == ("text", "image")
    assert stamped.models[0].runtimes  # the rebuild actually ran


def test_declared_model_input_modalities_row_lookup() -> None:
    row = _row(
        model_ids='[{"id": "alias-a", "input_modalities": ["text"]}, "alias-b"]',
    )
    assert declared_model_input_modalities(row, "alias-a") == ("text",)
    assert declared_model_input_modalities(row, "alias-b") is None
    assert declared_model_input_modalities(row, "missing") is None
