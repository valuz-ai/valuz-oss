"""Top-level container for valuz plugin categories.

Each immediate sub-package groups one plugin category. ``parser/`` is
the only category today; future categories (e.g. ``mcp/``, ``runtime/``,
``tools/``) follow the same shape — a category package that scans its
own sub-packages by convention and exposes a ``make_plugin``-style
factory contract.

See ``plugins/parser/__init__.py`` for the per-category README.
"""
