# Vendored from agent-harness — see KERNEL_VERSION for upstream commit.
# DO NOT EDIT — patch upstream and re-rsync.
#
# This module exists to make the vendored kernel importable.
# The kernel uses bare top-level imports (`from src.core...`, `from app.config...`),
# so we inject this directory onto sys.path on first import. Any caller can then do:
#
#     import kernel  # noqa: F401  (triggers path injection)
#     from src.core import Session, StorePort  # works
#     from app.dependencies import get_store    # works
#
# Importing `kernel` is also safe for type checkers and re-exports below.

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

del _os, _sys, _HERE
