from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from restorepoint.errors import IntegrityError, RestorePointError
from restorepoint.repo import Repo
from restorepoint.snapshot import (
    _Builder,
    canonical,
    create_snapshot,
    excluded,
    list_snapshots,
    load_snapshot,
    parse_snapshot,
    resolve_ref,
)
from tests.conftest import NOW


def manifest(entries: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": 1, "tool": "t", "created_ns": NOW, "label": "", "source": "s",
        "entries": entries, "skipped": [],
    }  # fmt: skip


def d(path: str) -> dict[str, object]:
    return {
        "path": path, "kind": "dir", "size": 0, "mode": 0o755, "mtime_ns": 0,
        "sha256": "", "chunks": [], "target": "",
    }  # fmt: skip


def test_snapshot_records_everything(repo: Repo, tree: Path) -> None:
    res = create_snapshot(repo, tree, label="first", now_ns=NOW)
    assert (res.files, res.dirs, res.links) == (4, 3, 0)
    # c.bin is 5 chunks, but its first four are identical, so only two are new; plus two small files
    assert res.new_chunks == 4 and res.reused_chunks == 3
    snap = load_snapshot(repo, "latest")
    assert snap.label == "first" and snap.source == "src"
    assert {e.path for e in snap.entries} == {
        "a.txt", "docs", "docs/b.txt", "docs/deep", "docs/deep/c.bin", "empty", "zero.dat",
    }  # fmt: skip
    assert snap.total_size == 6 + 6 + 5_120_000


def test_second_snapshot_reuses_unchanged_data(repo: Repo, tree: Path) -> None:
    create_snapshot(repo, tree, now_ns=NOW)
    (tree / "a.txt").write_text("changed\n")
    res = create_snapshot(repo, tree, now_ns=NOW + 1)
    assert res.new_chunks == 1 and res.reused_chunks > 3
    assert len(list_snapshots(repo)) == 2


def test_same_state_gives_same_snapshot_id(repo: Repo, tree: Path) -> None:
    a = create_snapshot(repo, tree, now_ns=NOW)
    b = create_snapshot(repo, tree, now_ns=NOW)
    assert a.id == b.id and len(repo.snapshot_ids()) == 1


def test_excludes(repo: Repo, tree: Path) -> None:
    (tree / "cache").mkdir()
    (tree / "cache" / "x.tmp").write_text("x")
    (tree / "keep.tmp").write_text("x")
    create_snapshot(repo, tree, excludes=["*.tmp", "cache/", "docs/deep"], now_ns=NOW)
    paths = {e.path for e in load_snapshot(repo, "latest").entries}
    assert "keep.tmp" not in paths and "cache" not in paths and "docs/deep" not in paths
    assert "docs/b.txt" in paths
    assert excluded("a/b", True, ["b/"]) and not excluded("a/b", False, ["b/"])


def test_repo_inside_source_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    inner = Repo.init(src / "store")
    with pytest.raises(RestorePointError, match="must not contain"):
        create_snapshot(inner, src, now_ns=NOW)
    with pytest.raises(RestorePointError, match="not a directory"):
        create_snapshot(inner, tmp_path / "missing", now_ns=NOW)


def test_unreadable_files_are_skipped_and_reported(
    repo: Repo, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = Path.open

    def fake_open(self: Path, *a: object, **k: object):  # type: ignore[no-untyped-def]
        if self.name == "b.txt":
            raise PermissionError(13, "Permission denied")
        return real_open(self, *a, **k)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", fake_open)
    res = create_snapshot(repo, tree, now_ns=NOW)
    assert any("docs/b.txt" in w for w in res.warnings)
    snap = load_snapshot(repo, "latest")
    assert "docs/b.txt" not in {e.path for e in snap.entries}
    assert any("docs/b.txt" in s for s in snap.skipped)


@pytest.mark.skipif(os.name == "nt", reason="symlinks and fifos")
def test_links_and_special_files(repo: Repo, tree: Path) -> None:
    os.symlink("a.txt", tree / "ln")
    os.mkfifo(tree / "pipe")
    res = create_snapshot(repo, tree, now_ns=NOW)
    assert res.links == 1 and any("pipe" in w for w in res.warnings)
    snap = load_snapshot(repo, "latest")
    link = next(e for e in snap.entries if e.path == "ln")
    assert link.kind == "link" and link.target == "a.txt"


def test_file_changing_during_read_is_reported(
    repo: Repo, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _Builder.add_file

    def stale(self: _Builder, path: Path, rel: str, st: os.stat_result) -> None:
        if rel == "a.txt":  # pretend the file was a different size when it was listed
            st = SimpleNamespace(  # type: ignore[assignment]
                st_size=st.st_size + 10, st_mode=st.st_mode, st_mtime_ns=st.st_mtime_ns
            )
        original(self, path, rel, st)

    monkeypatch.setattr(_Builder, "add_file", stale)
    res = create_snapshot(repo, tree, now_ns=NOW)
    assert any("a.txt changed while it was being read" in w for w in res.warnings)


def test_refs(repo: Repo, tree: Path) -> None:
    with pytest.raises(RestorePointError, match="no snapshots"):
        resolve_ref(repo, "latest")
    a = create_snapshot(repo, tree, now_ns=NOW).id
    (tree / "a.txt").write_text("v2\n")
    b = create_snapshot(repo, tree, now_ns=NOW + 5).id
    assert resolve_ref(repo, "latest") == b
    assert resolve_ref(repo, a[:8].upper()) == a
    with pytest.raises(RestorePointError, match="at least 6"):
        resolve_ref(repo, "ab")
    with pytest.raises(RestorePointError, match="no snapshot matches"):
        resolve_ref(repo, "ffffff")


def test_manifest_validation() -> None:
    ok = canonical(manifest([d("a"), d("a/b")]))
    assert len(parse_snapshot("0" * 64, ok).entries) == 2
    bad = {
        "traversal": [d("../x")],
        "absolute": [d("/x")],
        "duplicate": [d("a"), d("a")],
        "orphan": [d("a/b")],
        "kind": [{**d("a"), "kind": "device"}],
        "mode": [{**d("a"), "mode": 0o77777}],
        "extra field": [{**d("a"), "extra": 1}],
        "dir with chunks": [{**d("a"), "chunks": ["0" * 64]}],
        "file size/chunks": [{**d("a"), "kind": "file", "size": 5, "sha256": "0" * 64}],
        "bad chunk": [{**d("a"), "kind": "file", "size": 5, "sha256": "0" * 64, "chunks": ["zz"]}],
        "link without target": [{**d("a"), "kind": "link"}],
    }
    for name, entries in bad.items():
        with pytest.raises(IntegrityError):
            parse_snapshot("0" * 64, canonical(manifest(entries)))
        assert name
    with pytest.raises(IntegrityError, match="parent"):
        parse_snapshot(
            "0" * 64,
            canonical(manifest([{**d("l"), "kind": "link", "target": "x"}, d("l/inside")])),
        )
    with pytest.raises(IntegrityError):
        parse_snapshot("0" * 64, b"not json")
    with pytest.raises(IntegrityError):
        parse_snapshot("0" * 64, b'{"schema": 2}')
