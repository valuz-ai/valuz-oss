"""Read-only event payloads.

A hook sees the event its caller passed in, and changes it only by handing a
changed copy to ``next`` — the same rule Claude Code mods follow (``e`` is
deep-frozen). ``freeze`` turns a JSON-shaped value into one that raises on
mutation but still serializes as plain JSON (``FrozenDict`` is a ``dict``,
sequences become tuples); ``thaw`` gives back ordinary mutable containers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NoReturn


class FrozenDict(dict[str, Any]):
    """A ``dict`` that refuses mutation. JSON-serializable as-is."""

    __slots__ = ()

    def _read_only(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise TypeError(
            "hook event data is read-only — pass a changed copy to next() "
            "(event.with_data(...)) instead of mutating it"
        )

    __setitem__ = _read_only
    __delitem__ = _read_only
    clear = _read_only
    pop = _read_only
    popitem = _read_only
    setdefault = _read_only
    update = _read_only
    __ior__ = _read_only

    def __copy__(self) -> FrozenDict:
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> FrozenDict:
        return self

    def __reduce__(self) -> tuple[Any, ...]:
        return (FrozenDict, (dict(self),))

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(tuple(sorted((k, _hashable(v)) for k, v in self.items())))


def _hashable(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return hash(value)
    if isinstance(value, (list, dict, set)):
        return repr(value)
    return value


def freeze(value: Any) -> Any:
    """Deep-freeze a JSON-shaped value (mappings, sequences, scalars)."""
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        frozen = FrozenDict()
        for key, item in value.items():
            dict.__setitem__(frozen, str(key), freeze(item))
        return frozen
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    """Inverse of :func:`freeze`: plain dicts and lists, recursively."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [thaw(item) for item in value]
    return value


__all__ = ["FrozenDict", "freeze", "thaw"]
