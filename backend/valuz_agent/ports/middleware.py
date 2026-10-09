"""ASGI middleware construction contract shared by hosts and overlays.

Each middleware constructor has its own keyword arguments, supplied by its
registration. The constructed value must implement the ASGI application contract.
"""

from collections.abc import Callable

from starlette.types import ASGIApp

MiddlewareFactory = Callable[..., ASGIApp]
