"""Snapshot model, manifest parsing and creation."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .errors import IntegrityError, RestorePointError
from .paths import check_relpath, parent_of
from .repo import CHUNK_SIZE, SCHEMA, Repo, is_key

KIND_FILE, KIND_DIR, KIND_LINK = "file", "dir", "link"
_KINDS = {KIND_FILE, KIND_DIR, KIND_LINK}
MAX_ENTRIES = 2_000_000
Progress = Callable[[str], None]


@dataclass(frozen=True)
class Entry:
    path: str
    kind: str
    size: int
    mode: int
    mtime_ns: int
    sha256: str = ""
    chunks: tuple[str, ...] = ()
    target: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size": self.size,
            "mode": self.mode,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
            "chunks": list(self.chunks),
            "target": self.target,
        }


@dataclass(frozen=True)
class Snapshot:
    id: str
    created_ns: int
    label: str
    source: str
    entries: tuple[Entry, ...]
    skipped: tuple[str, ...]
    tool: str

    @property
    def files(self) -> list[Entry]:
        return [e for e in self.entries if e.kind == KIND_FILE]

    @property
    def total_size(self) -> int:
        return sum(e.size for e in self.entries if e.kind == KIND_FILE)

    def referenced_chunks(self) -> set[str]:
        return {c for e in self.entries for c in e.chunks}


def canonical(manifest: dict[str, object]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("ascii")


def _int(obj: dict[str, object], key: str, *, low: int = 0) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise IntegrityError(f"manifest field {key!r} is not a valid number")
    return value


def _str(obj: dict[str, object], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise IntegrityError(f"manifest field {key!r} is not text")
    return value


def _parse_entry(raw: object) -> Entry:
    if not isinstance(raw, dict) or set(raw) != {
        "path", "kind", "size", "mode", "mtime_ns", "sha256", "chunks", "target",
    }:  # fmt: skip
        raise IntegrityError("manifest entry has the wrong fields")
    path = check_relpath(_str(raw, "path"))
    kind = _str(raw, "kind")
    if kind not in _KINDS:
        raise IntegrityError(f"unknown entry kind {kind!r}")
    chunks_raw = raw["chunks"]
    if not isinstance(chunks_raw, list) or not all(
        isinstance(c, str) and is_key(c) for c in chunks_raw
    ):
        raise IntegrityError(f"bad chunk list for {path!r}")
    entry = Entry(
        path=path,
        kind=kind,
        size=_int(raw, "size"),
        mode=_int(raw, "mode"),
        mtime_ns=_int(raw, "mtime_ns", low=-(2**63)),
        sha256=_str(raw, "sha256"),
        chunks=tuple(chunks_raw),
        target=_str(raw, "target"),
    )
    if entry.mode > 0o7777:
        raise IntegrityError(f"bad mode for {path!r}")
    if kind == KIND_FILE:
        if not is_key(entry.sha256) or entry.target:
            raise IntegrityError(f"bad file entry {path!r}")
        if bool(entry.chunks) != (entry.size > 0):
            raise IntegrityError(f"size and chunk list disagree for {path!r}")
    elif kind == KIND_DIR:
        if entry.sha256 or entry.chunks or entry.target or entry.size:
            raise IntegrityError(f"bad directory entry {path!r}")
    elif entry.sha256 or entry.chunks or not entry.target or "\x00" in entry.target:
        raise IntegrityError(f"bad link entry {path!r}")
    return entry


def parse_snapshot(sid: str, data: bytes) -> Snapshot:
    try:
        raw = json.loads(data)
    except ValueError as exc:
        raise IntegrityError(f"snapshot {sid[:12]} is not valid JSON") from exc
    keys = {"schema", "tool", "created_ns", "label", "source", "entries", "skipped"}
    if not isinstance(raw, dict) or set(raw) != keys or raw["schema"] != SCHEMA:
        raise IntegrityError(f"snapshot {sid[:12]} has an unsupported format")
    entries_raw, skipped_raw = raw["entries"], raw["skipped"]
    if (
        not isinstance(entries_raw, list)
        or len(entries_raw) > MAX_ENTRIES
        or not isinstance(skipped_raw, list)
        or not all(isinstance(s, str) for s in skipped_raw)
    ):
        raise IntegrityError(f"snapshot {sid[:12]} has a malformed entry list")
    entries = tuple(_parse_entry(e) for e in entries_raw)
    kinds: dict[str, str] = {}
    for entry in entries:
        if entry.path in kinds:
            raise IntegrityError(f"duplicate path {entry.path!r}")
        parent = parent_of(entry.path)
        if parent and kinds.get(parent) != KIND_DIR:
            raise IntegrityError(f"{entry.path!r} has no directory entry for its parent")
        kinds[entry.path] = entry.kind
    return Snapshot(
        id=sid,
        created_ns=_int(raw, "created_ns"),
        label=_str(raw, "label"),
        source=_str(raw, "source"),
        entries=entries,
        skipped=tuple(skipped_raw),
        tool=_str(raw, "tool"),
    )


def load_snapshot(repo: Repo, ref: str) -> Snapshot:
    sid = resolve_ref(repo, ref)
    return parse_snapshot(sid, repo.read_snapshot_bytes(sid))


def resolve_ref(repo: Repo, ref: str) -> str:
    """Accept a full id, a unique prefix of at least 6 characters, or ``latest``."""
    ids = repo.snapshot_ids()
    if not ids:
        raise RestorePointError("the repository has no snapshots yet")
    if ref == "latest":
        return max(ids, key=lambda i: (load_created(repo, i), i))
    if len(ref) < 6:
        raise RestorePointError("give at least 6 characters of a snapshot id, or 'latest'")
    matches = [i for i in ids if i.startswith(ref.lower())]
    if not matches:
        raise RestorePointError(f"no snapshot matches {ref!r}")
    if len(matches) > 1:
        raise RestorePointError(f"{ref!r} matches {len(matches)} snapshots; use more characters")
    return matches[0]


def load_created(repo: Repo, sid: str) -> int:
    return parse_snapshot(sid, repo.read_snapshot_bytes(sid)).created_ns


def list_snapshots(repo: Repo) -> list[Snapshot]:
    snaps = [parse_snapshot(i, repo.read_snapshot_bytes(i)) for i in repo.snapshot_ids()]
    return sorted(snaps, key=lambda s: (s.created_ns, s.id))


# -- creation -----------------------------------------------------------------------------


@dataclass
class SnapshotResult:
    id: str = ""
    files: int = 0
    dirs: int = 0
    links: int = 0
    bytes: int = 0
    new_chunks: int = 0
    reused_chunks: int = 0
    stored_bytes: int = 0
    warnings: list[str] = field(default_factory=list)


def excluded(rel: str, is_dir: bool, patterns: Sequence[str]) -> bool:
    name = rel.rpartition("/")[2]
    for pat in patterns:
        if pat.endswith("/"):
            if is_dir and (
                fnmatch.fnmatchcase(name, pat[:-1]) or fnmatch.fnmatchcase(rel, pat[:-1])
            ):
                return True
        elif fnmatch.fnmatchcase(name, pat) or fnmatch.fnmatchcase(rel, pat):
            return True
    return False


class _Builder:
    def __init__(self, repo: Repo, patterns: Sequence[str], progress: Progress) -> None:
        self.repo, self.patterns, self.progress = repo, patterns, progress
        self.entries: list[Entry] = []
        self.skipped: list[str] = []
        self.result = SnapshotResult()

    def skip(self, rel: str, why: str) -> None:
        self.skipped.append(f"{rel}: {why}")
        self.result.warnings.append(f"skipped {rel}: {why}")

    def walk(self, directory: Path, prefix: str) -> None:
        try:
            with os.scandir(directory) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            self.skip(prefix or ".", exc.strerror or "cannot read directory")
            return
        for child in children:
            rel = f"{prefix}/{child.name}" if prefix else child.name
            try:
                check_relpath(rel)
            except IntegrityError:
                self.skip(rel, "name cannot be stored safely")
                continue
            try:
                st = child.stat(follow_symlinks=False)
            except OSError as exc:
                self.skip(rel, exc.strerror or "cannot stat")
                continue
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISLNK(st.st_mode):
                if excluded(rel, False, self.patterns):
                    continue
                try:
                    target = os.readlink(child.path)
                except OSError as exc:
                    self.skip(rel, exc.strerror or "cannot read link")
                    continue
                self.entries.append(
                    Entry(rel, KIND_LINK, 0, mode, st.st_mtime_ns, target=target.replace("\\", "/"))
                )
                self.result.links += 1
            elif stat.S_ISDIR(st.st_mode):
                if excluded(rel, True, self.patterns):
                    continue
                self.entries.append(Entry(rel, KIND_DIR, 0, mode, st.st_mtime_ns))
                self.result.dirs += 1
                self.walk(Path(child.path), rel)
            elif stat.S_ISREG(st.st_mode):
                if excluded(rel, False, self.patterns):
                    continue
                self.add_file(Path(child.path), rel, st)
            elif not excluded(rel, False, self.patterns):
                self.skip(rel, "not a regular file, directory or link")

    def add_file(self, path: Path, rel: str, st: os.stat_result) -> None:
        keys: list[str] = []
        digest = hashlib.sha256()
        total = 0
        try:
            with path.open("rb") as fh:
                while block := fh.read(CHUNK_SIZE):
                    digest.update(block)
                    total += len(block)
                    key, stored, new = self.repo.put_chunk(block)
                    keys.append(key)
                    if new:
                        self.result.new_chunks += 1
                        self.result.stored_bytes += stored
                    else:
                        self.result.reused_chunks += 1
        except OSError as exc:
            self.skip(rel, exc.strerror or "cannot read")
            return
        if total != st.st_size:
            self.result.warnings.append(
                f"{rel} changed while it was being read; stored what was read"
            )
        self.entries.append(
            Entry(
                rel,
                KIND_FILE,
                total,
                stat.S_IMODE(st.st_mode),
                st.st_mtime_ns,
                sha256=digest.hexdigest(),
                chunks=tuple(keys),
            )
        )
        self.result.files += 1
        self.result.bytes += total
        self.progress(rel)


def create_snapshot(
    repo: Repo,
    source: Path,
    *,
    label: str = "",
    excludes: Sequence[str] = (),
    now_ns: int,
    progress: Progress = lambda _p: None,
) -> SnapshotResult:
    src = source.resolve()
    if not src.is_dir():
        raise RestorePointError(f"{source} is not a directory")
    root = repo.root.resolve()
    if root == src or src in root.parents or root in src.parents:
        raise RestorePointError("the repository and the source folder must not contain each other")
    builder = _Builder(repo, excludes, progress)
    with repo.lock():
        builder.walk(src, "")
        manifest: dict[str, object] = {
            "schema": SCHEMA,
            "tool": f"restorepoint {__version__}",
            "created_ns": now_ns,
            "label": label,
            "source": src.name,
            "entries": [e.to_dict() for e in builder.entries],
            "skipped": builder.skipped,
        }
        builder.result.id = repo.write_snapshot(canonical(manifest))
    return builder.result
