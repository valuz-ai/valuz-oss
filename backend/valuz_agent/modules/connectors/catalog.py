"""Connector catalog loading — bundled entries plus edition contributions.

``resources/connector_catalog.json`` ships the connectors every build gets.
A distribution built on this host (a commercial or industry edition) needs to
offer its own connectors without forking that file: forking means every entry
the host later adds is silently missing from the edition, and the divergence
only surfaces as "why isn't this connector in the directory".

``VALUZ_CONNECTOR_CATALOG_EXTRA`` lists extra catalog JSON files (separated by
the platform path separator) merged on top of the bundled one at import. The
build wires it up — no file in this package is ever rewritten.

Merging is by top-level ``slug``:

- a new slug is appended;
- an existing slug is shallow-merged, contributor keys winning;
- when both sides carry ``connectors``, members merge by member slug, so an
  edition can add one connector to a bundled group (and thereby join its OAuth
  credential group) without restating the members it did not write.

Both the directory endpoints and OAuth credential sharing read through here, so
a contributed group behaves exactly like a bundled one — including the
"same auth_type, same origin" proof that sharing demands.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CATALOG_FILE = Path(__file__).parent.parent.parent / "resources" / "connector_catalog.json"
EXTRA_ENV = "VALUZ_CONNECTOR_CATALOG_EXTRA"


def _merge_entry(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """One catalog entry overlaid with a contributor's version of it."""
    merged = {**base, **extra}
    base_members = base.get("connectors")
    extra_members = extra.get("connectors")
    if isinstance(base_members, list) and isinstance(extra_members, list):
        by_slug: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for member in [*base_members, *extra_members]:
            if not isinstance(member, dict):
                continue
            slug = member.get("slug")
            if not slug:
                continue
            if slug not in by_slug:
                order.append(slug)
                by_slug[slug] = member
            else:
                by_slug[slug] = {**by_slug[slug], **member}
        merged["connectors"] = [by_slug[slug] for slug in order]
    return merged


def _extra_paths() -> list[Path]:
    raw = os.environ.get(EXTRA_ENV, "")
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def load_catalog() -> list[dict[str, Any]]:
    """Bundled catalog entries with every contributed file merged on top.

    Raises whatever reading the bundled file raises — a broken bundled catalog
    is a build defect, and callers already decide how loud that should be. A
    contributed file that is missing or malformed is logged and skipped: an
    edition's bad JSON must not take the whole directory down.
    """
    catalog: list[dict[str, Any]] = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    index = {
        entry.get("slug"): position
        for position, entry in enumerate(catalog)
        if isinstance(entry, dict) and entry.get("slug")
    }

    for path in _extra_paths():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("connector catalog extra %s ignored: %s", path, exc)
            continue
        if not isinstance(entries, list):
            logger.warning("connector catalog extra %s ignored: not a list", path)
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("slug"):
                logger.warning("connector catalog extra %s: skipping entry without slug", path)
                continue
            slug = entry["slug"]
            position = index.get(slug)
            if position is None:
                index[slug] = len(catalog)
                catalog.append(entry)
            else:
                catalog[position] = _merge_entry(catalog[position], entry)
        logger.info("connector catalog extended from %s", path)

    return catalog


__all__ = ["CATALOG_FILE", "EXTRA_ENV", "load_catalog"]
