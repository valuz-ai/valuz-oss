"""Deprecated compatibility import; use ``valuz_agent.modules.automations.app_plugin_support``."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module(
    "valuz_agent.modules.automations.app_plugin_support"
)
