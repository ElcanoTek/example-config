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


def test_a_root_swapped_for_a_symlink_after_resolution_is_refused(tree, monkeypatch, tmp_path):
    """The root's parent may be sandbox-writable: swapping the root itself must not
    move every descriptor-relative open outside the tree."""
    root = tree["root"]
    (tree["outside"] / "sub").mkdir()
    (tree["outside"] / "sub" / "a.txt").write_bytes(b"secret")
    real_resolve = safe_open._resolve

    def resolve_then_swap_root(path, roots, label):
        result = real_resolve(path, roots, label)
        root.rename(tmp_path / "root.orig")
        root.symlink_to(tree["outside"], target_is_directory=True)
        return result

    monkeypatch.setattr(safe_open, "_resolve", resolve_then_swap_root)
    with pytest.raises(safe_open.UnsafePathError, match="changed|replaced"):
        safe_open.read_bytes(str(root / "sub" / "a.txt"), [str(root)])


def test_a_root_replaced_by_another_directory_is_refused(tree, monkeypatch, tmp_path):
    root = tree["root"]
    real_resolve = safe_open._resolve

    def resolve_then_replace_root(path, roots, label):
        result = real_resolve(path, roots, label)
        root.rename(tmp_path / "root.orig")
        root.mkdir()
        (root / "sub").mkdir()
        (root / "sub" / "a.txt").write_bytes(b"other")
        return result

    monkeypatch.setattr(safe_open, "_resolve", resolve_then_replace_root)
    with pytest.raises(safe_open.UnsafePathError, match="replaced"):
        safe_open.read_bytes(str(root / "sub" / "a.txt"), [str(root)])


def test_a_failed_write_removes_only_the_file_it_created(tree, monkeypatch):
    import errno as _errno

    real_write = os.write

    def no_space(fd, data):
        real_write(fd, bytes(data)[:2])
        raise OSError(_errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(safe_open.os, "write", no_space)
    with pytest.raises(OSError):
        safe_open.write_new_or_truncate(str(tree["root"] / "new.txt"), [str(tree["root"])], b"hello")
    assert not (tree["root"] / "new.txt").exists()


def test_remove_created_never_follows_a_swapped_directory(tree, tmp_path):
    seen = []
    path = tree["root"] / "sub" / "a2.txt"
    safe_open.write_new_or_truncate(str(path), [str(tree["root"])], b"x", on_created=seen.append)
    (tree["outside"] / "a2.txt").write_bytes(b"host file")
    (tree["root"] / "sub").rename(tmp_path / "sub.orig")
    (tree["root"] / "sub").symlink_to(tree["outside"], target_is_directory=True)
    assert safe_open.remove_created(str(path), [str(tree["root"])], seen[0]) is False
    assert (tree["outside"] / "a2.txt").read_bytes() == b"host file"
    # Restored: the file it created is removed.
    (tree["root"] / "sub").unlink()
    (tmp_path / "sub.orig").rename(tree["root"] / "sub")
    assert safe_open.remove_created(str(path), [str(tree["root"])], seen[0]) is True
    assert not path.exists()


def test_a_root_swapped_before_resolve_is_not_adopted(tree, tmp_path):
    """Once a configured root has been used, a later swap of that root for a
    symlink (before _resolve even runs) is refused, never re-resolved."""
    root = tree["root"]
    assert safe_open.read_bytes(str(root / "sub" / "a.txt"), [str(root)])[0] == b"inside"  # first use pins it
    (tree["outside"] / "sub").mkdir()
    (tree["outside"] / "sub" / "a.txt").write_bytes(b"secret")
    root.rename(tmp_path / "root.orig")
    root.symlink_to(tree["outside"], target_is_directory=True)
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.read_bytes(str(root / "sub" / "a.txt"), [str(root)])


def test_pins_are_keyed_by_the_configured_path_not_its_resolution(tmp_path):
    """Callers pass configured paths: a root swapped between two calls is refused,
    because the second call reuses the first call's pin instead of resolving
    the (now symlinked) configured path into a new root."""
    configured = tmp_path / "configured-root"
    configured.mkdir()
    (configured / "a.txt").write_bytes(b"inside")
    assert safe_open.read_bytes(str(configured / "a.txt"), [str(configured)])[0] == b"inside"
    target = tmp_path / "attacker"
    target.mkdir()
    (target / "a.txt").write_bytes(b"secret")
    configured.rename(tmp_path / "configured.orig")
    configured.symlink_to(target, target_is_directory=True)
    # The pin still names the directory first configured, never the attacker's target.
    assert safe_open.pin_roots([str(configured)])[0] == str(configured)
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.read_bytes(str(configured / "a.txt"), [str(configured)])


def test_a_root_already_a_symlink_at_first_lazy_pin_is_refused(tree, tmp_path):
    """A renameable workspace root swapped for a symlink BEFORE its first use
    (nothing pinned yet) is refused at call time, never resolved to its target."""
    workspace = tmp_path / "workspace-first-use"
    workspace.symlink_to(tree["outside"], target_is_directory=True)
    assert safe_open.pin_roots([str(workspace)]) == []
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.read_bytes(str(workspace / "a.txt"), [str(workspace)])
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.write_new_or_truncate(str(workspace / "new.txt"), [str(workspace)], b"x")
    assert not (tree["outside"] / "new.txt").exists()


@pytest.mark.parametrize("suffix", ["/", "//", "/."])
def test_a_root_configured_with_a_trailing_separator_swapped_before_first_use_is_refused(tree, tmp_path, suffix):
    """os.open("root/", O_NOFOLLOW) follows a final symlink on Linux: the root's
    last component must be opened relative to its parent, trailing "/" or not."""
    workspace = tmp_path / f"workspace-slash-{len(suffix)}-{suffix.endswith('.')}"
    workspace.symlink_to(tree["outside"], target_is_directory=True)
    configured = str(workspace) + suffix
    assert safe_open.pin_roots([configured]) == []
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.read_bytes(str(workspace / "a.txt"), [configured])
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.write_new_or_truncate(str(workspace / "new.txt"), [configured], b"x")
    assert not (tree["outside"] / "new.txt").exists()


def test_a_real_root_with_a_trailing_separator_still_works(tree):
    configured = str(tree["root"]) + "/"
    assert safe_open.pin_roots([configured]) == [os.path.realpath(tree["root"])]
    data, _ = safe_open.read_bytes(str(tree["root"] / "sub" / "a.txt"), [configured])
    assert data == b"inside"


def test_a_symlinked_root_pinned_at_startup_still_works(tree, tmp_path):
    """Operator config such as macOS /tmp: pinned at server start, it is trusted."""
    operator_tmp = tmp_path / "operator-tmp"
    operator_tmp.symlink_to(tree["root"], target_is_directory=True)
    assert safe_open.pin_at_startup([str(operator_tmp)]) == [os.path.realpath(tree["root"])]
    data, _ = safe_open.read_bytes(str(operator_tmp / "sub" / "a.txt"), [str(operator_tmp)])
    assert data == b"inside"


def test_servers_pin_their_roots_at_import():
    import importlib

    import ses_sender

    # This bundle's only safe_open server; it pins its roots at server start.
    for module in (ses_sender,):
        source = importlib.util.find_spec(module.__name__).origin
        with open(source) as handle:
            assert "safe_open.pin_at_startup(" in handle.read(), module.__name__


def test_a_transient_fstat_failure_after_create_removes_the_file_by_its_identity(tree, monkeypatch):
    """fstat fails once on the new leaf: the retry on the same descriptor proves
    its identity, and only then is the entry removed."""
    import stat as _stat

    real_fstat = os.fstat
    hit = []

    def failing_once(fd):
        st = real_fstat(fd)
        if _stat.S_ISREG(st.st_mode) and not hit:  # the freshly created leaf, first call only
            hit.append(fd)
            raise OSError(5, "I/O error")
        return st

    monkeypatch.setattr(safe_open.os, "fstat", failing_once)
    with pytest.raises(OSError, match="I/O error"):
        safe_open.write_new_or_truncate(str(tree["root"] / "fresh.txt"), [str(tree["root"])], b"x")
    monkeypatch.setattr(safe_open.os, "fstat", real_fstat)
    assert hit, "the leaf fstat was never reached"
    assert not (tree["root"] / "fresh.txt").exists()


def test_a_persistent_fstat_failure_never_removes_an_entry_by_name(tree, monkeypatch):
    """Without the descriptor's identity the entry is left alone: a file another
    writer swapped in under the same name must never be deleted."""
    import stat as _stat

    real_fstat = os.fstat
    target = tree["root"] / "fresh.txt"

    def failing_fstat(fd):
        st = real_fstat(fd)
        if _stat.S_ISREG(st.st_mode):
            # Another writer replaces the entry between our create and cleanup.
            target.unlink()
            target.write_bytes(b"")
            raise OSError(5, "I/O error")
        return st

    monkeypatch.setattr(safe_open.os, "fstat", failing_fstat)
    with pytest.raises(OSError, match="I/O error"):
        safe_open.write_new_or_truncate(str(target), [str(tree["root"])], b"x")
    monkeypatch.setattr(safe_open.os, "fstat", real_fstat)
    assert target.exists()  # the swapped-in file survives


def test_an_ancestor_swapped_for_a_symlink_after_startup_is_never_followed(tree, tmp_path):
    """A root absent at startup is reached from its ancestor pinned THEN: swapping
    an intermediate directory for a symlink later cannot authorize another tree."""
    workspace = tmp_path / "ws-anc"
    workspace.mkdir()
    root = workspace / "run" / "out"  # does not exist yet
    assert safe_open.pin_at_startup([str(root)]) == []

    # Attacker: /ws-anc/run becomes a symlink to a tree that has an "out" dir.
    evil = tmp_path / "evil"
    (evil / "out").mkdir(parents=True)
    (evil / "out" / "a.txt").write_bytes(b"secret")
    (workspace / "run").symlink_to(evil, target_is_directory=True)

    assert safe_open.pin_roots([str(root)]) == []
    with pytest.raises(safe_open.UnsafePathError):
        safe_open.read_bytes(str(root / "a.txt"), [str(root)])


def test_a_pinned_ancestor_replaced_after_startup_is_refused(tree, tmp_path):
    workspace = tmp_path / "ws-repl"
    workspace.mkdir()
    root = workspace / "out"
    safe_open.pin_at_startup([str(root)])
    workspace.rename(tmp_path / "ws-repl.orig")
    elsewhere = tmp_path / "elsewhere-repl"
    (elsewhere / "out").mkdir(parents=True)
    workspace.symlink_to(elsewhere, target_is_directory=True)
    assert safe_open.pin_roots([str(root)]) == []


def test_a_root_created_after_startup_under_its_pinned_ancestor_works(tree, tmp_path):
    workspace = tmp_path / "ws-late"
    workspace.mkdir()
    root = workspace / "run" / "out"
    safe_open.pin_at_startup([str(root)])
    root.mkdir(parents=True)
    (root / "a.txt").write_bytes(b"ok")
    data, _ = safe_open.read_bytes(str(root / "a.txt"), [str(root)])
    assert data == b"ok"


def test_a_root_never_seen_at_startup_refuses_a_symlinked_ancestor(tree, tmp_path):
    link = tmp_path / "anc-link"
    link.symlink_to(tree["root"], target_is_directory=True)
    assert safe_open.pin_roots([str(link / "sub")]) == []
