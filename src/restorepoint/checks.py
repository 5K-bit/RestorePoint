"""Verification, restore drills, diffs and pruning."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .errors import IntegrityError, RestorePointError
from .repo import CHUNK_SIZE, Repo
from .restore import restore
from .snapshot import KIND_DIR, KIND_FILE, KIND_LINK, Snapshot, parse_snapshot


@dataclass
class VerifyResult:
    snapshots: int = 0
    chunks_checked: int = 0
    orphan_chunks: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_repo(repo: Repo, *, deep: bool = True) -> VerifyResult:
    """Check every snapshot against its id and every chunk it uses.

    Deep (the default) decompresses and re-hashes each chunk; shallow only checks they exist.
    """
    result = VerifyResult()
    referenced: set[str] = set()
    for sid in repo.snapshot_ids():
        result.snapshots += 1
        try:
            snap = parse_snapshot(sid, repo.read_snapshot_bytes(sid))
        except IntegrityError as exc:
            result.problems.append(str(exc))
            continue
        referenced |= snap.referenced_chunks()
    for key in sorted(referenced):
        if deep:
            result.chunks_checked += 1
            try:
                repo.get_chunk(key)
            except IntegrityError as exc:
                result.problems.append(str(exc))
        elif not repo.has_chunk(key):
            result.problems.append(f"chunk {key[:12]} is missing")
    result.orphan_chunks = sum(1 for k in repo.iter_chunk_keys() if k not in referenced)
    return result


@dataclass
class DrillResult:
    files: int = 0
    bytes: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def drill(repo: Repo, snap: Snapshot, *, scratch: Path | None = None) -> DrillResult:
    """Restore ``snap`` into a scratch folder and compare the resulting tree with the manifest.

    Restore already checks each file's checksum; the drill also checks that every entry exists
    with the right type and size and that nothing extra appeared. The folder is removed after.
    """
    result = DrillResult()
    base = tempfile.mkdtemp(prefix="restorepoint-drill-", dir=scratch)
    target = Path(base) / "tree"
    try:
        try:
            done = restore(repo, snap, target, links=True)
        except IntegrityError as exc:
            result.problems.append(str(exc))
            return result
        expected = {e.path: e for e in snap.entries}
        seen: set[str] = set()
        for dirpath, dirnames, filenames in os.walk(target):
            for name in [*dirnames, *filenames]:
                full = Path(dirpath) / name
                seen.add(full.relative_to(target).as_posix())
        for path, entry in expected.items():
            full = target.joinpath(*path.split("/"))
            if entry.kind == KIND_LINK:
                continue  # links are recreated only when safe; restore warns about the rest
            elif entry.kind == KIND_DIR:
                if not full.is_dir():
                    result.problems.append(f"{path}: directory missing after restore")
            elif entry.kind == KIND_FILE:
                if not full.is_file():
                    result.problems.append(f"{path}: file missing after restore")
                elif full.stat().st_size != entry.size:
                    result.problems.append(f"{path}: size differs after restore")
                elif _hash_file(full) != entry.sha256:
                    result.problems.append(f"{path}: content differs after restore")
        result.problems.extend(
            f"{extra}: unexpected extra item after restore"
            for extra in sorted(seen - expected.keys())
        )
        result.files, result.bytes = done.files, done.bytes
        return result
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(CHUNK_SIZE):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class DiffResult:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    @property
    def same(self) -> bool:
        return not (self.added or self.removed or self.modified)


def diff_snapshots(old: Snapshot, new: Snapshot) -> DiffResult:
    a = {e.path: e for e in old.entries}
    b = {e.path: e for e in new.entries}
    out = DiffResult(
        added=sorted(b.keys() - a.keys()),
        removed=sorted(a.keys() - b.keys()),
    )
    for path in sorted(a.keys() & b.keys()):
        x, y = a[path], b[path]
        if (x.kind, x.sha256, x.target) != (y.kind, y.sha256, y.target):
            out.modified.append(path)
    return out


@dataclass
class PruneResult:
    removed: list[str] = field(default_factory=list)
    chunks_freed: int = 0
    bytes_freed: int = 0
    applied: bool = False


def prune(repo: Repo, *, keep_last: int, apply: bool) -> PruneResult:
    """Drop all but the newest ``keep_last`` snapshots, then delete chunks nothing uses.

    Dry run unless ``apply``. Refuses to touch anything if a snapshot cannot be read, because
    the chunks of a damaged snapshot must not be mistaken for garbage.
    """
    if keep_last < 1:
        raise RestorePointError("--keep-last must be at least 1")
    result = PruneResult(applied=apply)
    with repo.lock():
        snaps = []
        for sid in repo.snapshot_ids():
            snaps.append(parse_snapshot(sid, repo.read_snapshot_bytes(sid)))
        snaps.sort(key=lambda s: (s.created_ns, s.id))
        doomed = snaps[: max(0, len(snaps) - keep_last)]
        kept = snaps[len(doomed) :]
        result.removed = [s.id for s in doomed]
        live: set[str] = set()
        for s in kept:
            live |= s.referenced_chunks()
        garbage = [k for k in repo.iter_chunk_keys() if k not in live]
        result.chunks_freed = len(garbage)
        result.bytes_freed = sum(repo.chunk_path(k).stat().st_size for k in garbage)
        if apply:
            for s in doomed:
                repo.delete_snapshot(s.id)
            for key in garbage:
                repo.delete_chunk(key)
    return result
