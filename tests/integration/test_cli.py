from __future__ import annotations

import os
import zlib
from pathlib import Path

import pytest

from restorepoint import __version__
from restorepoint.cli import build_parser, human, main


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def cli(tmp_path: Path, tree: Path) -> list[str]:
    return ["--repo", str(tmp_path / "store")]


def test_full_session(
    capsys: pytest.CaptureFixture[str], cli: list[str], tree: Path, tmp_path: Path
) -> None:
    assert run(capsys, *cli, "init")[0] == 0
    code, out, _ = run(capsys, *cli, "snapshot", str(tree), "--label", "first")
    assert code == 0 and "4 files" in out
    (tree / "a.txt").write_text("edited\n")
    assert run(capsys, *cli, "snapshot", str(tree))[0] == 0
    code, out, _ = run(capsys, *cli, "list")
    assert code == 0 and "first" in out and out.count("\n") == 2
    ids = [line.split()[0] for line in out.splitlines()]
    code, out, _ = run(capsys, *cli, "diff", ids[0], ids[1])
    assert "~ a.txt" in out
    assert "no differences" in run(capsys, *cli, "diff", ids[0], ids[0])[1]
    code, out, _ = run(capsys, *cli, "show", ids[0], "--files")
    assert "docs/deep/c.bin" in out and "source   src" in out
    out_dir = tmp_path / "back"
    code, out, _ = run(capsys, *cli, "restore", ids[0], str(out_dir))
    assert code == 0 and (out_dir / "a.txt").read_text() == "alpha\n"
    code, out, _ = run(capsys, *cli, "verify", "--drill", "latest")
    assert code == 0 and "integrity: OK" in out and "restore drill" in out
    code, out, _ = run(capsys, *cli, "prune", "--keep-last", "1")
    assert code == 0 and "dry run" in out
    code, out, _ = run(capsys, *cli, "prune", "--keep-last", "1", "--apply")
    assert "removed 1 snapshot" in out


def test_integrity_failure_exits_2(
    capsys: pytest.CaptureFixture[str], cli: list[str], tree: Path, tmp_path: Path
) -> None:
    run(capsys, *cli, "init")
    run(capsys, *cli, "snapshot", str(tree))
    chunk = _files(tmp_path / "store")
    chunk.write_bytes(zlib.compress(b"flipped"))
    code, out, _ = run(capsys, *cli, "verify")
    assert code == 2 and "integrity: FAILED" in out and "PROBLEM" in out
    code, _, err = run(capsys, *cli, "restore", "latest", str(tmp_path / "r"))
    assert code == 2 and "integrity failure" in err


def _files(root: Path) -> Path:
    return next(p for p in sorted((root / "chunks").rglob("*")) if p.is_file())


def test_errors_exit_1(
    capsys: pytest.CaptureFixture[str], cli: list[str], tree: Path, tmp_path: Path
) -> None:
    code, _, err = run(capsys, *cli, "list")
    assert code == 1 and "init" in err
    run(capsys, *cli, "init")
    assert run(capsys, *cli, "init")[0] == 1  # refuses to re-initialise
    assert run(capsys, *cli, "snapshot", str(tmp_path / "missing"))[0] == 1
    assert run(capsys, *cli, "restore", "latest", str(tmp_path / "o"))[0] == 1  # no snapshots


def test_strict_fails_when_something_is_skipped(
    capsys: pytest.CaptureFixture[str], cli: list[str], tree: Path
) -> None:
    run(capsys, *cli, "init")
    if os.name == "nt":
        pytest.skip("fifo")
    os.mkfifo(tree / "pipe")
    code, _, err = run(capsys, *cli, "snapshot", str(tree))
    assert code == 0 and "skipped pipe" in err
    assert run(capsys, *cli, "snapshot", str(tree), "--strict")[0] == 1


def test_lock_left_behind_is_explained(
    capsys: pytest.CaptureFixture[str], cli: list[str], tree: Path, tmp_path: Path
) -> None:
    run(capsys, *cli, "init")
    (tmp_path / "store" / "lock").write_text("123\n")
    code, _, err = run(capsys, *cli, "snapshot", str(tree))
    assert code == 1 and "delete that file" in err


def test_version_and_helpers(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--version"])
    assert f"restorepoint {__version__}" in capsys.readouterr().out
    assert human(0) == "0 B" and human(2048) == "2.0 KiB" and human(5 * 1024**3) == "5.0 GiB"
    assert build_parser().prog == "restorepoint"


def test_unwritable_target_is_a_clean_error_not_a_traceback(
    capsys: pytest.CaptureFixture[str], cli: list[str], tree: Path, tmp_path: Path
) -> None:
    run(capsys, *cli, "init")
    run(capsys, *cli, "snapshot", str(tree))
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    code, _, err = run(capsys, *cli, "restore", "latest", str(blocker / "inside"))
    assert code == 1 and err.startswith("restorepoint: ")
    assert "Traceback" not in err
