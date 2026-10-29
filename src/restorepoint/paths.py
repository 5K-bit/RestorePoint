"""Rules for the relative paths stored in a snapshot. Applied when creating and when reading."""

from __future__ import annotations

import re

from .errors import IntegrityError

_UNSAFE = re.compile(r"[\x00-\x1f\\]")
_WINDOWS_BAD = re.compile(r'[<>:"|?*]')
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}  # fmt: skip
MAX_PATH_LENGTH = 1024


def check_relpath(path: str) -> str:
    """Return ``path`` if it is relative, normalised, '/'-separated and free of control characters.

    This is the safety rule: a stored path can never climb out of the restore directory.
    """
    if not path or len(path) > MAX_PATH_LENGTH:
        raise IntegrityError(f"bad path length: {path[:40]!r}")
    if path.startswith("/") or path.endswith("/"):
        raise IntegrityError(f"path must be relative and normalised: {path!r}")
    for part in path.split("/"):
        if part in ("", ".", ".."):
            raise IntegrityError(f"path must be relative and normalised: {path!r}")
        if _UNSAFE.search(part):
            raise IntegrityError(f"control character or backslash in path: {path!r}")
    return path


def windows_problem(path: str) -> str | None:
    """Why ``path`` cannot be created on Windows, or None. Checked only when restoring there."""
    for part in path.split("/"):
        if _WINDOWS_BAD.search(part):
            return f"{path!r} contains a character Windows does not allow"
        if part.endswith((" ", ".")) or part.split(".")[0].lower() in _WINDOWS_RESERVED:
            return f"{path!r} is a name Windows does not allow"
    return None


def parent_of(path: str) -> str:
    head, _, _ = path.rpartition("/")
    return head
