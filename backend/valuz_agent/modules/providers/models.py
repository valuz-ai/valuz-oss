from sqlalchemy import BigInteger, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin


class ProviderRow(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "valuz_provider"

    name: Mapped[str] = mapped_column(String(128))
    provider_kind: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32))  # managed | user
    credential_source: Mapped[str] = mapped_column(String(32))  # secret_ref | none
    # Vestigial column retained from the removed hosted-account OAuth
    # subsystem; always NULL now (OSS uses ``secret_ref`` only).
    account_provider_id: Mapped[str | None] = mapped_column(String(64))
    base_url: Mapped[str | None] = mapped_column(Text)
    default_model: Mapped[str | None] = mapped_column(String(128))
    model_ids: Mapped[str | None] = mapped_column(Text)
    secret_ref: Mapped[str | None] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    deletable: Mapped[bool] = mapped_column(Boolean, default=True)
    test_status: Mapped[str] = mapped_column(String(32), default="never")
    test_error: Mapped[str | None] = mapped_column(Text)
    tested_at: Mapped[int | None] = mapped_column(BigInteger)
    protocol: Mapped[str | None] = mapped_column(
        String(32)
    )  # anthropic | openai-completion | openai-response | gemini
    # ``api_key`` (default — credentials live in secret_ref and pass through
    # HTTP headers) or ``oauth`` (credentials live in the
    # provider's CLI keychain, e.g. claude /login or codex /login). OAuth
    # providers skip the connection-test path because the host has no api_key
    # to send — auth happens out-of-band.
    auth_type: Mapped[str] = mapped_column(String(16), default="api_key")
