"""Deprecated compatibility import; use ``valuz_agent.ports.app_plugins``."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("valuz_agent.ports.app_plugins")
