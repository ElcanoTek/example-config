"""utils.safe_open: one confined open per validated path, no symlink following."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from utils import safe_open


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_bytes(b"secret")
    return {"root": root, "outside": outside}


def test_reads_a_regular_file_under_a_root(tree):
    data, st = safe_open.read_bytes(str(tree["root"] / "sub" / "a.txt"), [str(tree["root"])])
    assert data == b"inside"
    assert st.st_size == 6


def test_refuses_a_path_outside_every_root(tree):
    with pytest.raises(safe_open.UnsafePathError, match="outside the allowed"):
        safe_open.read_bytes(str(tree["outside"] / "a.txt"), [str(tree["root"])])


def test_refuses_a_symlink_that_escapes(tree):
    (tree["root"] / "link.txt").symlink_to(tree["outside"] / "a.txt")
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.read_bytes(str(tree["root"] / "link.txt"), [str(tree["root"])])


def test_follows_a_symlink_that_stays_inside(tree):
    (tree["root"] / "link.txt").symlink_to(tree["root"] / "sub" / "a.txt")
    data, _ = safe_open.read_bytes(str(tree["root"] / "link.txt"), [str(tree["root"])])
    assert data == b"inside"


def test_root_prefix_is_not_a_sibling(tree, tmp_path):
    sibling = tmp_path / "root-evil"
    sibling.mkdir()
    (sibling / "x").write_bytes(b"x")
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.read_bytes(str(sibling / "x"), [str(tree["root"])])


def test_dir_swapped_between_resolve_and_walk_is_refused(tree, monkeypatch):
    """Simulate the race: realpath sees a real directory, the walk then meets a symlink."""
    sub = tree["root"] / "sub"
    real_resolve = safe_open._resolve

    def resolve_then_swap(path, roots, label):
        result = real_resolve(path, roots, label)
        sub.rename(tree["root"] / "sub.orig")
        sub.symlink_to(tree["outside"], target_is_directory=True)
        return result

    monkeypatch.setattr(safe_open, "_resolve", resolve_then_swap)
    with pytest.raises(safe_open.UnsafePathError, match="changed"):
        safe_open.read_bytes(str(sub / "a.txt"), [str(tree["root"])])


def test_leaf_swapped_between_resolve_and_open_is_refused(tree, monkeypatch):
    leaf = tree["root"] / "sub" / "a.txt"
    real_resolve = safe_open._resolve

    def resolve_then_swap(path, roots, label):
        result = real_resolve(path, roots, label)
        leaf.unlink()
        leaf.symlink_to(tree["outside"] / "a.txt")
        return result

    monkeypatch.setattr(safe_open, "_resolve", resolve_then_swap)
    with pytest.raises(safe_open.UnsafePathError, match="symlink"):
        safe_open.read_bytes(str(leaf), [str(tree["root"])])


def test_size_cap(tree):
    with pytest.raises(safe_open.FileTooLargeError) as exc:
        safe_open.read_bytes(str(tree["root"] / "sub" / "a.txt"), [str(tree["root"])], max_bytes=3)
    assert exc.value.size == 6


def test_non_regular_files_are_refused(tree):
    with pytest.raises(safe_open.UnsafePathError, match="regular"):
        safe_open.read_bytes(str(tree["root"] / "sub"), [str(tree["root"])])
    fifo = tree["root"] / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(safe_open.UnsafePathError, match="regular"):
        safe_open.read_bytes(str(fifo), [str(tree["root"])])


def test_write_creates_dirs_inside_and_never_through_a_symlink(tree):
    target = tree["root"] / "new" / "deep" / "out.html"
    assert safe_open.write_new_or_truncate(str(target), [str(tree["root"])], b"hi", create_dirs=True) is True
    assert target.read_bytes() == b"hi"
    assert safe_open.write_new_or_truncate(str(target), [str(tree["root"])], b"again") is False
    assert target.read_bytes() == b"again"

    (tree["root"] / "evil").symlink_to(tree["outside"], target_is_directory=True)
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.write_new_or_truncate(str(tree["root"] / "evil" / "a.txt"), [str(tree["root"])], b"pwned")
    assert (tree["outside"] / "a.txt").read_bytes() == b"secret"


def test_exclusive_write_refuses_existing(tree):
    with pytest.raises(FileExistsError):
        safe_open.write_new_or_truncate(str(tree["root"] / "sub" / "a.txt"), [str(tree["root"])], b"x", exclusive=True)


def test_rewrite_same_file_skips_a_replaced_inode(tree):
    path = tree["root"] / "sub" / "a.txt"
    _, st = safe_open.read_bytes(str(path), [str(tree["root"])])
    path.rename(path.with_name("kept.txt"))  # keep the old inode alive so it is not reused
    path.write_bytes(b"replacement")
    assert safe_open.rewrite_same_file(str(path), [str(tree["root"])], st, b"new") is False
    assert path.read_bytes() == b"replacement"
    _, st2 = safe_open.read_bytes(str(path), [str(tree["root"])])
    assert safe_open.rewrite_same_file(str(path), [str(tree["root"])], st2, b"new") is True
    assert path.read_bytes() == b"new"
