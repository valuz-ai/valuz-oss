"""PTC codegen: wrapper emission, eligibility, docs, and the golden probe."""

from __future__ import annotations

from pathlib import Path

from tests.ptc.emission_probe import PROBE_SERVER, PROBE_TOOLS
from valuz_agent.modules.ptc.tool_generator import (
    ToolFunctionGenerator,
    ToolInfo,
    codegen_version,
    is_code_callable,
    sanitize_name,
)

GOLDEN_PATH = Path(__file__).parent / "emission_probe_golden.txt"


def _probe_tools() -> list[ToolInfo]:
    return [ToolInfo.from_dict(raw, PROBE_SERVER) for raw in PROBE_TOOLS]


def _emit_probe() -> str:
    """Deterministic emission over the probe toolset (module + docs + skill)."""
    generator = ToolFunctionGenerator()
    eligible = [t for t in _probe_tools() if is_code_callable(t)]
    sections = [
        "== module ==",
        generator.generate_tool_module(PROBE_SERVER, eligible),
        "== docs ==",
    ]
    sections += [generator.generate_tool_documentation(t) for t in eligible]
    sections += [
        "== skill ==",
        generator.generate_skill_markdown({PROBE_SERVER: eligible}),
        "== client-config ==",
        generator.generate_mcp_client_code([PROBE_SERVER]).splitlines()[-1],
    ]
    return "\n".join(sections)


# -- eligibility ------------------------------------------------------------


def test_eligibility_is_read_only_hint_fail_closed():
    by_name = {t.name: t for t in _probe_tools()}
    assert is_code_callable(by_name["get_series"]) is True
    assert is_code_callable(by_name["manage_things"]) is False  # readOnlyHint False
    assert is_code_callable(by_name["no_hint_tool"]) is False  # no annotations


# -- wrapper emission -------------------------------------------------------


def test_generated_module_compiles_and_binds_wire_names():
    generator = ToolFunctionGenerator()
    tools = [t for t in _probe_tools() if is_code_callable(t)]
    module = generator.generate_tool_module(PROBE_SERVER, tools)
    compile(module, "<generated>", "exec")  # must be valid Python
    # Renamed keyword param: signature uses ``from_``, wire dict keeps "from".
    assert "def get_series(from_: str" in module
    assert '"from": from_,' in module
    # Enum → Literal with the schema's real values; default carried over.
    assert "kind: Literal['daily', 'weekly', 'monthly'] = 'daily'" in module
    # Array with item type; nullable anyOf integer.
    assert "symbols: list[str]" in module
    assert "limit: int | None = None" in module
    # The wire server name (with dash) is what the client is called with.
    assert '_call_mcp_tool("probe-data", "get_series", arguments)' in module
    # Optional params with None meaning "not provided" are dropped.
    assert "arguments.update({k: v for k, v in optional.items() if v is not None})" in module


def test_required_param_without_identifier_drops_the_tool():
    generator = ToolFunctionGenerator()
    broken = next(t for t in _probe_tools() if t.name == "broken_required")
    module = generator.generate_tool_module(PROBE_SERVER, [broken])
    assert "def broken_required" not in module


def test_output_schema_wins_over_docstring_returns():
    generator = ToolFunctionGenerator()
    by_name = {t.name: t for t in _probe_tools()}
    module = generator.generate_tool_module(
        PROBE_SERVER, [by_name["get_series"], by_name["list_items"]]
    )
    # get_series: no outputSchema → docstring "Returns:\n dict:" extraction.
    assert "def get_series" in module and "-> dict:" in module
    # list_items: outputSchema array-of-object → list[dict].
    assert "def list_items() -> list[dict]:" in module


def test_hostile_docstring_quotes_are_escaped():
    generator = ToolFunctionGenerator()
    tool = next(t for t in _probe_tools() if t.name == "get_series")
    module = generator.generate_tool_module(PROBE_SERVER, [tool])
    compile(module, "<generated>", "exec")
    assert '\\"\\"\\"' in module  # the hostile triple quote survived, escaped


def test_epilogue_is_injection_safe_for_hostile_server_names():
    generator = ToolFunctionGenerator()
    hostile = 'evil"] )\n import os # '
    code = generator.generate_mcp_client_code([hostile])
    compile(code, "<generated-client>", "exec")


# -- docs + skill -----------------------------------------------------------


def test_docs_show_sanitized_signature_names():
    generator = ToolFunctionGenerator()
    tool = next(t for t in _probe_tools() if t.name == "get_series")
    doc = generator.generate_tool_documentation(tool)
    assert "get_series(from_: str" in doc
    assert "[allowed: 'daily', 'weekly', 'monthly']" in doc
    assert "from tools.probe_data import get_series" in doc


def test_skill_markdown_is_sorted_and_stable():
    generator = ToolFunctionGenerator()
    eligible = [t for t in _probe_tools() if is_code_callable(t)]
    text_a = generator.generate_skill_markdown({PROBE_SERVER: eligible})
    text_b = generator.generate_skill_markdown({PROBE_SERVER: list(reversed(eligible))})
    assert text_a == text_b  # ordering is canonical, not input order
    assert "from tools.probe_data import" in text_a
    assert "ToolCallError" in text_a


# -- golden -----------------------------------------------------------------


def test_emission_probe_matches_golden():
    """The emitted bytes are pinned. An intentional emission change must
    regenerate the golden (see this file's ``__main__``) AND move
    ``_GENERATOR_SALT`` so warm workspaces resync."""
    assert GOLDEN_PATH.exists(), (
        "golden missing — generate with: uv run python -m tests.ptc.test_tool_generator"
    )
    assert _emit_probe() == GOLDEN_PATH.read_text(encoding="utf-8")


def test_codegen_version_moves_with_runtime_source(monkeypatch):
    import valuz_agent.modules.ptc.tool_generator as tg

    real_source = tg.client_runtime_source  # keep the lru-cached original
    before = codegen_version()
    monkeypatch.setattr(tg, "client_runtime_source", lambda: "changed source")
    assert tg.codegen_version() != before
    real_source.cache_clear()


def test_sanitize_name_edges():
    assert sanitize_name("from") == "from_"
    assert sanitize_name("2fast") == "_2fast"
    assert sanitize_name("a-b.c") == "a_b_c"
    assert sanitize_name("???") == "___"


if __name__ == "__main__":  # golden (re)generation entry point
    GOLDEN_PATH.write_text(_emit_probe(), encoding="utf-8")
    print(f"wrote {GOLDEN_PATH}")
