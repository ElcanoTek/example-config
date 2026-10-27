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
import os
import stat
from collections.abc import Iterable

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
    """The deepest (longest) real root that contains ``real``, or None."""
    best: str | None = None
    for root in roots:
        if not root:
            continue
        real_root = os.path.realpath(root)
        if is_within(real, real_root) and (best is None or len(real_root) > len(best)):
            best = real_root
    return best


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
    fd = os.open(root, os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC)
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
) -> bool:
    """Create (or, unless ``exclusive``, truncate) a confined regular file and
    write ``data`` through its descriptor. Returns True when the file was
    created by this call."""
    created = True
    try:
        fd = open_file(path, roots, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, create_dirs=create_dirs, label=label)
    except FileExistsError:
        if exclusive:
            raise
        created = False
        fd = open_file(path, roots, os.O_WRONLY | _O_NONBLOCK, mode, create_dirs=create_dirs, label=label)
    try:
        st = os.fstat(fd)
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
    finally:
        os.close(fd)
