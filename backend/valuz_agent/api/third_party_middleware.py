"""Deprecated compatibility import; use ``valuz_agent.api.app_plugin_middleware``."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("valuz_agent.api.app_plugin_middleware")
