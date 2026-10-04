"""Crash-recoverable, per-display ownership of temporary core key mappings."""

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import display


def _directory() -> Path:
    root = Path.home() / ".cache" / "linux-computer-use"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"unsafe keymap state directory: {root}")
    return root


def _paths() -> tuple[Path, Path]:
    key = hashlib.sha256(os.environ["DISPLAY"].split(".", 1)[0].encode()).hexdigest()[:20]
    root = _directory()
    return root / f"keymap-{key}.lock", root / f"keymap-{key}.json"


def _read(path: Path) -> list[dict]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError(f"unsafe keymap journal: {path}")
        with os.fdopen(fd, "r") as stream:
            fd = -1
            data = json.load(stream)
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(data, list):
        raise RuntimeError("invalid keymap journal")
    return data


def write(path: Path, entries: list[dict]) -> None:
    """Commit recovery data before changing the X server's keymap."""
    fd, temporary = tempfile.mkstemp(prefix=".keymap-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(entries, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def clear(path: Path) -> None:
    path.unlink(missing_ok=True)


def owned_mapping(current: list[int], bound: list[int]) -> bool:
    """XKB may zero unused slots after XChangeKeyboardMapping."""
    return bool(bound) and len(current) == len(bound) and any(current) and all(
        value in (0, expected) for value, expected in zip(current, bound)
    )


def matches_entry(current: list[int], entry: dict) -> bool:
    return owned_mapping(current, entry["bound"]) or owned_mapping(current, entry.get("prior", []))


def _recover(path: Path) -> None:
    if not path.exists():
        return
    entries = _read(path)
    d = display.get_core_display()
    for entry in entries:
        code = entry["code"]
        current = list(d.get_keyboard_mapping(code, 1)[0])
        if matches_entry(current, entry):
            d.change_keyboard_mapping(code, [entry["before"]])
    d.sync()
    clear(path)


@contextmanager
def session():
    """Serialize all lcu keymap borrowing across processes on this display."""
    lock_path, journal = _paths()
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError(f"unsafe keymap lock: {lock_path}")
        fcntl.flock(fd, fcntl.LOCK_EX)
        _recover(journal)
        yield journal
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def recover() -> None:
    """Called by the supervisor after a child exits unexpectedly."""
    with session():
        pass
