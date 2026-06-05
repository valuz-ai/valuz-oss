"""Parser routing module.

Owns:

- ``ParserPluginRegistry`` — the set of plugins this build ships with
- ``ParserRouter`` — implementation of ``ParserBackend`` that dispatches
  to the active plugin based on file kind + settings
- ``SetupJobRow`` + ``PollingTaskRow`` — module-owned tables registered
  with ``Base.metadata`` via the import below so the host Alembic chain
  (``alembic/env.py`` imports every module's models for autogenerate) and
  the test fixtures' ``Base.metadata.create_all`` both see them.

The kernel never touches this module; only ``DocumentLibraryService`` and
``api/deps.py`` should import from here.
"""

# Eagerly import models so SQLAlchemy registers SetupJobRow / PollingTaskRow
# with ``Base.metadata``. The host schema is built by Alembic
# (``run_host_migrations`` at startup), whose ``env.py`` pulls in every
# module's models for autogenerate; test fixtures build the same schema via
# ``Base.metadata.create_all``. Importing for side effect — F401 is intentional.
from valuz_agent.modules.parser import models as models  # noqa: F401
from valuz_agent.modules.parser.registry import (
    ParserPluginRegistry,
    build_default_registry,
)
from valuz_agent.modules.parser.router import ParserRouter

__all__ = [
    "ParserPluginRegistry",
    "ParserRouter",
    "build_default_registry",
    "models",
]
