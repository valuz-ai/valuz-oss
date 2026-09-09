"""File-tree walk: truncation is visible, and ``path`` cannot leave the root.

Two properties this module has to keep:

1. **A directory that was not listed is not an empty one.** Depth-limited
   listings used to emit ``children: []``, which the wire format drops entirely
   — so a folder with a whole subtree under it rendered exactly like a folder
   with nothing in it, and clients silently lost everything below the limit.
   ``truncated`` is what lets a client draw the difference and ask for the rest.
   Every directory the walk declines to enter says so, whether it ran out of
   depth or out of descent budget.
2. **``path`` is a relative path under the listing root.** It is caller-supplied
   and reaches the filesystem, so traversal and absolute paths must be rejected
   — including via a symlink pointing out of the tree.
"""

from __future__ import annotations

import pytest

from valuz_agent.modules.projects.service import (
    _node_to_dict,
    _resolve_listing_dir,
    _walk_dir,
    _WalkBudget,
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


def test_every_unlisted_directory_says_so(tree):
    """``truncated`` means "not in this listing", not "known to have content".

    Deciding which boundary directories are really empty would cost one
    directory open apiece, at the widest level of the walk and on the mount
    where a metadata op is a network round trip. An empty one costs the user a
    click instead.
    """
    nodes = _by_name(_walk_dir(tree, depth=0, include_hidden=False))
    assert nodes["a"].truncated is True
    assert nodes["empty"].truncated is True
    # Files are never truncated — the flag is a directory concept.
    assert nodes["top.txt"].truncated is False


def test_descent_budget_stops_the_walk_and_marks_what_it_skipped(tmp_path):
    """A shallow tree can still fan out; the budget bounds that, visibly.

    The walk runs on a thread ``asyncio.to_thread`` cannot cancel, so an
    unbounded one outlives the request that asked for it.
    """
    for i in range(5):
        (tmp_path / f"d{i}" / "inner").mkdir(parents=True)
    nodes = _by_name(_walk_dir(tmp_path, depth=3, include_hidden=False, budget=_WalkBudget(2)))

    # The budget counts every descent anywhere in the walk, not just top-level
    # ones: ``d0`` and ``d0/inner`` spend both, so the four siblings after it
    # are refused.
    assert nodes["d0"].truncated is False
    assert [n.name for n in nodes.values() if n.truncated] == ["d1", "d2", "d3", "d4"]
    # Refused, not dropped — every sibling is still listed and reachable.
    assert len(nodes) == 5
    # ``d0/inner`` was entered and really is empty, so it says so.
    inner = nodes["d0"].children[0]
    assert (inner.name, inner.truncated, inner.children) == ("inner", False, [])


def test_truncated_survives_serialization(tree):
    nodes = _by_name(_walk_dir(tree, depth=1, include_hidden=False))
    b = _node_to_dict(_by_name(nodes["a"].children)["b"])
    a = _node_to_dict(nodes["a"])
    assert b["truncated"] is True
    # Absent, not ``False``: existing clients read a missing key as "listed in
    # full", which is what an untruncated node means.
    assert "truncated" not in a


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


def test_pptx_previews_as_a_presentation():
    """A deck the agent produced is the deliverable, so it must be viewable.

    Before this it fell to ``unsupported``, which on a cloud deployment left
    the file with no way out at all — the unsupported view's only exit is
    "open locally", and that is false for a remote file.
    """
    from valuz_agent.modules.projects.service import _preview_kind

    assert _preview_kind("deck.pptx", None) == "presentation"
    # By mime too: an upload can arrive with no useful extension.
    assert (
        _preview_kind(
            "deck",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        == "presentation"
    )


def test_legacy_ppt_stays_unsupported():
    """``.ppt`` is a different, binary format the renderer cannot open.

    Claiming otherwise would trade "no preview" for "broken preview"; it stays
    unsupported, and therefore downloadable.
    """
    from valuz_agent.modules.projects.service import _preview_kind

    assert _preview_kind("deck.ppt", None) == "unsupported"
