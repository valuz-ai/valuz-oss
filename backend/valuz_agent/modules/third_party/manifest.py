"""Deprecated compatibility import; use ``valuz_agent.modules.app_plugins.manifest``."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("valuz_agent.modules.app_plugins.manifest")
