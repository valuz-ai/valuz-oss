"""Deprecated compatibility import; use ``valuz_agent.api.routes.builtin_plugins``."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("valuz_agent.api.routes.builtin_plugins")
