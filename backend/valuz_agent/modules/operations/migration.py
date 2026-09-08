"""Bounded identity-preserving history import for trusted outer transactions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, cast

from sqlalchemy import Boolean, Connection, Integer, String, Table, insert, select
from sqlalchemy.exc import IntegrityError

from valuz_agent.modules.evidence.identity import bounded_payload
from valuz_agent.modules.evidence.transaction import ensure_outer_transaction
from valuz_agent.modules.operations.models import ConfirmationDecisionRow, OperationRecordRow

OperationRecordKind = Literal["operation", "decision"]
_TABLES: dict[str, Table] = {
    "operation": cast(Table, OperationRecordRow.__table__),
    "decision": cast(Table, ConfirmationDecisionRow.__table__),
}
_LISTS = {"target_refs", "canonical_result_refs"}


def validate_operation_record(kind: OperationRecordKind, record: Any, owner_user_id: str) -> None:
    if not isinstance(owner_user_id, str) or not owner_user_id.strip() or len(owner_user_id) > 64:
        raise ValueError("operation_import_owner_required")
    if kind not in _TABLES:
        raise ValueError("operation_import_kind_invalid")
    table = _TABLES[kind]
    if type(record) is not dict or set(record) != set(table.columns.keys()):
        raise ValueError("operation_import_columns_invalid")
    bounded_payload(record)
    if record["user_id"] != owner_user_id or not record["id"]:
        raise ValueError("operation_import_owner_mismatch")
    for column in table.columns:
        value = record[column.name]
        if value is None:
            if column.nullable:
                continue
            raise ValueError("operation_import_required_field")
        if isinstance(column.type, Boolean):
            valid = type(value) is bool
        elif isinstance(column.type, Integer):
            valid = type(value) is int and -(2**63) <= value < 2**63
        elif isinstance(column.type, String):
            valid = type(value) is str and (
                column.type.length is None or len(value) <= column.type.length
            )
        else:
            valid = type(value) is (list if column.name in _LISTS else dict)
        if not valid:
            raise ValueError("operation_import_invalid_field")
    if len(record["proposal_hash"]) != 64:
        raise ValueError("operation_import_hash_invalid")
    if kind == "operation":
        if record["operation_version"] < 1 or not record["idempotency_key"]:
            raise ValueError("operation_import_identity_invalid")
        enums = {
            "state": {
                "proposed",
                "awaiting_confirmation",
                "executing",
                "succeeded",
                "failed",
                "cancelled",
                "expired",
                "stale",
                "superseded",
            },
            "risk_level": {"low", "material", "destructive", "external"},
            "actor_kind": {"user", "agent", "playbook", "automation", "system"},
            "confirmation_policy": {
                "direct",
                "explicit_submit",
                "confirm",
                "approval",
                "preauthorized",
            },
        }
        if any(record[field] not in values for field, values in enums.items()):
            raise ValueError("operation_import_enum_invalid")
        if any(type(ref) is not dict for field in _LISTS for ref in record[field]):
            raise ValueError("operation_import_invalid_reference")
    elif record["decision"] not in {"approve", "reject", "request_changes"}:
        raise ValueError("operation_import_enum_invalid")


def import_operation_records(
    connection: Connection,
    *,
    owner_user_id: str,
    kind: OperationRecordKind,
    records: Sequence[dict[str, Any]],
) -> int:
    """Insert missing original records; never authorize or overwrite history.

    The caller verifies the complete selected reference closure and permission
    to migrate before entering this port. No HTTP endpoint accepts these rows.
    Existing live records remain live only on an exact replay, and missing
    decisions cannot be attached to them. New operations are always historical.
    """
    if not isinstance(owner_user_id, str) or not owner_user_id.strip() or len(owner_user_id) > 64:
        raise ValueError("operation_import_owner_required")
    if len(records) > 100 or kind not in _TABLES:
        raise ValueError("operation_import_invalid_batch")
    for record in records:
        validate_operation_record(kind, record, owner_user_id)
    table = _TABLES[kind]
    inserted = 0
    ensure_outer_transaction(connection)
    try:
        with connection.begin_nested():
            for record in records:
                existing = (
                    connection.execute(select(table).where(table.c.id == record["id"]))
                    .mappings()
                    .first()
                )
                comparison = dict(record)
                if kind == "operation" and existing is not None and existing["historical_only"]:
                    comparison["historical_only"] = True
                if existing is not None:
                    if dict(existing) != comparison:
                        raise ValueError("operation_import_identity_conflict")
                    continue
                if kind == "operation":
                    comparison["historical_only"] = True
                else:
                    parent = (
                        connection.execute(
                            select(OperationRecordRow.__table__)
                            .where(
                                OperationRecordRow.id == record["operation_id"],
                                OperationRecordRow.user_id == owner_user_id,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .first()
                    )
                    if parent is None or parent["proposal_hash"] != record["proposal_hash"]:
                        raise ValueError("operation_import_decision_parent_mismatch")
                    if not parent["historical_only"]:
                        raise ValueError("operation_import_live_decision_conflict")
                connection.execute(insert(table).values(**comparison))
                inserted += 1
    except IntegrityError as exc:
        raise ValueError("operation_import_identity_conflict") from exc
    return inserted
