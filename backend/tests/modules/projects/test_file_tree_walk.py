"""File-tree walk: truncation is visible, and ``path`` cannot leave the root.

Two properties this module has to keep:

1. **A cut-off directory is not an empty one.** Depth-limited listings used to
   emit ``children: []``, which the wire format drops entirely — so a folder
   with a whole subtree under it rendered exactly like a folder with nothing in
   it, and clients silently lost everything below the limit. ``truncated``
   is what lets a client draw the difference and ask for the rest.
2. **``path`` is a relative path under the listing root.** It is caller-supplied
   and reaches the filesystem, so traversal and absolute paths must be rejected
   — including via a symlink pointing out of the tree.
"""

from __future__ import annotations

import pytest

from valuz_agent.modules.projects.service import (
    _has_visible_entries,
    _node_to_dict,
    _resolve_listing_dir,
    _walk_dir,
)


@pytest.fixture
def tree(tmp_path):
    """``root/a/b/c/deep.txt`` plus an empty ``root/empty`` and a top file."""
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c" / "deep.txt").write_text("deep")
    (tmp_path / "empty").mkdir()
    (tmp_path / "top.txt").write_text("top")
    return tmp_path


def _by_name(nodes):
    return {n.name: n for n in nodes}


def test_cut_off_directory_is_marked_truncated(tree):
    # depth=1 lists root's children and their children, stopping at ``b``.
    nodes = _by_name(_walk_dir(tree, depth=1, include_hidden=False))
    b = _by_name(nodes["a"].children)["b"]
    assert b.children == []
    assert b.truncated is True


def test_genuinely_empty_directory_is_not_truncated(tree):
    nodes = _by_name(_walk_dir(tree, depth=0, include_hidden=False))
    assert nodes["empty"].truncated is False
    # ``a`` is cut off at the same depth, so the flag is what separates them.
    assert nodes["a"].truncated is True


def test_directory_of_only_hidden_entries_reads_as_empty(tmp_path):
    """The walk and the emptiness probe must agree on what counts as listed.

    A folder holding nothing but ``node_modules`` shows no children, so calling
    it truncated would offer an expansion that comes back empty.
    """
    (tmp_path / "vendored" / "node_modules").mkdir(parents=True)
    nodes = _by_name(_walk_dir(tmp_path, depth=0, include_hidden=False))
    assert nodes["vendored"].truncated is False
    assert _has_visible_entries(tmp_path / "vendored", include_hidden=True) is True


def test_truncated_survives_serialization(tree):
    nodes = _walk_dir(tree, depth=0, include_hidden=False)
    payload = _by_name(nodes)
    a = _node_to_dict(payload["a"])
    empty = _node_to_dict(payload["empty"])
    assert a["truncated"] is True
    # Absent, not ``False``: existing clients read a missing key as "listed in
    # full", which is what an untruncated node means.
    assert "truncated" not in empty


def test_full_walk_marks_nothing_truncated(tree):
    nodes = _by_name(_walk_dir(tree, depth=8, include_hidden=False))
    b = _by_name(nodes["a"].children)["b"]
    assert b.truncated is False
    assert _by_name(b.children)["c"].children[0].name == "deep.txt"


@pytest.mark.parametrize("path", ["", None, ".", "./"])
def test_empty_path_resolves_to_the_root(tree, path):
    assert _resolve_listing_dir(tree, path) == tree


def test_relative_path_resolves_under_the_root(tree):
    assert _resolve_listing_dir(tree, "a/b") == (tree / "a" / "b").resolve()


@pytest.mark.parametrize("path", ["/etc", "../outside", "a/../../outside"])
def test_escaping_paths_are_rejected(tree, path):
    with pytest.raises(ValueError):
        _resolve_listing_dir(tree, path)


def test_symlink_out_of_the_root_is_rejected(tree, tmp_path_factory):
    """Resolution happens before the prefix check, so a link cannot smuggle."""
    outside = tmp_path_factory.mktemp("outside")
    (tree / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        _resolve_listing_dir(tree, "escape")
