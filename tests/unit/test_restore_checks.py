from __future__ import annotations

import os
import zlib
from pathlib import Path

import pytest

from restorepoint.checks import diff_snapshots, drill, prune, verify_repo
from restorepoint.errors import IntegrityError, RestorePointError
from restorepoint.repo import Repo
from restorepoint.restore import restore
from restorepoint.snapshot import (
    canonical,
    create_snapshot,
    load_snapshot,
)
from tests.conftest import NOW


def same_tree(a: Path, b: Path) -> bool:
    fa = sorted(p.relative_to(a).as_posix() for p in a.rglob("*"))
    fb = sorted(p.relative_to(b).as_posix() for p in b.rglob("*"))
    if fa != fb:
        return False
    return all((a / f).read_bytes() == (b / f).read_bytes() for f in fa if (a / f).is_file())


def test_roundtrip_is_byte_identical(repo: Repo, tree: Path, tmp_path: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    out = tmp_path / "out"
    res = restore(repo, load_snapshot(repo, "latest"), out)
    assert same_tree(tree, out)
    assert (res.files, res.bytes) == (4, 5_120_012)
    assert (out / "docs" / "b.txt").stat().st_mtime_ns == 1_600_000_000 * 10**9
    assert (out / "empty").is_dir()


def test_restore_refuses_a_non_empty_target(repo: Repo, tree: Path, tmp_path: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    out = tmp_path / "out"
    out.mkdir()
    (out / "precious.txt").write_text("do not overwrite")
    with pytest.raises(RestorePointError, match="not empty"):
        restore(repo, load_snapshot(repo, "latest"), out)
    assert (out / "precious.txt").read_text() == "do not overwrite"


def test_restore_only_selected_paths(repo: Repo, tree: Path, tmp_path: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    snap = load_snapshot(repo, "latest")
    out = tmp_path / "one"
    restore(repo, snap, out, only=["docs/b.txt"])
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*")) == ["docs", "docs/b.txt"]
    out2 = tmp_path / "dir"
    restore(repo, snap, out2, only=["docs/deep"])
    assert (out2 / "docs" / "deep" / "c.bin").is_file() and not (out2 / "a.txt").exists()
    with pytest.raises(RestorePointError, match="not in snapshot"):
        restore(repo, snap, tmp_path / "x", only=["nope"])


@pytest.mark.skipif(os.name == "nt", reason="symlinks")
def test_links_need_opt_in_and_must_stay_inside(repo: Repo, tree: Path, tmp_path: Path) -> None:
    os.symlink("a.txt", tree / "good")
    os.symlink("../../outside", tree / "docs" / "escape")
    os.symlink("/etc/passwd", tree / "absolute")
    create_snapshot(repo, tree, now_ns=NOW)
    snap = load_snapshot(repo, "latest")
    plain = restore(repo, snap, tmp_path / "plain")
    assert plain.links == 0 and len(plain.warnings) == 3
    out = tmp_path / "linked"
    res = restore(repo, snap, out, links=True)
    assert res.links == 1 and (out / "good").is_symlink()  # "../x" from docs/ is still inside
    assert not (out / "absolute").exists() and not (out / "docs" / "escape").is_symlink()
    assert sum("outside the tree" in w for w in res.warnings) == 2


def test_forged_traversal_manifest_cannot_write_outside(repo: Repo, tmp_path: Path) -> None:
    evil = {
        "schema": 1, "tool": "x", "created_ns": NOW, "label": "", "source": "s", "skipped": [],
        "entries": [{
            "path": "../escape.txt", "kind": "file", "size": 1, "mode": 0o644, "mtime_ns": 0,
            "sha256": "0" * 64, "chunks": ["0" * 64], "target": "",
        }],
    }  # fmt: skip
    sid = repo.write_snapshot(canonical(evil))
    with pytest.raises(IntegrityError):
        load_snapshot(repo, sid)
    assert not (tmp_path / "escape.txt").exists()


def test_damaged_chunk_aborts_restore_and_leaves_no_partial_file(
    repo: Repo, tree: Path, tmp_path: Path
) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    snap = load_snapshot(repo, "latest")
    victim = next(e for e in snap.entries if e.path == "docs/b.txt")
    repo.chunk_path(victim.chunks[0]).write_bytes(zlib.compress(b"tampered"))
    out = tmp_path / "out"
    with pytest.raises(IntegrityError):
        restore(repo, snap, out)
    assert not (out / "docs" / "b.txt").exists()


def test_verify_clean_repo(repo: Repo, tree: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    res = verify_repo(repo)
    assert res.ok and res.snapshots == 1 and res.chunks_checked == 4 and res.orphan_chunks == 0


def test_verify_finds_each_kind_of_damage(repo: Repo, tree: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    keys = sorted(load_snapshot(repo, "latest").referenced_chunks())
    repo.chunk_path(keys[0]).write_bytes(zlib.compress(b"flipped"))
    repo.chunk_path(keys[1]).unlink()
    res = verify_repo(repo)
    assert not res.ok and len(res.problems) == 2
    assert not verify_repo(repo, deep=False).ok  # the missing chunk shows even without re-reading
    sid = repo.snapshot_ids()[0]
    (repo.snapshots_dir / f"{sid}.json").write_bytes(b"{}")
    assert any("altered" in p for p in verify_repo(repo).problems)


def test_verify_counts_orphans(repo: Repo, tree: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    repo.put_chunk(b"left behind by a crashed run")
    assert verify_repo(repo).orphan_chunks == 1


def test_drill_passes_and_cleans_up(repo: Repo, tree: Path, tmp_path: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    res = drill(repo, load_snapshot(repo, "latest"), scratch=scratch)
    assert res.ok and res.files == 4
    assert list(scratch.iterdir()) == []


def test_drill_reports_damage(repo: Repo, tree: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    snap = load_snapshot(repo, "latest")
    repo.chunk_path(snap.files[0].chunks[0]).unlink()
    res = drill(repo, snap)
    assert not res.ok and "missing" in res.problems[0]


def test_diff(repo: Repo, tree: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    old = load_snapshot(repo, "latest")
    (tree / "a.txt").write_text("changed\n")
    (tree / "new.txt").write_text("n")
    (tree / "docs" / "b.txt").unlink()
    create_snapshot(repo, tree, now_ns=NOW + 1)
    new = load_snapshot(repo, "latest")
    d = diff_snapshots(old, new)
    assert (d.added, d.removed, d.modified) == (["new.txt"], ["docs/b.txt"], ["a.txt"])
    assert diff_snapshots(old, old).same


def test_prune_dry_run_then_apply(repo: Repo, tree: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    (tree / "a.txt").write_text("second version\n")
    create_snapshot(repo, tree, now_ns=NOW + 1)
    (tree / "a.txt").write_text("third version\n")
    create_snapshot(repo, tree, now_ns=NOW + 2)
    before = set(repo.iter_chunk_keys())
    dry = prune(repo, keep_last=1, apply=False)
    assert len(dry.removed) == 2 and not dry.applied and dry.chunks_freed == 2
    assert set(repo.iter_chunk_keys()) == before and len(repo.snapshot_ids()) == 3
    done = prune(repo, keep_last=1, apply=True)
    assert done.applied and len(repo.snapshot_ids()) == 1
    assert verify_repo(repo).ok and verify_repo(repo).orphan_chunks == 0
    assert load_snapshot(repo, "latest").created_ns == NOW + 2


def test_prune_refuses_when_a_snapshot_is_damaged(repo: Repo, tree: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    sid = repo.snapshot_ids()[0]
    (repo.snapshots_dir / f"{sid}.json").write_bytes(b"{}")
    before = set(repo.iter_chunk_keys())
    with pytest.raises(IntegrityError):
        prune(repo, keep_last=1, apply=True)
    assert set(repo.iter_chunk_keys()) == before
    with pytest.raises(RestorePointError, match="at least 1"):
        prune(repo, keep_last=0, apply=False)
