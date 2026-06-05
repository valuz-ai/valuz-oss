from typing import Any, Protocol


class RemoteResourcePort(Protocol):
    """Commercial overlay: fetches remote resource manifests from CDN.

    OSS injects None; commercial injects an HTTP client.
    """

    async def fetch_manifest(self) -> dict[str, Any] | None:
        """Pull the remote resource manifest. Returns None on network error."""
        ...

    async def fetch_skill_content(self, content_url: str) -> bytes | None:
        """Download a skill content tarball. Returns None on network error."""
        ...
