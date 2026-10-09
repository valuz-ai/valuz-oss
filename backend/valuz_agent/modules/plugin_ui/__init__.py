"""The UI bus: plugins hand Valuz element trees, the frontend draws them.

See docs/design/plugin-architecture/hooks-and-plugin-ui.md §6 (commercial
repo). Registry and pushes are in-process; routes in
``api/routes/plugin_ui.py``; backend plugins use ``ctx.ui``.
"""

from __future__ import annotations

from valuz_agent.modules.plugin_ui.elements import DEFAULT, InvalidTree, normalize_tree
from valuz_agent.modules.plugin_ui.push import UiPush, UiPushHub, ui_push_hub
from valuz_agent.modules.plugin_ui.registry import (
    StaleAction,
    UiProvider,
    UiRegistry,
    UiRequest,
    ui_registry,
)

__all__ = [
    "DEFAULT",
    "InvalidTree",
    "StaleAction",
    "UiProvider",
    "UiPush",
    "UiPushHub",
    "UiRegistry",
    "UiRequest",
    "normalize_tree",
    "ui_push_hub",
    "ui_registry",
]
