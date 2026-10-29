"""Restore a snapshot into a new directory, checking every file as it is written."""

from __future__ import annotations

import hashlib
import os
import posixpath
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .errors import IntegrityError, RestorePointError
from .paths import check_relpath, windows_problem
from .repo import Repo
from .snapshot import KIND_DIR, KIND_FILE, KIND_LINK, Entry, Progress, Snapshot

_ON_WINDOWS = os.name == "nt"


@dataclass
class RestoreResult:
    files: int = 0
    dirs: int = 0
    links: int = 0
    bytes: int = 0
    warnings: list[str] = field(default_factory=list)


def select_entries(snap: Snapshot, only: Sequence[str]) -> list[Entry]:
    """All entries, or those under the named paths (plus the directories leading to them)."""
    if not only:
        return list(snap.entries)
    wanted: set[str] = set()
    by_path = {e.path: e for e in snap.entries}
    for raw in only:
        path = check_relpath(raw.strip("/"))
        if path not in by_path:
            raise RestorePointError(f"{raw!r} is not in snapshot {snap.id[:12]}")
        wanted.add(path)
        parts = path.split("/")
        wanted.update("/".join(parts[:i]) for i in range(1, len(parts)))
        if by_path[path].kind == KIND_DIR:
            wanted.update(p for p in by_path if p.startswith(path + "/"))
    return [e for e in snap.entries if e.path in wanted]


def _link_is_safe(entry: Entry) -> bool:
    target = entry.target
    if target.startswith("/") or (len(target) > 1 and target[1] == ":"):
        return False
    base = posixpath.dirname(entry.path)
    resolved = posixpath.normpath(posixpath.join(base, target))
    return not (resolved == ".." or resolved.startswith("../"))


def restore(
    repo: Repo,
    snap: Snapshot,
    target: Path,
    *,
    only: Sequence[str] = (),
    links: bool = False,
    progress: Progress = lambda _p: None,
) -> RestoreResult:
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise RestorePointError(f"{target} exists and is not empty; restore into a new folder")
    entries = select_entries(snap, only)
    if _ON_WINDOWS:
        for entry in entries:
            problem = windows_problem(entry.path)
            if problem:
                raise RestorePointError(f"cannot restore on Windows: {problem}")
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    result = RestoreResult()
    dir_times: list[tuple[Path, Entry]] = []

    def dest_for(entry: Entry) -> Path:
        dest = root.joinpath(*entry.path.split("/"))
        if root not in dest.parents:  # belt and braces: check_relpath already forbids this
            raise IntegrityError(f"{entry.path!r} would be written outside the target")
        return dest

    for entry in entries:
        if entry.kind == KIND_DIR:
            dest = dest_for(entry)
            dest.mkdir(parents=True, exist_ok=True)
            dir_times.append((dest, entry))
            result.dirs += 1
        elif entry.kind == KIND_FILE:
            _restore_file(repo, entry, dest_for(entry))
            result.files += 1
            result.bytes += entry.size
            progress(entry.path)
    for entry in entries:
        if entry.kind != KIND_LINK:
            continue
        if not links:
            result.warnings.append(f"link {entry.path} not restored (use --links)")
        elif not _link_is_safe(entry):
            result.warnings.append(f"link {entry.path} points outside the tree; not restored")
        else:
            try:
                os.symlink(entry.target, dest_for(entry))
                result.links += 1
            except OSError as exc:
                result.warnings.append(f"link {entry.path} could not be created: {exc.strerror}")
    for dest, entry in reversed(dir_times):
        os.utime(dest, ns=(entry.mtime_ns, entry.mtime_ns))
        if not _ON_WINDOWS:
            os.chmod(dest, (entry.mode & 0o777) | 0o700)
    return result


def _restore_file(repo: Repo, entry: Entry, dest: Path) -> None:
    digest = hashlib.sha256()
    written = 0
    try:
        with dest.open("xb") as out:  # "x": never write through something already there
            for key in entry.chunks:
                block = repo.get_chunk(key)
                digest.update(block)
                written += len(block)
                out.write(block)
    except IntegrityError:
        dest.unlink(missing_ok=True)
        raise
    if written != entry.size or digest.hexdigest() != entry.sha256:
        dest.unlink(missing_ok=True)
        raise IntegrityError(f"{entry.path} does not match its recorded checksum")
    os.utime(dest, ns=(entry.mtime_ns, entry.mtime_ns))
    if not _ON_WINDOWS:
        os.chmod(dest, entry.mode & 0o777)
