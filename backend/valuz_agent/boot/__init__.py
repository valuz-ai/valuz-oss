"""Process lifecycle orchestration (boot) — distinct from infra primitives.

Exposes only ``lifespan``; ``api/app.py`` binds it via ``FastAPI(lifespan=…)``.
"""

from valuz_agent.boot.lifespan import lifespan

__all__ = ["lifespan"]
