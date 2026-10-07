"""Deprecated compatibility import; use ``valuz_agent.features.app_plugins``."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("valuz_agent.features.app_plugins")
