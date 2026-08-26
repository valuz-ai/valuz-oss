"""``LLMModel.max_input_tokens`` — parsing, stamping, and row lookup.

The declared input window rides stored dict model entries
(``{"id": ..., "max_input_tokens": ...}``) and ADR-011 contributed channels.
Both list/detail assembly paths rebuild ``LLMModel`` rows field-by-field
(frozen dataclass), so these tests pin that the declaration survives the
runtimes-stamping rebuilds — dropping it there would silently disable
auto-compaction sizing for every session on the channel.
"""

from __future__ import annotations

from valuz_agent.modules.providers.models import ProviderRow
from valuz_agent.modules.providers.schemas import LLMChannel, LLMModel
from valuz_agent.modules.providers.service import (
    _row_to_list_item,
    _stamp_contributed_runtimes,
    declared_model_max_input_tokens,
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
    row = _row(model_ids='[{"id": "valuz-pro-anthropic", "max_input_tokens": 200000}]')
    models = _row_to_list_item(row).models
    assert models[0].max_input_tokens == 200_000
    # Stamping filled runtimes without dropping the declaration.
    assert models[0].runtimes


def test_bare_id_entry_has_no_declaration() -> None:
    row = _row(model_ids='["valuz-pro-anthropic"]')
    assert _row_to_list_item(row).models[0].max_input_tokens is None


def test_malformed_declaration_degrades_to_none() -> None:
    row = _row(
        model_ids='[{"id": "a", "max_input_tokens": "200000"},'
        ' {"id": "b", "max_input_tokens": -5},'
        ' {"id": "c", "max_input_tokens": true}]'
    )
    assert all(m.max_input_tokens is None for m in _row_to_list_item(row).models)


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
        models=[LLMModel(id="valuz-lite-anthropic", max_input_tokens=200_000)],
    )
    stamped = _stamp_contributed_runtimes(ch)
    assert stamped.models[0].max_input_tokens == 200_000
    assert stamped.models[0].runtimes  # the rebuild actually ran


def test_declared_model_max_input_tokens_row_lookup() -> None:
    row = _row(
        model_ids='[{"id": "alias-a", "max_input_tokens": 131072}, "alias-b"]',
    )
    assert declared_model_max_input_tokens(row, "alias-a") == 131_072
    assert declared_model_max_input_tokens(row, "alias-b") is None
    assert declared_model_max_input_tokens(row, "missing") is None
