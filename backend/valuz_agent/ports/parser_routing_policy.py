"""Port: whether per-user settings are the last word on which parser runs.

The routing snapshot is per-user settings, which is the right model for a
workstation: the person configured MinerU with their own key, so their files
go to MinerU. A managed deployment inverts it — parsing is a capability the
operator provides and pays for, not a preference the account holds — and the
per-user row such a deployment would have to write is both invisible to that
person (the settings page they see reaches a different backend) and unreliable
to keep current, because it can only be written by a code path that runs at
login.

So the deployment answers the question directly, per router build, from process
state it already has. OSS binds ``UserSettingsParserRoutingPolicy`` and nothing
changes.

The policy *sees* the loaded snapshot rather than replacing the loader: a
deployment that fixes the engine still wants the per-plugin ``plugin_configs``
(secret refs, options) that only settings can supply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from valuz_agent.modules.settings.parser_routing import ParserRoutingConfig


class ParserRoutingPolicyPort(Protocol):
    """Port: the effective routing for one router build."""

    def decide(self, config: ParserRoutingConfig, *, user_id: str) -> ParserRoutingConfig:
        """Return the routing to use, given what settings hold for ``user_id``.

        Must not do I/O: this runs on every router build, including the ones
        the attachment and KB parse paths make per file.
        """
        ...


class UserSettingsParserRoutingPolicy:
    """OSS default: what the user configured is what runs."""

    def decide(
        self,
        config: ParserRoutingConfig,
        *,
        user_id: str,  # noqa: ARG002 — part of the port signature
    ) -> ParserRoutingConfig:
        return config
