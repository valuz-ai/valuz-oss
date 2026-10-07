"""The ``BackendPlugin`` contract (docs/design/plugin-architecture §7).

A plugin is one reversible contribution unit -- the Python half of a feature,
the counterpart of the frontend ``ValuzPlugin``. It declares what it ``needs``
and ``provides`` (service / port names, the equivalent of DSH ``inject``), and
its ``apply(ctx, config)`` registers contributions through the
:class:`~valuz_agent.plugin_host.context.PluginContext`. Every registration
returns a disposer that lands on the plugin's ``ExitStack``, so a plugin that
fails halfway is rolled back to exactly where it started.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from valuz_agent.plugin_host.context import PluginContext

#: What a toggle / config edit means for the running process. Backend plugins
#: contribute FastAPI routes and process-wide ports, neither of which can be
#: unloaded safely, so every backend change is ``restart-required``; ``applied``
#: exists for symmetry with the frontend host and DSH's PluginManager.
ChangeResult = Literal["applied", "restart-required"]

#: ``disabled`` — switched off for this start (plugin prefs or
#: ``set_enabled``), never applied; ``disposed`` — applied, then unloaded.
PluginStatus = Literal["pending", "active", "failed", "disabled", "disposed"]


@runtime_checkable
class BackendPlugin(Protocol):
    """Structural contract the host accepts.

    Members are read-only properties so a plugin may declare them as plain class
    attributes (``id = "x"``), instance attributes, or properties -- all satisfy
    the protocol.
    """

    @property
    def id(self) -> str: ...

    @property
    def needs(self) -> tuple[str, ...]: ...

    @property
    def provides(self) -> tuple[str, ...]: ...

    @property
    def required(self) -> bool:
        """``True`` => a failure aborts startup with ``PluginStartupError``;
        ``False`` => the failure is rolled back, logged and recorded, and the
        rest of the host keeps going."""
        ...

    @property
    def entitlement(self) -> str | None:
        """Org-level gate key (design §8.3). Declared here, enforced per request
        by the P3 entitlement layer -- the host only carries it."""
        ...

    @property
    def Config(self) -> type[BaseModel] | None: ...  # noqa: N802 - contract name

    def apply(self, ctx: PluginContext, config: Any) -> None: ...


class BackendPluginBase:
    """Convenience base with the contract's defaults. Subclass and set ``id``."""

    id: str = ""
    needs: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    required: bool = False
    entitlement: str | None = None
    Config: type[BaseModel] | None = None

    def apply(self, ctx: PluginContext, config: Any) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def migrations(self) -> Sequence[Any]:
        """Alembic chains this plugin owns (static: no side effects).

        Declared separately from ``apply`` because migrations run in processes
        that never compose the app (``migrate``, the serve pre-boot path).
        Disabling a plugin never rolls its tables back (design §7).
        """
        return ()
