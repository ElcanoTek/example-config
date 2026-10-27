"""Open a path that was checked against an allowlist exactly once, safely.

Host-side servers (outbound email, Fast.io upload, SSP list files) read and
write files that a model names and that sandboxed code can modify: the run
workspace is mounted into the sandbox. Checking a path with ``realpath`` and
then re-opening it BY NAME is a race: between the check and the open, code in
the sandbox can replace the file (or any directory above it) with a symlink to
an arbitrary host file, and the server then mails, uploads or overwrites that
file instead.

This module closes the race by never trusting a name twice:

1. The path is resolved once with ``realpath`` and must sit under one of the
   caller's allowed roots (exact match or ``root + os.sep`` prefix).
2. The containing root is opened as a directory, and every component below it
   is opened relative to its parent descriptor with ``O_NOFOLLOW |
   O_DIRECTORY`` (an openat-style walk). A directory swapped for a symlink
   after step 1 makes the walk fail instead of leaving the root.
3. The leaf is opened with ``O_NOFOLLOW`` relative to its directory
   descriptor, ``fstat``-ed (a regular file, within the size cap), and the
   caller reads or writes through THAT descriptor only.

A symlink that already pointed inside the root when the path was checked
still works (step 1 resolved it, and the walk opens its real target); one that
appears afterwards is refused.

Stdlib only and free of any client or server knowledge, so client bundles can
port it unchanged next to the servers that import it.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import stat
from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
# A FIFO planted at the leaf would block a plain O_RDONLY open forever; with
# O_NONBLOCK the open returns and fstat refuses it as not a regular file.
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class UnsafePathError(ValueError):
    """The path left the allowed roots, or changed (symlink swap) while being opened."""


class FileTooLargeError(ValueError):
    """The file is larger than the caller's byte cap."""

    def __init__(self, message: str, size: int, limit: int) -> None:
        super().__init__(message)
        self.size = size
        self.limit = limit


def is_within(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` or below it (no ``/app`` vs ``/apple`` confusion)."""
    root = root.rstrip(os.sep) or os.sep
    if root == os.sep:
        return path.startswith(os.sep)
    return path == root or path.startswith(root + os.sep)


def containing_root(real: str, roots: Iterable[str]) -> str | None:
    """The deepest (longest) pinned root that contains ``real``, or None.

    Every root is used as pinned (:func:`pin_roots`): a configured root is
    resolved the FIRST time this process uses it and that resolution, with its
    (device, inode), is kept; it is never realpath()ed again at call time, so a
    root swapped for a symlink later is refused instead of adopted."""
    best: str | None = None
    for root in pin_roots(roots):
        if is_within(real, root) and (best is None or len(root) > len(best)):
            best = root
    return best


def _open_dir_last_nofollow(path: str) -> int:
    """Open directory ``path`` without following a symlink in its LAST component.

    ``os.open("root/", O_NOFOLLOW)`` follows a final symlink on Linux (the
    trailing separator makes the kernel resolve it), so the path is never opened
    as one string: trailing separators are stripped and the last component is
    opened with O_NOFOLLOW|O_DIRECTORY relative to its parent's descriptor. The
    parent chain is configuration, opened normally."""
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    stripped = str(path)
    while True:  # "root/", "root//", "root/." and "root/./" all name "root"
        trimmed = stripped.rstrip(os.sep)
        if trimmed.endswith(os.sep + ".") or trimmed == ".":
            trimmed = trimmed[:-1]
        if trimmed == stripped:
            break
        stripped = trimmed
    if not stripped:
        return os.open(os.sep if str(path).startswith(os.sep) else os.curdir, flags)
    parent, name = os.path.split(stripped)
    if name == "..":
        # "link/.." is the symlink TARGET's parent: not a pinnable literal root.
        raise OSError(errno.ELOOP, "root ends in '..'", str(path))
    parent_fd = os.open(parent or os.curdir, os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC)
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _trusted_system_symlink(dir_fd: int, name: str) -> str | None:
    """The target of ``name`` (in directory ``dir_fd``) when it is a TRUSTED
    system symlink, else None.

    Trusted means the link itself is owned by root AND its directory is
    root-owned and writable by nobody else (no group / other write bit): only
    root could have planted it (macOS /tmp -> private/tmp, /var -> private/var).
    Any other symlink, e.g. one in a sandbox-writable workspace, is refused.
    Callers only ask this for links directly under "/" (see walk_trusted): a
    process running as root (tests, some containers) owns every link it makes,
    so ownership alone cannot vouch for a link deeper in the tree."""
    try:
        link = os.lstat(name, dir_fd=dir_fd)
        if not stat.S_ISLNK(link.st_mode) or link.st_uid != 0:
            return None
        parent = os.fstat(dir_fd)
        if parent.st_uid != 0 or parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return None
        return os.readlink(name, dir_fd=dir_fd)
    except (OSError, TypeError, NotImplementedError):
        return None


def walk_trusted(path: str, *, _top: str = os.sep) -> int:
    """Open directory ``path`` from "/" one component at a time with
    O_NOFOLLOW|O_DIRECTORY. A symlink component is followed ONLY when it sits
    directly under "/" and is a trusted system symlink
    (:func:`_trusted_system_symlink`); any other symlink fails the walk (ELOOP). A ".." component is refused: the walk only descends."""
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    pending = [p for p in os.path.abspath(str(path)).split(os.sep) if p not in ("", ".")]
    done: list[str] = []
    followed = 0
    fd = os.open(_top, flags)  # _top: the filesystem root ("/"; a test seam)
    try:
        while pending:
            part = pending.pop(0)
            if part == "..":
                raise OSError(errno.ELOOP, "'..' in a root path", str(path))
            try:
                child = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                if exc.errno not in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
                    raise
                # Only a top-level system link ("/tmp", "/var", "/home") may be
                # followed; anything deeper is refused whoever owns it.
                target = _trusted_system_symlink(fd, part) if not done else None
                followed += 1
                if target is None or followed > 8:
                    raise
                # Restart from "/" on the link's target (relative to its directory).
                base = target if os.path.isabs(target) else os.path.join(os.sep, *done, target)
                pending = [p for p in os.path.normpath(base).split(os.sep) if p not in ("", ".")] + pending
                done = []
                os.close(fd)
                fd = os.open(_top, flags)
                continue
            os.close(fd)
            fd = child
            done.append(part)
        return fd
    except BaseException:
        os.close(fd)
        raise


# Kept for callers in this module.
_walk_nofollow = walk_trusted


# Roots absent at startup: their nearest EXISTING ancestor is resolved and
# pinned then (operator config, trusted only at startup), with the components
# still to come. Lazily, the root is reached from that ancestor's pinned
# identity with no-follow opens, so no ancestor swapped for a symlink after
# startup is ever followed.
_PENDING_ROOTS: dict[str, tuple[str, tuple[int, int], list[str]]] = {}


def _record_pending_root(root: str) -> None:
    """Pin the nearest EXISTING ancestor of an absent root, found by a no-follow
    walk from "/" (walk_trusted): an ancestor that is a symlink, other than a
    trusted top-level system link, records nothing, so the root is later
    refused instead of being pinned as the symlink's target."""
    parts = [p for p in os.path.abspath(str(root)).split(os.sep) if p not in ("", ".")]
    if ".." in parts:
        return
    for depth in range(len(parts), -1, -1):
        prefix = os.sep + os.sep.join(parts[:depth])
        try:
            fd = walk_trusted(prefix)
        except FileNotFoundError:
            continue
        except OSError:
            return  # a symlinked (or non-directory) ancestor: never pinned
        try:
            st = os.fstat(fd)
        finally:
            os.close(fd)
        anc_real = os.path.realpath(prefix)
        try:
            check = os.stat(anc_real)
        except OSError:
            return
        if (check.st_dev, check.st_ino) != (st.st_dev, st.st_ino):
            return  # it changed while being pinned: do not guess
        _PENDING_ROOTS[root] = (anc_real, (st.st_dev, st.st_ino), parts[depth:])
        return


def _open_lazy_root(root: str) -> int:
    """Open a root first used after startup, following no symlink on its path."""
    pending = _PENDING_ROOTS.get(root)
    if pending is None:
        return _walk_nofollow(root)
    anc_real, anc_ident, rest = pending
    fd = _walk_nofollow(anc_real)
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != anc_ident:
            raise OSError(errno.ELOOP, "the ancestor pinned at startup was replaced", anc_real)
        flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
        for part in rest:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


# Configured root (as given) -> its pinned resolution, for the process lifetime.
_ROOT_PINS: dict[str, _Root] = {}


def _pin_one(root: str, *, trust_symlink: bool = False) -> _Root | None:
    """Pin one CONFIGURED root, keyed by the literal string the caller configured.

    The literal path is opened once with O_NOFOLLOW|O_DIRECTORY and its
    (device, inode) recorded with its realpath; later calls reuse that pin and
    never resolve the literal again, so swapping the configured path for a
    symlink later changes nothing. Callers must pass the configured paths, not
    realpath()ed ones: a resolved string would be a new cache key after a swap.

    A configured root whose own last component is a symlink is resolved ONLY
    with ``trust_symlink`` (:func:`pin_at_startup`, before any sandbox code can
    act: operator config such as macOS /tmp). Pinned lazily at call time, such a
    root is REFUSED: the symlink may be an attacker's swap of a renameable
    workspace root that happened before its first use."""
    if isinstance(root, _Root) and root.ident is not None:
        return root
    cached = _ROOT_PINS.get(root)
    if cached is not None:
        return cached
    try:
        # At startup only the root's own last component is opened no-follow
        # (operator config may sit under a symlinked ancestor); afterwards EVERY
        # component is (_open_lazy_root).
        fd = _open_dir_last_nofollow(root) if trust_symlink else _open_lazy_root(root)
    except OSError as exc:
        if exc.errno not in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            return None  # not there yet: nothing to pin, nothing under it to open
        if not trust_symlink:
            logger.warning("allowed root %s is a symlink and was not pinned at startup: refused", root)
            return None
        reason = _untrusted_root_link_reason(root)
        if reason is not None:
            logger.warning("allowed root %s is a symlink that is not trusted (%s): refused", root, reason)
            return None
        fd = None  # an operator-configured symlink root, pinned at startup: resolved once, now
    try:
        real = _Root(os.path.realpath(root))
        st = os.fstat(fd) if fd is not None else os.stat(real)
        if not stat.S_ISDIR(st.st_mode):
            return None
        if fd is not None:
            check = os.stat(real)
            if (check.st_dev, check.st_ino) != (st.st_dev, st.st_ino):
                return None  # it moved while being pinned: do not guess
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)
    real.ident = (st.st_dev, st.st_ino)
    _ROOT_PINS[root] = real
    return real


def _untrusted_root_link_reason(root: str) -> str | None:
    """Why a root whose own final component is a symlink must NOT be followed
    at startup, or None when it may be.

    Followed only when the link is owned by root or by this process's user AND
    its directory is owned by root or this user and writable by neither group
    nor others: then only the operator (or root) could have placed it. A stale
    link left in a shared or sandbox-writable workspace is refused."""
    path = str(root).rstrip(os.sep) or os.sep
    euid = os.geteuid() if hasattr(os, "geteuid") else None
    trusted_uids = {0} if euid is None else {0, euid}
    try:
        link = os.lstat(path)
        parent = os.stat(os.path.dirname(os.path.abspath(path)) or os.sep)
    except OSError as exc:
        return f"cannot inspect it: {exc}"
    if link.st_uid not in trusted_uids:
        return f"the link is owned by uid {link.st_uid}"
    if parent.st_uid not in trusted_uids:
        return f"its directory is owned by uid {parent.st_uid}"
    if parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "its directory is writable by group or others"
    return None


def pin_at_startup(roots: Iterable[str]) -> list[str]:
    """Pin the configured roots when a server STARTS, before any tool call (and
    so before sandbox code can swap anything): the one moment a root that is
    itself an operator-configured symlink (macOS /tmp) is resolved and trusted,
    and only when the link and its directory pass _untrusted_root_link_reason.
    Roots that do not exist yet are left for lazy pinning, which refuses a
    symlinked root. Never raises: a server must start even if a root is odd."""
    pinned: list[str] = []
    for root in roots:
        if not root:
            continue
        try:
            one = _pin_one(root, trust_symlink=True)
            if one is None and not os.path.lexists(root):
                _record_pending_root(root)
        except Exception:  # noqa: BLE001 - startup must not fail on one root
            one = None
        if one is not None and one not in pinned:
            pinned.append(one)
    return pinned


def pin_roots(roots: Iterable[str]) -> list[str]:
    """The allowed roots, each resolved ONCE per process and pinned by (device, inode).

    Validation and the read/write that follows then check against the same
    identities, so a root swapped for a symlink at any point after its first use
    is refused (it is opened O_NOFOLLOW and its dev/ino must match) instead of
    having its new target resolved and accepted. Roots that do not exist yet are
    left out (and pinned when they first exist)."""
    pinned: list[str] = []
    for root in roots:
        if not root:
            continue
        one = _pin_one(root)
        if one is not None and one not in pinned:
            pinned.append(one)
    return pinned


class _Root(str):
    """A resolved root path plus the (device, inode) it had when it was resolved."""

    ident: tuple[int, int] | None = None


def _resolve(path: str, roots: Iterable[str], label: str) -> tuple[str, list[str]]:
    """(containing root, components below it) for the realpath of ``path``."""
    if not path or "\x00" in str(path):
        raise UnsafePathError(f"{label} path is empty or invalid")
    real = os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))
    roots = list(roots)
    root = containing_root(real, roots)
    if root is None:
        raise UnsafePathError(
            f"SECURITY: {label} path is outside the allowed directories: {path} "
            f"(allowed: {', '.join(r for r in roots if r) or '(none configured)'})"
        )
    rel = os.path.relpath(real, root)
    parts = [] if rel == "." else rel.split(os.sep)
    if any(part in ("", ".", "..") for part in parts):
        raise UnsafePathError(f"SECURITY: {label} path is not a plain path below an allowed directory: {path}")
    return root, parts


def _walk_dirs(root: str, parts: list[str], *, create: bool, label: str, path: str) -> int:
    """Open ``root/parts...`` as a directory descriptor without following any
    symlink below ``root``; with ``create`` missing components are made (like ``os.makedirs``)."""
    # The root itself is opened without following a symlink and must be the very
    # directory that was resolved: if its parent is writable by the sandbox, the
    # root could otherwise be swapped for a symlink and every descriptor-relative
    # open below would start outside the tree.
    try:
        fd = _open_dir_last_nofollow(root)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise UnsafePathError(f"SECURITY: allowed directory {root} changed after {path} was checked") from exc
        raise
    ident = getattr(root, "ident", None)
    st = os.fstat(fd)
    if ident is not None and (st.st_dev, st.st_ino) != ident:
        os.close(fd)
        raise UnsafePathError(f"SECURITY: allowed directory {root} was replaced after {path} was checked")
    try:
        for part in parts:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o777, dir_fd=fd)  # umask applies, as with os.makedirs
            try:
                child = os.open(part, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=fd)
            except (FileNotFoundError, PermissionError):
                raise
            except OSError as exc:
                # ELOOP (a symlink), ENOTDIR (a file, or a symlink on some
                # platforms): the tree changed after the path was checked.
                raise UnsafePathError(
                    f"SECURITY: {label} path changed or runs through a non-directory: {path}"
                ) from exc
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_dir(path: str, roots: Iterable[str], *, create: bool = False, label: str = "directory") -> int:
    """Open directory ``path`` (confined to ``roots``) and return its descriptor."""
    root, parts = _resolve(path, roots, label)
    return _walk_dirs(root, parts, create=create, label=label, path=str(path))


def makedirs(path: str, roots: Iterable[str], *, label: str = "directory") -> None:
    """``os.makedirs`` confined to ``roots``: never creates anything outside them."""
    os.close(open_dir(path, roots, create=True, label=label))


def open_file(
    path: str,
    roots: Iterable[str],
    flags: int = os.O_RDONLY,
    mode: int = 0o600,
    *,
    create_dirs: bool = False,
    label: str = "file",
) -> int:
    """Open ``path`` once, confined to ``roots``, and return the descriptor.

    ``flags`` are the leaf's open flags (``O_NOFOLLOW`` and ``O_CLOEXEC`` are
    always added). A leaf that is a symlink at open time raises
    :class:`UnsafePathError`; a missing one raises ``FileNotFoundError``.
    """
    root, parts = _resolve(path, roots, label)
    if not parts:
        raise UnsafePathError(f"{label} path names an allowed directory itself, not a file: {path}")
    dir_fd = _walk_dirs(root, parts[:-1], create=create_dirs, label=label, path=str(path))
    try:
        try:
            return os.open(parts[-1], flags | _O_NOFOLLOW | _O_CLOEXEC, mode, dir_fd=dir_fd)
        except OSError as exc:
            # O_NOFOLLOW on a symlink leaf: ELOOP (Linux, macOS), EMLINK (FreeBSD).
            if exc.errno in (errno.ELOOP, errno.EMLINK):
                raise UnsafePathError(
                    f"SECURITY: {label} path changed to a symlink after it was checked: {path}"
                ) from exc
            raise
    finally:
        os.close(dir_fd)


def open_regular(
    path: str,
    roots: Iterable[str],
    *,
    max_bytes: int | None = None,
    label: str = "file",
) -> tuple[int, os.stat_result]:
    """Open an existing regular file for reading: ``(fd, fstat)``.

    Refuses a non-regular file (directory, FIFO, device, socket) and, with
    ``max_bytes``, a file larger than the cap (:class:`FileTooLargeError`).
    The caller owns the descriptor.
    """
    fd = open_file(path, roots, os.O_RDONLY | _O_NONBLOCK, label=label)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafePathError(f"{label} path is not a regular file: {path}")
        if max_bytes is not None and st.st_size > max_bytes:
            raise FileTooLargeError(
                f"{label} is too large: {path} ({st.st_size} bytes, limit {max_bytes})", st.st_size, max_bytes
            )
        if _O_NONBLOCK:
            # Back to blocking reads for the regular file we now know it is.
            os.set_blocking(fd, True)
        return fd, st
    except BaseException:
        os.close(fd)
        raise


def read_fd(fd: int, max_bytes: int | None = None, *, label: str = "file") -> bytes:
    """Read all of ``fd`` (from its current offset), refusing more than ``max_bytes``.

    The file can grow after ``fstat``; the cap is enforced on what is read.
    Does not close ``fd``.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise FileTooLargeError(f"{label} grew past the {max_bytes}-byte limit while reading", total, max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def read_bytes(
    path: str,
    roots: Iterable[str],
    *,
    max_bytes: int | None = None,
    label: str = "file",
) -> tuple[bytes, os.stat_result]:
    """Read a confined regular file through one descriptor: ``(data, fstat)``."""
    fd, st = open_regular(path, roots, max_bytes=max_bytes, label=label)
    try:
        return read_fd(fd, max_bytes, label=label), st
    finally:
        os.close(fd)


def same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def rewrite_same_file(
    path: str,
    roots: Iterable[str],
    expected: os.stat_result,
    data: bytes,
    *,
    label: str = "file",
) -> bool:
    """Overwrite ``path`` with ``data`` only if it is still the file ``expected``
    describes (same device and inode, reached by a confined walk). Returns
    False, writing nothing, when the file was replaced in between."""
    fd = open_file(path, roots, os.O_WRONLY | _O_NONBLOCK, label=label)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not same_file(st, expected):
            return False
        os.set_blocking(fd, True)
        os.ftruncate(fd, 0)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        return True
    finally:
        os.close(fd)


def write_new_or_truncate(
    path: str,
    roots: Iterable[str],
    data: bytes,
    *,
    exclusive: bool = False,
    mode: int = 0o600,
    create_dirs: bool = False,
    label: str = "file",
    on_created: Callable[[tuple[int, int]], None] | None = None,
) -> bool:
    """Create (or, unless ``exclusive``, truncate) a confined regular file and
    write ``data`` through its descriptor. Returns True when the file was
    created by this call.

    The helper owns cleanup of what it creates: if the write fails after the
    file was created (ENOSPC), the file is unlinked relative to its directory
    descriptor, and only while that name is still the inode it created.
    ``on_created((dev, ino))`` runs as soon as the file exists, so a caller can
    later remove it with :func:`remove_created` (e.g. when a sibling write fails)."""
    root, parts = _resolve(path, roots, label)
    if not parts:
        raise UnsafePathError(f"{label} path names an allowed directory itself, not a file: {path}")
    name = parts[-1]
    dir_fd = _walk_dirs(root, parts[:-1], create=create_dirs, label=label, path=str(path))
    try:
        created = True
        try:
            fd = _open_leaf(name, dir_fd, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, label, path)
        except FileExistsError:
            if exclusive:
                raise
            created = False
            fd = _open_leaf(name, dir_fd, os.O_WRONLY | _O_NONBLOCK, mode, label, path)
        ident: tuple[int, int] | None = None
        try:
            st = os.fstat(fd)
            ident = (st.st_dev, st.st_ino)
            if created and on_created is not None:
                on_created(ident)
            if not stat.S_ISREG(st.st_mode):
                raise UnsafePathError(f"{label} path is not a regular file: {path}")
            os.set_blocking(fd, True)
            if not created:
                os.ftruncate(fd, 0)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            return created
        except BaseException:
            if created:
                if ident is None:
                    # fstat itself failed right after the O_EXCL create: retry it
                    # once on the still-open descriptor. A name lookup cannot
                    # prove identity (another writer may have swapped the entry
                    # in), so without the descriptor's own identity the entry
                    # is left in place and logged, never removed by name.
                    try:
                        retry = os.fstat(fd)
                        ident = (retry.st_dev, retry.st_ino)
                    except OSError:
                        logger.warning(
                            "%s %s: fstat failed after creating it; the empty file is left in place", label, path
                        )
                if ident is not None:
                    _unlink_if_same(name, dir_fd, ident)
            raise
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def _open_leaf(name: str, dir_fd: int, flags: int, mode: int, label: str, path: str) -> int:
    try:
        return os.open(name, flags | _O_NOFOLLOW | _O_CLOEXEC, mode, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise UnsafePathError(f"SECURITY: {label} path changed to a symlink after it was checked: {path}") from exc
        raise


def _unlink_if_same(name: str, dir_fd: int, ident: tuple[int, int]) -> bool:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (st.st_dev, st.st_ino) != ident:
            return False
        os.unlink(name, dir_fd=dir_fd)
        return True
    except OSError:
        return False


def remove_created(path: str, roots: Iterable[str], ident: tuple[int, int], *, label: str = "file") -> bool:
    """Unlink a file this process created, by a confined walk to its directory and
    only while the name is still that inode: a directory swapped for a symlink
    never lets the cleanup delete a same-named file elsewhere."""
    try:
        root, parts = _resolve(path, roots, label)
        if not parts:
            return False
        dir_fd = _walk_dirs(root, parts[:-1], create=False, label=label, path=str(path))
    except (OSError, ValueError):
        return False
    try:
        return _unlink_if_same(parts[-1], dir_fd, ident)
    finally:
        os.close(dir_fd)
