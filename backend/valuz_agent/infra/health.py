from collections.abc import Callable

from pydantic import BaseModel


class HealthStatus(BaseModel):
    name: str
    status: str
    detail: str | None = None


HealthCheck = Callable[[], HealthStatus]
