"""Fail-closed Sandbox maintenance seam for cutover and terminal receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SandboxMaintenanceUnsupportedError(RuntimeError):
    """The active backend cannot prove a maintenance operation."""


SandboxMaintenanceUnsupported = SandboxMaintenanceUnsupportedError


@dataclass(frozen=True)
class SandboxMaintenanceProbe:
    sandbox_id: str
    foreground_active: bool
    background_active: bool
    supports_freeze: bool
    supports_copy_out: bool
    supports_rehash: bool
    supports_stop_and_seal: bool

    @property
    def supported(self) -> bool:
        return all(
            (
                self.supports_freeze,
                self.supports_copy_out,
                self.supports_rehash,
                self.supports_stop_and_seal,
            )
        )


@dataclass(frozen=True)
class SandboxMaintenanceLease:
    sandbox_id: str
    lease_id: str
    fencing_token: str
    expires_at: int


@dataclass(frozen=True)
class SandboxTerminalReceipt:
    sandbox_id: str
    fencing_token: str
    frozen_hash: str
    copy_out_ref: str
    sealed: bool


class SandboxMaintenancePort(Protocol):
    async def acquire_maintenance_lease(
        self, sandbox_id: str, *, owner: str, ttl_seconds: int
    ) -> SandboxMaintenanceLease: ...

    async def probe_activity(
        self, lease: SandboxMaintenanceLease
    ) -> SandboxMaintenanceProbe: ...

    async def freeze_filesystem(self, lease: SandboxMaintenanceLease) -> None: ...

    async def probe(self, lease: SandboxMaintenanceLease) -> SandboxMaintenanceProbe: ...

    async def freeze(self, lease: SandboxMaintenanceLease) -> None: ...

    async def copy_out(self, lease: SandboxMaintenanceLease) -> str: ...

    async def rehash_frozen(self, lease: SandboxMaintenanceLease) -> str: ...

    async def stop_and_seal(
        self,
        lease: SandboxMaintenanceLease,
        *,
        copy_out_ref: str,
        frozen_hash: str,
    ) -> SandboxTerminalReceipt: ...


class UnsupportedSandboxMaintenancePort:
    """Explicit default; callers must not turn UNSUPPORTED into success."""

    async def probe(self, lease: SandboxMaintenanceLease | str) -> SandboxMaintenanceProbe:
        sandbox_id = lease.sandbox_id if isinstance(lease, SandboxMaintenanceLease) else lease
        return SandboxMaintenanceProbe(
            sandbox_id=sandbox_id,
            foreground_active=False,
            background_active=False,
            supports_freeze=False,
            supports_copy_out=False,
            supports_rehash=False,
            supports_stop_and_seal=False,
        )

    async def acquire_maintenance_lease(
        self, sandbox_id: str, *, owner: str, ttl_seconds: int
    ) -> SandboxMaintenanceLease:
        await self._unsupported("acquire_maintenance_lease")
        raise AssertionError("unreachable")

    async def probe_activity(self, lease: SandboxMaintenanceLease) -> SandboxMaintenanceProbe:
        await self._unsupported("probe_activity")
        raise AssertionError("unreachable")

    async def freeze_filesystem(self, lease: SandboxMaintenanceLease) -> None:
        await self._unsupported("freeze_filesystem")

    async def _unsupported(self, operation: str) -> None:
        raise SandboxMaintenanceUnsupported(operation)

    async def freeze(self, lease: SandboxMaintenanceLease) -> None:
        await self._unsupported("freeze")

    async def copy_out(self, lease: SandboxMaintenanceLease) -> str:
        await self._unsupported("copy_out")
        raise AssertionError("unreachable")

    async def rehash_frozen(self, lease: SandboxMaintenanceLease) -> str:
        await self._unsupported("rehash_frozen")
        raise AssertionError("unreachable")

    async def stop_and_seal(
        self,
        lease: SandboxMaintenanceLease,
        *,
        copy_out_ref: str,
        frozen_hash: str,
    ) -> SandboxTerminalReceipt:
        await self._unsupported("stop_and_seal")
        raise AssertionError("unreachable")


__all__ = [
    "SandboxMaintenancePort",
    "SandboxMaintenanceProbe",
    "SandboxMaintenanceLease",
    "SandboxMaintenanceUnsupported",
    "SandboxTerminalReceipt",
    "UnsupportedSandboxMaintenancePort",
]
