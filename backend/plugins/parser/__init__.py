"""Built-in parser plugins, scanned by convention.

Each immediate sub-package of this module is treated as a parser plugin
candidate. The host's ``build_default_registry`` (see
``valuz_agent.modules.parser.registry``) walks
``pkgutil.iter_modules(plugins.parser.__path__)`` at startup, imports
each sub-package, and calls its ``make_plugin(scheduler=...)`` factory.
Sub-packages that don't expose ``make_plugin`` are skipped with a
warning.

This is the canonical home for in-tree parser plugins. Adding a new
built-in is a four-step recipe with no global state to thread through:

  1. Create ``plugins/parser/<id>/__init__.py``
  2. Export ``make_plugin(scheduler) -> ParserPlugin``
  3. Add the matching frontend package under
     ``frontend/packages/parser-plugins/src/<id>/`` (locale JSON +
     ``register()`` call)
  4. Wire frontend ``register()`` into
     ``frontend/packages/parser-plugins/src/index.ts``

Out-of-tree plugins (Phase 2 — separate pypi packages) opt in via the
``valuz.parser_plugins`` entry-point group instead, and the same
``build_default_registry`` fans those in additively. Built-ins always
win on id collisions; the registry logs and skips conflicting third-
party plugins.
"""
