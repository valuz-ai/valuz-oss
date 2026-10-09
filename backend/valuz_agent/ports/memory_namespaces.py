"""Host authorization for registered memory namespaces, not model-granted scope."""

from __future__ import annotations

from typing import Protocol

from valuz_agent.ports.memory import MemoryMutationCommand, MemoryProtected, MemorySnapshot


class MemoryNamespacePolicy(Protocol):
    def is_registered(self, namespace: str) -> bool: ...

    def registration_token(self, namespace: str) -> object | None: ...

    async def authorize_read(
        self, owner_user_id: str, namespace: str, snapshot: MemorySnapshot
    ) -> None: ...

    async def authorize_write(
        self, owner_user_id: str, command: MemoryMutationCommand, access: object | None
    ) -> None: ...


class CoreMemoryNamespacePolicy:
    def is_registered(self, namespace: str) -> bool:
        return namespace == "core"

    def registration_token(self, namespace: str) -> object | None:
        return self if namespace == "core" else None

    async def authorize_read(
        self, owner_user_id: str, namespace: str, snapshot: MemorySnapshot
    ) -> None:
        if not self.is_registered(namespace) or snapshot.owner_user_id != owner_user_id:
            raise MemoryProtected("Memory namespace is not authorized")

    async def authorize_write(
        self, owner_user_id: str, command: MemoryMutationCommand, access: object | None
    ) -> None:
        if not self.is_registered(command.namespace):
            raise MemoryProtected("Memory namespace is not authorized")


# A process-local capability, never serializable as a DTO or supplied by HTTP.
_ISSUED = object()


class MemoryNamespaceWriteApproval:
    def __init__(
        self,
        owner_user_id: str,
        namespace: str,
        fingerprint: str,
        policy: MemoryNamespacePolicy,
        original_access: object,
        seal: object,
    ) -> None:
        if seal is not _ISSUED:
            raise MemoryProtected(
                "Namespace approval must be issued by the host authorization path"
            )
        self._owner = owner_user_id
        self._namespace = namespace
        self._fingerprint = fingerprint
        self._policy = policy
        self._registration = policy.registration_token(namespace)
        self._original_access = original_access
        self._seal = seal

    def permits(self, owner_user_id: str, namespace: str, fingerprint: str) -> bool:
        from valuz_agent.ports.extensions import ext

        return (
            self._seal is _ISSUED
            and namespace != "core"
            and self._original_access is not None
            and self._owner == owner_user_id
            and self._namespace == namespace
            and self._fingerprint == fingerprint
            and self._policy is ext.memory_namespace_policy
            and self._registration is not None
            and self._policy.registration_token(namespace) is self._registration
            and self._policy.is_registered(namespace)
        )


async def authorize_namespace_write(
    owner_user_id: str, command: MemoryMutationCommand, access: object | None, fingerprint: str
) -> MemoryNamespaceWriteApproval | None:
    from valuz_agent.ports.extensions import ext

    policy = ext.memory_namespace_policy
    registration = policy.registration_token(command.namespace)
    await policy.authorize_write(owner_user_id, command, access)
    if command.namespace == "core":
        return None
    if (
        registration is None
        or policy.registration_token(command.namespace) is not registration
        or access is None
        or command.source != "user"
        or command.action != "add"
    ):
        raise MemoryProtected("A verified explicit namespace learning approval is required")
    return MemoryNamespaceWriteApproval(
        owner_user_id, command.namespace, fingerprint, policy, access, _ISSUED
    )
