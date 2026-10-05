"""The UI bus element tree — what a plugin hands Valuz to draw.

A node is a string (text) or ``{"type", "props"?, "children"?}``. The element
set follows Claude Code mods' (Box / Text / Button / Input / Select / Link /
Code / Markdown / Image) plus ``Badge``; ``Default`` stands for "what this
place would show without the plugin" — the mods ``next(e)`` — and may carry
``props.overrides`` for the host's own drawing. The frontend draws every
element with Valuz design-system components, so plugin UI looks native.

``normalize_tree`` validates and trims a tree: unknown element types are an
error, unknown props are dropped, strings and sizes are bounded.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MAX_NODES = 500
MAX_DEPTH = 20
MAX_TEXT = 20_000

_TONES = {"default", "muted", "success", "warning", "error", "brand"}

# element -> {prop: validator}
_PROPS: dict[str, dict[str, Any]] = {
    "Box": {
        "direction": {"row", "column"},
        "gap": range(0, 9),
        "padding": range(0, 9),
        "align": {"start", "center", "end", "stretch"},
        "justify": {"start", "center", "end", "between"},
        "border": bool,
        "wrap": bool,
    },
    "Text": {
        "text": str,
        "tone": _TONES,
        "bold": bool,
        "italic": bool,
        "mono": bool,
        "size": {"xs", "sm", "md"},
    },
    "Badge": {"text": str, "tone": _TONES},
    "Button": {
        "label": str,
        "action": str,
        "variant": {"default", "outline", "ghost", "destructive", "link"},
        "disabled": bool,
    },
    "Input": {"action": str, "placeholder": str, "value": str, "multiline": bool},
    "Select": {"action": str, "options": list, "value": str, "placeholder": str},
    "Link": {"href": str, "text": str},
    "Code": {"code": str, "language": str},
    "Markdown": {"text": str},
    "Image": {"src": str, "alt": str, "width": range(1, 2049)},
    "Default": {"overrides": dict},
}

ELEMENT_TYPES = frozenset(_PROPS)
CONTAINERS = frozenset({"Box"})


class InvalidTree(ValueError):  # noqa: N818 — a validation verdict, not a crash
    pass


def _clip(value: str) -> str:
    return value if len(value) <= MAX_TEXT else value[:MAX_TEXT]


def _valid_prop(rule: Any, value: Any) -> Any:
    if rule is bool:
        return value if isinstance(value, bool) else None
    if rule is str:
        return _clip(value) if isinstance(value, str) else None
    if rule is list:
        return value if isinstance(value, list) else None
    if rule is dict:
        return value if isinstance(value, Mapping) else None
    if isinstance(rule, range):
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value in rule
            else None
        )
    if isinstance(rule, set):
        return value if value in rule else None
    return None


def _options(raw: list[Any]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for item in raw[:200]:
        if isinstance(item, Mapping) and isinstance(item.get("value"), str):
            label = item.get("label")
            options.append(
                {
                    "value": _clip(item["value"]),
                    "label": _clip(label if isinstance(label, str) else item["value"]),
                }
            )
    return options


def _safe_href(href: str) -> bool:
    return href.startswith(("https://", "http://", "mailto:"))


def _safe_src(src: str) -> bool:
    return src.startswith(("https://", "http://", "data:image/"))


def normalize_tree(tree: Any) -> Any:
    """A validated copy of *tree*; raises :class:`InvalidTree`."""
    count = {"nodes": 0}

    def walk(node: Any, depth: int) -> Any:
        count["nodes"] += 1
        if count["nodes"] > MAX_NODES:
            raise InvalidTree(f"tree has more than {MAX_NODES} nodes")
        if depth > MAX_DEPTH:
            raise InvalidTree(f"tree is deeper than {MAX_DEPTH}")
        if isinstance(node, str):
            return _clip(node)
        if not isinstance(node, Mapping):
            raise InvalidTree(f"a node must be a string or an element, got {type(node).__name__}")
        kind = node.get("type")
        if kind not in ELEMENT_TYPES:
            raise InvalidTree(f"unknown element {kind!r}; known: {sorted(ELEMENT_TYPES)}")
        rules = _PROPS[kind]
        raw = node.get("props")
        raw_props: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        props: dict[str, Any] = {}
        for key, rule in rules.items():
            if key not in raw_props:
                continue
            value = _valid_prop(rule, raw_props[key])
            if value is not None:
                props[key] = value
        if kind == "Select" and "options" in props:
            props["options"] = _options(props["options"])
        if kind == "Link" and not _safe_href(str(props.get("href", ""))):
            props.pop("href", None)
        if kind == "Image" and not _safe_src(str(props.get("src", ""))):
            raise InvalidTree("Image src must be http(s) or a data:image URL")
        if kind == "Default" and "overrides" in props:
            props["overrides"] = dict(props["overrides"])
        out: dict[str, Any] = {"type": kind}
        if props:
            out["props"] = props
        children = node.get("children")
        if children is not None:
            if kind not in CONTAINERS:
                raise InvalidTree(f"{kind} cannot have children")
            if not isinstance(children, list):
                raise InvalidTree("children must be a list")
            out["children"] = [walk(child, depth + 1) for child in children]
        return out

    return walk(tree, 0)


DEFAULT = {"type": "Default"}


__all__ = ["DEFAULT", "ELEMENT_TYPES", "InvalidTree", "normalize_tree"]
