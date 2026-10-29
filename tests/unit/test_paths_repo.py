from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from restorepoint.errors import IntegrityError, LockedError, RestorePointError
from restorepoint.paths import check_relpath, windows_problem
from restorepoint.repo import CHUNK_SIZE, Repo, sha256_hex


@pytest.mark.parametrize(
    "bad", ["", "/abs", "a/../b", "..", "a//b", "a/./b", "a/", "a\\b", "a\x00b"]
)
def test_unsafe_paths_are_rejected(bad: str) -> None:
    with pytest.raises(IntegrityError):
        check_relpath(bad)


def test_normal_paths_pass() -> None:
    assert check_relpath("docs/deep/c.bin") == "docs/deep/c.bin"
    assert check_relpath("naïve file (1).txt")


def test_windows_rules() -> None:
    assert windows_problem("ok/file.txt") is None
    assert windows_problem("a:b") is not None
    assert windows_problem("dir/CON.txt") is not None
    assert windows_problem("trailing.") is not None


def test_init_refuses_non_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "f").write_text("data")
    with pytest.raises(RestorePointError, match="not empty"):
        Repo.init(tmp_path / "x")


def test_open_requires_a_repository(tmp_path: Path) -> None:
    with pytest.raises(RestorePointError, match="not a RestorePoint repository"):
        Repo.open(tmp_path)
    (tmp_path / "repo.json").write_text('{"schema": 99}')
    with pytest.raises(RestorePointError, match="unsupported"):
        Repo.open(tmp_path)


def test_chunk_roundtrip_and_dedup(repo: Repo) -> None:
    key, stored, new = repo.put_chunk(b"hello" * 1000)
    assert new and stored > 0 and key == sha256_hex(b"hello" * 1000)
    assert repo.put_chunk(b"hello" * 1000) == (key, 0, False)
    assert repo.get_chunk(key) == b"hello" * 1000
    assert list(repo.iter_chunk_keys()) == [key]


def test_corrupt_chunks_are_detected(repo: Repo) -> None:
    key, _, _ = repo.put_chunk(b"precious data")
    path = repo.chunk_path(key)
    path.write_bytes(zlib.compress(b"different data"))
    with pytest.raises(IntegrityError, match="checksum mismatch"):
        repo.get_chunk(key)
    path.write_bytes(b"not zlib at all")
    with pytest.raises(IntegrityError, match="not valid"):
        repo.get_chunk(key)
    path.write_bytes(path.read_bytes()[:0])
    with pytest.raises(IntegrityError):
        repo.get_chunk(key)
    path.unlink()
    with pytest.raises(IntegrityError, match="missing"):
        repo.get_chunk(key)


def test_decompression_bomb_is_refused(repo: Repo) -> None:
    bomb = zlib.compress(b"\0" * (CHUNK_SIZE * 8))
    key = sha256_hex(b"\0" * (CHUNK_SIZE * 8))
    path = repo.chunk_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bomb)
    with pytest.raises(IntegrityError, match="wrong length"):
        repo.get_chunk(key)


def test_bad_keys_are_refused(repo: Repo) -> None:
    for bad in ("../../etc/passwd", "abc", "Z" * 64):
        with pytest.raises(IntegrityError):
            repo.chunk_path(bad)
        with pytest.raises(IntegrityError):
            repo.read_snapshot_bytes(bad)


def test_lock_excludes_a_second_writer(repo: Repo) -> None:
    with repo.lock(), pytest.raises(LockedError, match="delete that file"), repo.lock():
        pass
    with repo.lock():  # released afterwards
        pass


def test_altered_snapshot_is_detected(repo: Repo) -> None:
    sid = repo.write_snapshot(b'{"a":1}')
    (repo.snapshots_dir / f"{sid}.json").write_bytes(b'{"a":2}')
    with pytest.raises(IntegrityError, match="altered"):
        repo.read_snapshot_bytes(sid)
