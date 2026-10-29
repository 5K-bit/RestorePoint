"""The on-disk repository: content-addressed chunks, snapshot manifests, a writer lock.

Layout::

    repo.json                 {"schema": 1}
    chunks/ab/cdef...         zlib-compressed chunk, named by the SHA-256 of its raw bytes
    snapshots/<id>.json       canonical manifest; <id> is the SHA-256 of these exact bytes
    tmp/                      files being written; moved into place when complete
    lock                      exists while a writer is active

A snapshot manifest is written last, so a crash leaves unreferenced chunks, never a snapshot
that points at missing data.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import zlib
from collections.abc import Iterator
from pathlib import Path

from .errors import IntegrityError, LockedError, RestorePointError

CHUNK_SIZE = 1 << 20
SCHEMA = 1
_HEX = re.compile(r"[0-9a-f]{64}")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_key(text: str) -> bool:
    return bool(_HEX.fullmatch(text))


def _write_atomic(tmp_dir: Path, final: Path, data: bytes) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{os.getpid()}-{final.name}.part"
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.chunks_dir = root / "chunks"
        self.snapshots_dir = root / "snapshots"
        self.tmp_dir = root / "tmp"

    # -- creation and opening -------------------------------------------------------------
    @classmethod
    def init(cls, root: Path) -> Repo:
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise RestorePointError(f"{root} exists and is not empty; refusing to initialise it")
        repo = cls(root)
        for d in (repo.chunks_dir, repo.snapshots_dir, repo.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)
        (root / "repo.json").write_text(json.dumps({"schema": SCHEMA}) + "\n", encoding="utf-8")
        return repo

    @classmethod
    def open(cls, root: Path) -> Repo:
        marker = root / "repo.json"
        try:
            meta = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RestorePointError(
                f"{root} is not a RestorePoint repository (run `restorepoint init` first)"
            ) from exc
        if not isinstance(meta, dict) or meta.get("schema") != SCHEMA:
            raise RestorePointError(f"{root} has an unsupported repository schema")
        repo = cls(root)
        repo.tmp_dir.mkdir(exist_ok=True)
        return repo

    # -- locking ---------------------------------------------------------------------------
    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        path = self.root / "lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise LockedError(
                f"{path} exists: another RestorePoint run is writing, or one stopped abruptly. "
                "If nothing is running, delete that file and retry."
            ) from exc
        try:
            os.write(fd, f"{os.getpid()}\n".encode())
            os.close(fd)
            yield
        finally:
            with contextlib.suppress(OSError):
                path.unlink()

    # -- chunks ----------------------------------------------------------------------------
    def chunk_path(self, key: str) -> Path:
        if not is_key(key):
            raise IntegrityError(f"bad chunk key {key[:20]!r}")
        return self.chunks_dir / key[:2] / key[2:]

    def has_chunk(self, key: str) -> bool:
        return self.chunk_path(key).is_file()

    def put_chunk(self, data: bytes) -> tuple[str, int, bool]:
        """Store ``data``. Returns (key, stored bytes, whether it was new)."""
        key = sha256_hex(data)
        path = self.chunk_path(key)
        if path.is_file():
            return key, 0, False
        packed = zlib.compress(data, 6)
        _write_atomic(self.tmp_dir, path, packed)
        return key, len(packed), True

    def get_chunk(self, key: str) -> bytes:
        path = self.chunk_path(key)
        try:
            packed = path.read_bytes()
        except OSError as exc:
            raise IntegrityError(f"chunk {key[:12]} is missing") from exc
        inflater = zlib.decompressobj()
        try:
            data = inflater.decompress(packed, CHUNK_SIZE + 1)
        except zlib.error as exc:
            raise IntegrityError(
                f"chunk {key[:12]} is corrupt (not valid compressed data)"
            ) from exc
        if len(data) > CHUNK_SIZE or inflater.unconsumed_tail or not inflater.eof:
            raise IntegrityError(f"chunk {key[:12]} is corrupt (wrong length)")
        if sha256_hex(data) != key:
            raise IntegrityError(f"chunk {key[:12]} is corrupt (checksum mismatch)")
        return data

    def iter_chunk_keys(self) -> Iterator[str]:
        if not self.chunks_dir.is_dir():
            return
        for sub in sorted(self.chunks_dir.iterdir()):
            if sub.is_dir() and len(sub.name) == 2:
                for item in sorted(sub.iterdir()):
                    key = sub.name + item.name
                    if is_key(key):
                        yield key

    def delete_chunk(self, key: str) -> int:
        path = self.chunk_path(key)
        size = path.stat().st_size
        path.unlink()
        return size

    # -- snapshots -------------------------------------------------------------------------
    def snapshot_ids(self) -> list[str]:
        if not self.snapshots_dir.is_dir():
            return []
        return sorted(p.stem for p in self.snapshots_dir.glob("*.json") if is_key(p.stem))

    def write_snapshot(self, canonical: bytes) -> str:
        sid = sha256_hex(canonical)
        _write_atomic(self.tmp_dir, self.snapshots_dir / f"{sid}.json", canonical)
        return sid

    def read_snapshot_bytes(self, sid: str) -> bytes:
        if not is_key(sid):
            raise IntegrityError(f"bad snapshot id {sid[:20]!r}")
        try:
            data = (self.snapshots_dir / f"{sid}.json").read_bytes()
        except OSError as exc:
            raise IntegrityError(f"snapshot {sid[:12]} is missing") from exc
        if sha256_hex(data) != sid:
            raise IntegrityError(f"snapshot {sid[:12]} was altered (its checksum does not match)")
        return data

    def delete_snapshot(self, sid: str) -> None:
        (self.snapshots_dir / f"{sid}.json").unlink()
