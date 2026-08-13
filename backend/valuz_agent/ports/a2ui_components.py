"""A2UI component registry — backend prompt injection for edition extensions.

The frontend half (``@valuz/a2ui`` ``registerA2UIComponents``) puts an
edition's components into the A2UI renderer; this registry puts the matching
catalog text into the ``generate_ui`` compiler prompt. Ship one half without
the other and the failure is silent in both directions — a described component
that cannot render, or a renderable component the model is never told
about — so editions register here from the same generated asset their
frontend install reads (see ``docs/design/a2ui-dynamic-components.md``).

Design decisions this module fixes (the doc's two open points):

* **No per-owner scope.** The catalog is an *edition build* property, not an
  org property: a single-tenant desktop and a per-distribution deployment both
  run exactly one edition per process. Keying the prompt by owner would push
  tenancy into prompt assembly for a case no deployment has; if one appears,
  it arrives as a new port rather than a widening of this one.
* **Layer order mirrors the frontend.** Fixed ``commercial → distribution``;
  a name collision is refused, never resolved by merge order (the earlier
  layer wins deterministically). A layer may ``replace`` the OSS baseline
  wholesale — but never another layer's components, and never the root.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

A2UIComponentLayer = Literal["commercial", "distribution"]
A2UIComponentMode = Literal["append", "replace"]

_LAYER_ORDER: tuple[A2UIComponentLayer, ...] = ("commercial", "distribution")

#: The one name ``replace`` still refuses — a document with no resolvable root
#: renders nothing at all, so the root survives every mode (frontend rule).
ROOT_COMPONENT_NAME = "Stack"


@dataclass
class A2UIComponentRegisterResult:
    """What a registration accomplished. ``rejected`` must be surfaced by the
    caller — a silently dropped name is the exact failure this design exists
    to prevent."""

    accepted: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class _Registration:
    group: str
    #: ``(component name, pre-rendered catalog line)`` pairs. The line is authored
    #: by the edition's generator from the same zod schemas its renderer
    #: registers — this registry never re-renders it, so it cannot drift.
    entries: tuple[tuple[str, str], ...]
    notes: tuple[str, ...]
    mode: A2UIComponentMode


class A2UIComponentRegistry:
    """Process-wide registry consumed by the genui prompt assembly.

    The OSS baseline (the generated ``a2ui_component_catalog.txt``) is bound
    lazily by ``modules/genui/protocol`` —
    registration may legally happen earlier (overlay startup), so collisions
    against the baseline are re-checked at bind time and offending names are
    dropped loudly.
    """

    def __init__(self) -> None:
        self._layers: dict[A2UIComponentLayer, _Registration] = {}
        self._baseline_names: frozenset[str] | None = None
        self._baseline_catalog_text: str = ""
        self._dropped_at_bind: list[tuple[str, str]] = []

    # -- baseline -----------------------------------------------------------

    @property
    def baseline_bound(self) -> bool:
        return self._baseline_names is not None

    def bind_baseline(self, *, names: Iterable[str], catalog_text: str) -> None:
        """Bind the OSS baseline. Idempotent; re-validates prior registrations."""

        self._baseline_names = frozenset(names)
        self._baseline_catalog_text = catalog_text.rstrip("\n")
        for layer, registration in list(self._layers.items()):
            kept, dropped = self._split_baseline_collisions(registration)
            if dropped:
                self._dropped_at_bind.extend(dropped)
                logger.error(
                "A2UI components dropped at baseline bind (layer=%s): %s",
                    layer,
                    "; ".join(f"{name}: {reason}" for name, reason in dropped),
                )
                self._layers[layer] = _Registration(
                    group=registration.group,
                    entries=kept,
                    notes=registration.notes,
                    mode=registration.mode,
                )

    def _split_baseline_collisions(
        self, registration: _Registration
    ) -> tuple[tuple[tuple[str, str], ...], list[tuple[str, str]]]:
        # Under replace the baseline is suppressed, so its names are free for
        # the taking — except the root, which is checked at register time.
        if registration.mode == "replace" or self._baseline_names is None:
            return registration.entries, []
        kept: list[tuple[str, str]] = []
        dropped: list[tuple[str, str]] = []
        for name, line in registration.entries:
            if name in self._baseline_names:
                dropped.append((name, "name is already taken by a built-in component"))
            else:
                kept.append((name, line))
        return tuple(kept), dropped

    # -- registration -------------------------------------------------------

    def register(
        self,
        layer: A2UIComponentLayer,
        *,
        group: str,
        entries: Sequence[tuple[str, str]],
        notes: Sequence[str] = (),
        mode: A2UIComponentMode = "append",
    ) -> A2UIComponentRegisterResult:
        """Register a layer's components, replacing that layer's previous set.

        Taken names are refused rather than merged; the result's ``rejected``
        carries every refusal with its reason.
        """

        result = A2UIComponentRegisterResult()
        if layer not in _LAYER_ORDER:
            result.rejected = [(name, f"unknown layer {layer!r}") for name, _ in entries]
            return result

        holder = self._replacing_layer(except_layer=layer)
        if mode == "replace" and holder is not None:
            result.rejected = [
                (name, f'layer "{holder}" already replaces the baseline')
                for name, _ in entries
            ]
            return result

        taken = self._taken_names(except_layer=layer, registering_mode=mode)
        seen: set[str] = set()
        accepted: list[tuple[str, str]] = []
        for name, line in entries:
            if not name:
                result.rejected.append((str(name), "entry has no name"))
            elif mode == "replace" and name == ROOT_COMPONENT_NAME:
                result.rejected.append((name, "the root component name cannot be taken"))
            elif name in taken:
                result.rejected.append(
                    (name, "name is already taken by a base component or another layer")
                )
            elif name in seen:
                result.rejected.append((name, "duplicate name within this registration"))
            else:
                seen.add(name)
                accepted.append((name, line))

        if accepted:
            self._layers[layer] = _Registration(
                group=group,
                entries=tuple(accepted),
                notes=tuple(notes),
                mode=mode,
            )
        else:
            self._layers.pop(layer, None)

        if result.rejected:
            logger.error(
                "A2UI components rejected at registration (layer=%s): %s",
                layer,
                "; ".join(f"{name}: {reason}" for name, reason in result.rejected),
            )
        result.accepted = [name for name, _ in accepted]
        return result

    def unregister(self, layer: A2UIComponentLayer) -> None:
        self._layers.pop(layer, None)

    def _replacing_layer(self, except_layer: A2UIComponentLayer | None = None) -> str | None:
        for layer, registration in self._layers.items():
            if layer != except_layer and registration.mode == "replace":
                return layer
        return None

    def _taken_names(
        self, *, except_layer: A2UIComponentLayer, registering_mode: A2UIComponentMode
    ) -> frozenset[str]:
        names: set[str] = set()
        # Baseline names are off-limits under append; replace suppresses them
        # (only the root stays protected, handled by the caller).
        if registering_mode == "append" and self._baseline_names is not None:
            names.update(self._baseline_names)
        for layer, registration in self._layers.items():
            if layer == except_layer:
                continue
            names.update(name for name, _ in registration.entries)
        return frozenset(names)

    # -- prompt assembly ----------------------------------------------------

    def baseline_suppressed(self) -> bool:
        """True when a layer holds ``replace`` and suppresses the base catalog."""

        return self._replacing_layer() is not None

    def registered_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for layer in _LAYER_ORDER
            if (registration := self._layers.get(layer))
            for name, _ in registration.entries
        )

    def rejected_at_bind(self) -> tuple[tuple[str, str], ...]:
        """Names dropped when the baseline arrived after registration — for
        boot assertions."""

        return tuple(self._dropped_at_bind)

    def has_registrations(self) -> bool:
        return bool(self._layers)

    def catalog_text(
        self,
        *,
        baseline: bool = True,
        names: Iterable[str] | None = None,
        include_notes: bool = True,
        include_notes_without_entries: bool = False,
        note_keys: Iterable[str] | None = None,
    ) -> str:
        """The A2UI catalog: baseline ⧺ registered layers in
        fixed order, each registered layer as its own titled group.

        ``baseline=False`` returns the registered layers alone — what the
        ``edition`` scope asks for, since that scope offers what an edition
        installed *instead of* this repository's own set.
        """

        selected = frozenset(names) if names is not None else None
        selected_note_keys = (
            frozenset(note_keys) if note_keys is not None else None
        )
        parts: list[str] = []
        if baseline and not self.baseline_suppressed() and self._baseline_catalog_text:
            if selected is None:
                parts.append(self._baseline_catalog_text)
            else:
                lines = [
                    line
                    for line in self._baseline_catalog_text.splitlines()
                    if any(
                        line.lstrip().startswith(f"- {name}(")
                        for name in selected
                    )
                ]
                if lines:
                    parts.append("\n".join(lines))
        for layer in _LAYER_ORDER:
            registration = self._layers.get(layer)
            if registration is None:
                continue
            entries = (
                registration.entries
                if selected is None
                else tuple(
                    (name, line)
                    for name, line in registration.entries
                    if name in selected
                )
            )
            if not entries and not (
                include_notes_without_entries and registration.notes
            ):
                continue
            lines = "\n".join(line for _, line in entries)
            heading = (
                f"- {registration.group} components:"
                if entries
                else f"- {registration.group} data and composition notes:"
            )
            section = f"{heading}\n{lines}" if lines else heading
            notes_to_include = registration.notes
            if selected_note_keys is not None:
                notes_to_include = tuple(
                    note
                    for note in registration.notes
                    if not _note_contract_key(note)
                    or _note_contract_key(note) in selected_note_keys
                )
            if include_notes and notes_to_include:
                notes = "\n".join(f"  {note}" for note in notes_to_include)
                section = f"{section}\n{notes}"
            parts.append(section)
        return "\n".join(parts)

    # -- test seam ----------------------------------------------------------

    def reset(self) -> None:
        self._layers.clear()
        self._dropped_at_bind.clear()


def _note_contract_key(note: str) -> str | None:
    """The component name from one generated component-data contract note."""

    prefix = "COMPONENT_DATA_CONTRACT "
    stripped = note.strip()
    if not stripped.startswith(prefix):
        return None
    try:
        import json

        payload = json.loads(stripped.removeprefix(prefix))
    except (json.JSONDecodeError, TypeError):
        return None
    component = payload.get("component") if isinstance(payload, dict) else None
    return component if isinstance(component, str) and component else None


__all__ = [
    "A2UIComponentLayer",
    "A2UIComponentMode",
    "A2UIComponentRegisterResult",
    "A2UIComponentRegistry",
    "ROOT_COMPONENT_NAME",
]
