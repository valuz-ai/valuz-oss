"""store_wire — domain <-> JSON row codec for the remote StorePort transport.

The single source of truth for how Session / Message / Event / StoredEvent /
UsageRollupRow cross the wire between ``RemoteStoreHttp`` (sandbox client) and
the data service (server). Both sides import this module, so the contract can
never drift between them.

Session / Message reuse the canonical ORM converters
(``src.adapters.sqlalchemy_store.converters``) via an ORM bridge: domain ->
ORM -> {column_name: value} dict, and back. This keeps ONE serialization
definition (the ORM column mapping) — the wire row is "the row as the DB sees
it" (column-name keyed, JSON-safe: str / int / dict / list / None). Event /
StoredEvent / UsageRollupRow are flat enough to map directly.

Owner note: the message wire row carries no authoritative owner — the server
derives the owner from the verified token, never from the body. ``message_to_row``
stamps an empty placeholder; ``row_to_*`` never trusts a body ``user_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy import inspect as sa_inspect
from src.adapters.sqlalchemy_store.converters import (
    message_to_model,
    model_to_message,
    model_to_session,
    session_to_model,
)
from src.adapters.sqlalchemy_store.models import Base, MessageModel, SessionModel
from src.core.events import Event
from src.core.store_port import StoredEvent, UsageRollupRow
from src.core.types import Message, Session


def _model_to_row(model: Base) -> dict[str, Any]:
    """ORM instance -> {column_name: value} (JSON-safe column values).

    Iterates the mapper's column attributes (not ``__table__.columns``) so a
    renamed column maps correctly: attribute ``metadata_`` -> column name
    ``metadata`` (``getattr`` by the attribute key, key the row by column name).

    Datetimes are ISO-encoded: the row must survive JSON (the HTTP DataService
    wire), where a native ``datetime`` raises ``TypeError``. ``_row_to_kwargs``
    parses them back symmetrically.
    """
    mapper = sa_inspect(type(model))
    out: dict[str, Any] = {}
    for attr in mapper.column_attrs:
        value = getattr(model, attr.key)
        if isinstance(value, datetime):
            value = value.isoformat()
        out[attr.columns[0].name] = value
    return out


def _row_to_kwargs(model_cls: type[Base], row: dict[str, Any]) -> dict[str, Any]:
    """{column_name: value} -> {attr_key: value} kwargs for ``model_cls(**)``.

    Maps DB column names back to mapped attribute keys (e.g. ``metadata`` ->
    ``metadata_``). Columns absent from ``row`` are left to model defaults.
    """
    mapper = sa_inspect(model_cls)
    out: dict[str, Any] = {}
    for attr in mapper.column_attrs:
        name = attr.columns[0].name
        if name not in row:
            continue
        value = row[name]
        # Symmetric to ``_model_to_row``: ISO strings back to datetimes for
        # DateTime-typed columns (JSON round-trip).
        if isinstance(value, str) and isinstance(attr.columns[0].type, DateTime):
            value = datetime.fromisoformat(value)
        out[attr.key] = value
    return out


# -- Session --


def session_to_row(session: Session) -> dict[str, Any]:
    return _model_to_row(session_to_model(session))


def row_to_session(row: dict[str, Any]) -> Session:
    return model_to_session(SessionModel(**_row_to_kwargs(SessionModel, row)))


# -- Message --


def message_to_row(message: Message) -> dict[str, Any]:
    # Owner is non-authoritative on the wire (server uses the token owner);
    # stamp an empty placeholder into the user_id column.
    return _model_to_row(message_to_model("", message))


def row_to_message(row: dict[str, Any]) -> Message:
    return model_to_message(MessageModel(**_row_to_kwargs(MessageModel, row)))


# -- Event (write payload) --


def event_to_row(event: Event) -> dict[str, Any]:
    return {"type": event.type, "data": event.data, "timestamp": event.timestamp}


def row_to_event(row: dict[str, Any]) -> Event:
    return Event(
        type=row["type"],
        data=row.get("data") or {},
        timestamp=int(row["timestamp"]) if row.get("timestamp") is not None else 0,
    )


# -- StoredEvent (read projection) --


def stored_event_to_row(ev: StoredEvent) -> dict[str, Any]:
    return {
        "seq": ev.seq,
        "session_id": ev.session_id,
        "message_id": ev.message_id,
        "type": ev.type,
        "data": ev.data,
        "timestamp": ev.timestamp,
        "event_uid": ev.event_uid,
    }


def row_to_stored_event(row: dict[str, Any]) -> StoredEvent:
    return StoredEvent(
        seq=int(row["seq"]),
        session_id=row["session_id"],
        message_id=row["message_id"],
        type=row["type"],
        data=row.get("data") or {},
        timestamp=int(row.get("timestamp", 0)),
        event_uid=row.get("event_uid"),
    )


# -- UsageRollupRow (read projection) --


def usage_rollup_to_row(u: UsageRollupRow) -> dict[str, Any]:
    return {
        "day": u.day,
        "model": u.model,
        "request_count": u.request_count,
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_tokens": u.cache_read_tokens,
        "cache_write_tokens": u.cache_write_tokens,
    }


def row_to_usage_rollup(row: dict[str, Any]) -> UsageRollupRow:
    return UsageRollupRow(
        day=str(row["day"]),
        model=str(row.get("model") or ""),
        request_count=int(row.get("request_count") or 0),
        input_tokens=int(row.get("input_tokens") or 0),
        output_tokens=int(row.get("output_tokens") or 0),
        cache_read_tokens=int(row.get("cache_read_tokens") or 0),
        cache_write_tokens=int(row.get("cache_write_tokens") or 0),
    )
