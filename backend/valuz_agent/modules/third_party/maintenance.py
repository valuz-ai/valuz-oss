"""Deprecated compatibility import; use ``valuz_agent.modules.app_plugins.maintenance``."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("valuz_agent.modules.app_plugins.maintenance")
