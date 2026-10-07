"""npm-style version ranges (``engines.valuz-plugin-api``)."""

from __future__ import annotations

import pytest

from valuz_agent.modules.app_plugins.semver import (
    compare,
    is_newer,
    is_valid_range,
    parse_version,
    satisfies,
)


@pytest.mark.parametrize(
    ("version", "range_text", "expected"),
    [
        ("1.0.0", "^1.0.0", True),
        ("1.4.2", "^1.0.0", True),
        ("2.0.0", "^1.0.0", False),
        ("0.2.5", "^0.2.3", True),
        ("0.3.0", "^0.2.3", False),
        ("0.0.3", "^0.0.3", True),
        ("0.0.4", "^0.0.3", False),
        ("1.0.5", "~1.0.0", True),
        ("1.1.0", "~1.0.0", False),
        ("1.9.0", "~1", True),
        ("1.0.0", ">=1.0.0", True),
        ("0.9.9", ">=1.0.0", False),
        ("1.0.0", ">1.0.0", False),
        ("1.0.1", ">1.0.0", True),
        ("1.0.0", "<=1.0.0", True),
        ("1.0.1", "<=1.0.0", False),
        ("0.9.0", "<1.0.0", True),
        ("1.0.0", "<1.0.0", False),
        ("1.0.0", "=1.0.0", True),
        ("1.0.0", "1.0.0", True),
        ("1.0.1", "1.0.0", False),
        ("1.2.3", "*", True),
        ("1.2.3", "x", True),
        ("1.2.3", "1.x", True),
        ("2.0.0", "1.x", False),
        ("1.2.9", "1.2.x", True),
        ("1.3.0", "1.2", False),
        ("1.5.0", ">=1.2.0 <2.0.0", True),
        ("2.0.0", ">=1.2.0 <2.0.0", False),
        ("1.1.0", ">=1.2.0 <2.0.0", False),
        ("0.5.0", "^1.0.0 || ^0.5.0", True),
        ("3.0.0", "^1.0.0 || ^2.0.0", False),
        ("1.5.0", ">= 1.2.0", True),
        ("1.5.0", "1.2.3 - 2.3.4", True),
        ("2.4.0", "1.2.3 - 2.3.4", False),
        ("2.3.9", "1.2.3 - 2.3", True),
    ],
)
def test_satisfies(version: str, range_text: str, expected: bool) -> None:
    assert satisfies(version, range_text) is expected


def test_prerelease_only_satisfies_a_range_that_names_its_tuple() -> None:
    assert satisfies("1.1.0-beta.1", "^1.0.0") is False
    assert satisfies("1.1.0-beta.1", ">=1.1.0-alpha") is True
    assert satisfies("1.1.0-beta.1", "^1.1.0-beta.0") is True


def test_invalid_ranges_are_rejected() -> None:
    assert is_valid_range("^1.0.0")
    assert not is_valid_range("banana")
    with pytest.raises(ValueError):
        satisfies("1.0.0", "^^")


def test_precedence() -> None:
    order = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta", "1.0.0", "1.0.1", "1.1.0", "2.0.0"]
    versions = [parse_version(v) for v in order]
    for lower, higher in zip(versions, versions[1:], strict=False):
        assert compare(lower, higher) == -1
        assert compare(higher, lower) == 1
    assert compare(parse_version("1.0.0+build1"), parse_version("1.0.0+build2")) == 0
    assert is_newer("1.10.0", "1.9.0")
    assert not is_newer("1.0.0", "1.0.0")
