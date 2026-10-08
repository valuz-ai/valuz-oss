"""Shared memory DTOs keep their wire contract without importing storage."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from valuz_agent.modules.memory import models
from valuz_agent.ports import memory


@pytest.mark.parametrize(
    "name",
    ["SourceRef", "MemoryRecord", "MemorySnapshot", "MemoryInvalidation", "MemoryMutationResult"],
)
def test_compatibility_exports_are_same_type_and_preserve_titles(name: str) -> None:
    public = getattr(memory, name)
    assert getattr(models, name) is public
    assert public.model_json_schema()["title"] == name


def test_public_source_wire_shape_and_validation_stay_unchanged() -> None:
    source = memory.SourceRef(kind="message", source_id="message-1")
    assert json.loads(source.model_dump_json()) == {
        "kind": "message",
        "source_id": "message-1",
        "revision": None,
        "origin": "untrusted",
    }
    assert models.SourceRef.model_validate_json(source.model_dump_json()) == source
    with pytest.raises(ValidationError):
        memory.SourceRef(kind="arbitrary", source_id="message-1")
    assert models.CHAR_LIMITS == {"user": 1500, "global": 2500, "project": 4000}
    assert not hasattr(memory, "CHAR_LIMITS") and not hasattr(memory, "MemoryStore")


def test_port_import_does_not_load_memory_implementation() -> None:
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import valuz_agent.ports.memory; "
            "assert not any(name.startswith('valuz_agent.modules.memory') for name in sys.modules)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
