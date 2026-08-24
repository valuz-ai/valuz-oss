"""PTC tool-function generator — MCP tool schemas to workspace wrapper modules.

Port of the LangAlpha/ptc-demo generator, adapted to Valuz:

- **Eligibility is annotation-driven and fail-closed** (``is_code_callable``):
  only tools whose MCP ``annotations.readOnlyHint`` is literally ``True``
  enter the code face. A missing/false hint keeps the tool native-only, so
  a mutating tool can never ride the "one approval covers the program"
  contract (verified against the live Valuz Data catalog, where
  ``manage_watches`` honestly reports ``readOnlyHint: False``).
- **Return types prefer ``outputSchema``** over docstring ``Returns:``
  extraction (kept as fallback; the live catalog ships no outputSchema yet).
- The composed client is ``client_runtime.py`` (stdlib-only, loopback POST)
  plus a JSON config epilogue. The double ``json.dumps`` keeps the embedded
  config injection-safe: the inner call serializes the config, the outer one
  turns that JSON text into a fully-escaped Python string literal.

The generator emits data, never client logic; wrapper docs and the wrapper
signature are rendered from the same ``_bind_params`` result so they cannot
disagree (a doc showing a wire name for a renamed param sends the agent
straight into a TypeError).
"""

from __future__ import annotations

import hashlib
import json
import keyword
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

_RUNTIME_FILE = Path(__file__).parent / "client_runtime.py"

# Enums render as Literal[...] only while they stay readable as a signature.
_MAX_LITERAL_VALUES = 12
_MAX_LITERAL_CHARS = 400
_MAX_DOC_ENUM_VALUES = 12

_JSON_TO_PY = {
    "string": "str",
    "number": "float",
    "integer": "int",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
    "null": "None",
}

# Bump when wrapper/doc emission changes shape; folded into codegen_version()
# so materialized workspaces regenerate exactly when their bytes would change
# — pinned by the tests' emission golden.
_GENERATOR_SALT = "valuz-ptc-emission-1"


@lru_cache(maxsize=1)
def client_runtime_source() -> str:
    """The static client runtime, composed verbatim (plus epilogue)."""
    return _RUNTIME_FILE.read_text(encoding="utf-8")


def codegen_version() -> str:
    """Derived version of the composed client + emission logic.

    Keys workspace manifests: regeneration happens exactly when the runtime
    source or the generator's output shape moves, never on every start.
    """
    return hashlib.sha256((client_runtime_source() + _GENERATOR_SALT).encode("utf-8")).hexdigest()[
        :12
    ]


def sanitize_name(name: str) -> str | None:
    """Map a schema name to a legal, non-keyword Python identifier (or None)."""
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if not cleaned:
        return None
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    if keyword.iskeyword(cleaned):
        cleaned += "_"
    return cleaned if cleaned.isidentifier() else None


@dataclass(frozen=True)
class ToolInfo:
    """One MCP tool's schema, as returned by tools/list."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] = field(default_factory=dict, hash=False)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], server_name: str) -> ToolInfo:
        annotations = raw.get("annotations")
        output_schema = raw.get("outputSchema")
        return cls(
            name=raw.get("name", ""),
            description=raw.get("description", "") or "",
            input_schema=raw.get("inputSchema", {}) or {},
            server_name=server_name,
            output_schema=output_schema if isinstance(output_schema, dict) else None,
            annotations=annotations if isinstance(annotations, dict) else {},
        )

    def get_parameters(self) -> dict[str, dict[str, Any]]:
        """Resolve each schema property into the facts codegen consumes."""
        properties = self.input_schema.get("properties", {}) or {}
        required = set(self.input_schema.get("required", []) or [])
        params: dict[str, dict[str, Any]] = {}
        for wire, schema in properties.items():
            if not isinstance(schema, dict):
                schema = {}
            info: dict[str, Any] = {
                "type": schema.get("type", "any"),
                "required": wire in required,
                "description": schema.get("description", "") or "",
                "enum": schema.get("enum"),
                "has_default": "default" in schema,
                "default": schema.get("default"),
                "nullable": False,
                "items_type": None,
            }
            # anyOf [X, null] → nullable X (the common optional-field spelling)
            any_of = schema.get("anyOf")
            if isinstance(any_of, list) and any_of:
                arms = [a for a in any_of if isinstance(a, dict)]
                types = [a.get("type") for a in arms]
                if "null" in types:
                    info["nullable"] = True
                non_null = [a for a in arms if a.get("type") != "null"]
                if non_null:
                    info["type"] = non_null[0].get("type", "any")
                    info["enum"] = info["enum"] or non_null[0].get("enum")
            if info["type"] == "array":
                items = schema.get("items")
                if isinstance(items, dict):
                    info["items_type"] = items.get("type")
            params[wire] = info
        return params


def is_code_callable(tool: ToolInfo) -> bool:
    """Fail-closed code-face eligibility: ``annotations.readOnlyHint is True``.

    The code face runs N tool calls under ONE ``execute_code`` approval, so
    only tools that declare themselves read-only may enter it. No annotation
    → native-only.
    """
    return tool.annotations.get("readOnlyHint") is True


@dataclass(frozen=True)
class _ParamBinding:
    """One parameter's two names plus its resolved schema facts.

    ``wire`` is the schema key — the only name the server accepts. ``py`` is
    the wrapper-signature identifier the agent types. They differ exactly
    when sanitization renamed a keyword/illegal name (``from`` → ``from_``);
    the arguments dict always emits ``wire``.
    """

    wire: str
    py: str
    info: dict[str, Any] = field(hash=False)


class ToolFunctionGenerator:
    """Generates Python wrapper modules + markdown docs from MCP schemas."""

    # ------------------------------------------------------------------ module

    def generate_tool_module(self, server_name: str, tools: list[ToolInfo]) -> str:
        code = f'''"""
Auto-generated tool functions for MCP server: {server_name}

Each function forwards one tool call on the {server_name} server through
the PTC loopback client in ``tools/mcp_client.py``. Generated from
tools/list schemas — do not edit by hand.
"""

from typing import Any, Literal  # noqa: F401 - Literal used only by enum params

from .mcp_client import ToolCallError, _call_mcp_tool  # noqa: F401


'''
        for tool in tools:
            function_code = self._generate_function(tool, server_name)
            if function_code:
                code += function_code + "\n\n"
        return code

    def _generate_function(self, tool: ToolInfo, server_name: str) -> str:
        func_name = sanitize_name(tool.name)
        if func_name is None:
            return ""
        bindings = self._bind_params(tool)
        if bindings is None:
            # A required param has no legal Python name — don't ship a
            # wrapper that can never satisfy the server.
            return ""
        param_str = self._render_signature_params(bindings)
        docstring = self._generate_docstring(tool, bindings)
        return_type = self._return_type(tool)

        required = [b for b in bindings if b.info["required"]]
        optional = [b for b in bindings if not b.info["required"]]

        def _entries(bs: list[_ParamBinding]) -> str:
            return "\n".join(f'        "{b.wire}": {b.py},' for b in bs)

        # Required params are always sent (their None is an explicit JSON
        # null); an optional param's None means "not provided" — drop the key.
        if required:
            body = "    arguments = {\n" + _entries(required) + "\n    }\n"
        else:
            body = "    arguments = {}\n"
        if optional:
            body += (
                "    optional = {\n" + _entries(optional) + "\n    }\n"
                "    arguments.update({k: v for k, v in optional.items() if v is not None})\n"
            )

        return (
            f"def {func_name}({param_str}) -> {return_type}:\n"
            f'    """{docstring}"""\n'
            f"{body}"
            f'    return _call_mcp_tool("{server_name}", "{tool.name}", arguments)'
        )

    # ------------------------------------------------------------- parameters

    def _bind_params(self, tool: ToolInfo) -> list[_ParamBinding] | None:
        """Resolve each schema param to a (wire, py) name pair, schema order.

        Two params may sanitize to one identifier; the loser is renamed, not
        dropped — dropping a REQUIRED param would ship a wrapper that can
        never satisfy the server. Returns None when a required param has no
        salvageable identifier at all.
        """
        bindings: list[_ParamBinding] = []
        seen: set[str] = set()
        for wire, info in tool.get_parameters().items():
            py = sanitize_name(wire)
            if py is None:
                if info["required"]:
                    return None
                continue
            while py in seen:
                py += "_"
            seen.add(py)
            bindings.append(_ParamBinding(wire, py, info))
        return bindings

    def _render_signature_params(self, bindings: list[_ParamBinding]) -> str:
        rendered = []
        ordered = [b for b in bindings if b.info["required"]] + [
            b for b in bindings if not b.info["required"]
        ]
        for b in ordered:
            annotation = self._annotation(b.info)
            if b.info["required"]:
                rendered.append(f"{b.py}: {annotation}")
            elif b.info.get("has_default") and b.info.get("default") is not None:
                rendered.append(f"{b.py}: {annotation} = {b.info['default']!r}")
            else:
                if not annotation.endswith("| None"):
                    annotation = f"{annotation} | None"
                rendered.append(f"{b.py}: {annotation} = None")
        return ", ".join(rendered)

    def _annotation(self, info: dict[str, Any]) -> str:
        annotation = self._base_annotation(info)
        if info.get("nullable") and annotation != "Any" and not annotation.endswith("| None"):
            annotation = f"{annotation} | None"
        return annotation

    def _base_annotation(self, info: dict[str, Any]) -> str:
        enum = info.get("enum")
        if (
            enum
            and len(enum) <= _MAX_LITERAL_VALUES
            and all(isinstance(v, (str, int, bool)) for v in enum)
        ):
            literal = "Literal[{}]".format(", ".join(repr(v) for v in enum))
            if len(literal) <= _MAX_LITERAL_CHARS:
                return literal
        json_type = info.get("type")
        if json_type == "array":
            item = _JSON_TO_PY.get(info.get("items_type") or "")
            return f"list[{item}]" if item else "list"
        if not isinstance(json_type, str):
            return "Any"
        return _JSON_TO_PY.get(json_type, "Any")

    # ---------------------------------------------------------------- returns

    def _return_type(self, tool: ToolInfo) -> str:
        """``outputSchema`` first; docstring ``Returns:`` extraction second."""
        schema_type = self._output_schema_type(tool.output_schema)
        if schema_type is not None:
            return schema_type
        return self._extract_return_info(tool.description)[0]

    def _output_schema_type(self, schema: dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return None
        json_type = schema.get("type")
        if json_type == "object":
            return "dict"
        if json_type == "array":
            items = schema.get("items")
            if isinstance(items, dict) and items.get("type") == "object":
                return "list[dict]"
            return "list"
        if isinstance(json_type, str):
            return _JSON_TO_PY.get(json_type)
        return None

    def _extract_return_info(self, description: str) -> tuple[str, str]:
        """Return-type hint + description from a docstring ``Returns:`` section."""
        if not description:
            return ("Any", "Tool execution result")
        pattern = r"Returns?:\s*\n?\s*(.*?)(?:\n\s*(?:Args?:|Example|Note|Raises?:|$)|\Z)"
        match = re.search(pattern, description, re.IGNORECASE | re.DOTALL)
        if not match or not match.group(1).strip():
            return ("Any", "Tool execution result")
        text = match.group(1).strip()
        for type_pattern, hint in [
            (r"^(dict|Dict)\s*[:{]", "dict"),
            (r"^(list|List)\s*\[?\s*(dict|Dict)", "list[dict]"),
            (r"^(list|List)\b", "list"),
            (r"^(str|string)\b", "str"),
            (r"[Dd]ictionary\s+(?:with|containing)", "dict"),
            (r"[Ll]ist\s+of\s+(?:dict|record)", "list[dict]"),
        ]:
            if re.search(type_pattern, text, re.IGNORECASE):
                return (hint, text)
        return ("Any", text)

    # -------------------------------------------------------------- docstring

    def _generate_docstring(self, tool: ToolInfo, bindings: list[_ParamBinding]) -> str:
        def _escape(text: str) -> str:
            # Neutralize the triple-quote delimiter even for trusted text.
            return text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')

        lines: list[str] = []
        if tool.description:
            lines.append(_escape(tool.description))
            lines.append("")

        if bindings:
            lines.append("Args:")
            for b in bindings:
                desc = _escape(b.info.get("description", ""))
                required = " (required)" if b.info["required"] else ""
                suffix = _escape(self._facts_suffix(b.info))
                lines.append(
                    f"    {b.py} ({_escape(str(b.info['type']))}){required}: {desc}{suffix}"
                )
            lines.append("")

        return_type = self._return_type(tool)
        _, return_desc = self._extract_return_info(tool.description)
        lines.append("Returns:")
        first, *rest = _escape(return_desc).split("\n")
        lines.append(
            f"    {return_type}: {first.strip()}"
            if return_type != "Any"
            else f"    {first.strip()}"
        )
        lines.extend(f"    {r.strip()}" for r in rest if r.strip())

        example = self._render_example_call(tool, bindings, limit=2)
        if example:
            lines.append("")
            lines.append("Example:")
            lines.append(f"    result = {example}")

        return "\n    ".join(lines)

    def _facts_suffix(self, info: dict[str, Any]) -> str:
        """Allowed values + real defaults — what agents otherwise guess wrong."""
        parts = []
        enum = info.get("enum")
        if enum:
            shown = ", ".join(repr(v) for v in enum[:_MAX_DOC_ENUM_VALUES])
            if len(enum) > _MAX_DOC_ENUM_VALUES:
                shown += ", ..."
            parts.append(f"[allowed: {shown}]")
        if info.get("has_default") and info.get("default") is not None:
            parts.append(f"[default: {info['default']!r}]")
        return (" " + " ".join(parts)) if parts else ""

    def _render_example_call(
        self, tool: ToolInfo, bindings: list[_ParamBinding], limit: int | None = None
    ) -> str:
        """Schema-true example values (default, then first enum value) — a
        placeholder like mode="example" fails on every enum-typed param."""
        args = []
        for b in bindings:
            if not b.info["required"]:
                continue
            args.append(f"{b.py}={self._example_value(b.info)}")
        if not args:
            return ""
        return f"{sanitize_name(tool.name)}({', '.join(args[:limit])})"

    def _example_value(self, info: dict[str, Any]) -> str:
        if info.get("has_default") and info.get("default") is not None:
            return repr(info["default"])
        if info.get("enum"):
            return repr(info["enum"][0])
        placeholders = {
            "string": '"example"',
            "number": "42.0",
            "integer": "42",
            "boolean": "True",
            "array": "[]",
            "object": "{}",
        }
        json_type = info.get("type")
        return placeholders.get(json_type, '""') if isinstance(json_type, str) else '""'

    # ------------------------------------------------------------------- docs

    def generate_tool_documentation(self, tool: ToolInfo) -> str:
        """Markdown doc showing the exact callable signature (sanitized names
        included)."""
        func_name = sanitize_name(tool.name) or "_invalid_tool"
        module = sanitize_name(tool.server_name) or "server"
        bindings = self._bind_params(tool)
        if bindings is None:
            return (
                f"# {func_name} (unavailable)\n\nNot callable: a required "
                "parameter of its schema has no valid Python name.\n"
            )
        doc = f"# {func_name}({self._render_signature_params(bindings)})\n\n"
        if tool.description:
            doc += f"{tool.description}\n\n"
        doc += "## Parameters\n\n"
        if bindings:
            for b in bindings:
                marker = "**Required**" if b.info["required"] else "Optional"
                doc += f"- `{b.py}` ({b.info['type']}) - {marker}{self._facts_suffix(b.info)}\n"
                if b.info.get("description"):
                    doc += f"  {b.info['description']}\n"
                doc += "\n"
        else:
            doc += "No parameters\n\n"
        return_type = self._return_type(tool)
        _, return_desc = self._extract_return_info(tool.description)
        doc += f"## Returns\n\n**Type:** `{return_type}`\n\n{return_desc}\n\n"
        doc += "## Example\n\n```python\n"
        doc += f"from tools.{module} import {func_name}\n\n"
        example = self._render_example_call(tool, bindings)
        doc += f"result = {example or f'{func_name}()'}\nprint(result)\n```\n"
        return doc

    # ------------------------------------------------------------------ skill

    def generate_skill_markdown(self, tools_by_server: dict[str, list[ToolInfo]]) -> str:
        """SKILL.md — the progressive-disclosure entry the model reads.

        Byte-stable for a given input set: servers and tools render in
        dictionary order, one signature line each.
        """
        lines = [
            "---",
            "name: ptc-tools",
            "description: Call data tools from Python via execute_code — batch queries,",
            "  loops, and computation in one run with only printed summaries returning.",
            "---",
            "",
            "# PTC data tools",
            "",
            "Write Python via the `execute_code` tool. Import the generated wrappers",
            "and chain multiple tool calls + computation in ONE program:",
            "",
            "```python",
        ]
        first_server = next(iter(sorted(tools_by_server)), None)
        example_import = "from tools.<server> import <tool>"
        if first_server:
            module = sanitize_name(first_server) or "server"
            first_tools = sorted(t.name for t in tools_by_server[first_server])
            if first_tools:
                example_import = f"from tools.{module} import {sanitize_name(first_tools[0])}"
        lines += [
            example_import,
            "```",
            "",
            "Rules:",
            "",
            "1. **Dump first, then process** — save sizeable raw results to",
            "   `work/*.json` immediately, compute on the saved data, and print ONLY",
            "   compact summaries (counts, key stats, small tables). Never print raw",
            "   payloads: only stdout returns to you.",
            "2. A failed call raises `ToolCallError` (fields: `server`, `tool`) —",
            "   try/except it to handle and continue.",
            "3. Before first use of a tool, read its full doc:",
            "   `tools/docs/<server>/<tool>.md` (signature, allowed values, example).",
            "4. Chain related fetches + analysis in one program instead of one call",
            "   per program.",
            "",
            "## Available tools",
            "",
        ]
        for server_name in sorted(tools_by_server):
            module = sanitize_name(server_name) or "server"
            lines.append(f"### {server_name}  (import: `from tools.{module} import ...`)")
            lines.append("")
            for tool in sorted(tools_by_server[server_name], key=lambda t: t.name):
                bindings = self._bind_params(tool)
                if bindings is None:
                    continue
                func = sanitize_name(tool.name)
                signature = self._render_signature_params(bindings)
                lines.append(f"- `{func}({signature})`")
                summary = (tool.description or "").strip().splitlines()
                if summary and summary[0]:
                    lines.append(f"  - {summary[0][:160]}")
            lines.append("")
        return "\n".join(lines)

    # ----------------------------------------------------------------- client

    def generate_client_config(self, server_names: list[str]) -> dict[str, Any]:
        """The per-workspace config dict the client runtime consumes.

        Deliberately credential-free: the client only needs the server-name
        list for a clearer early error. URLs and headers stay kernel-side.
        """
        return {"servers": sorted(server_names)}

    def generate_mcp_client_code(self, server_names: list[str]) -> str:
        """Compose the deployable mcp_client.py: static runtime + epilogue."""
        config_json = json.dumps(self.generate_client_config(server_names), sort_keys=True)
        return (
            client_runtime_source()
            + "\n\n# --- Per-workspace configuration (generated epilogue). ---\n"
            + f"_apply_config_dict(json.loads({json.dumps(config_json)}))\n"
        )
