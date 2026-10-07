"""Deprecated compatibility import; use ``valuz_agent.integrations.tools_app_plugin_manager``."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("valuz_agent.integrations.tools_app_plugin_manager")
