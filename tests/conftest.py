from __future__ import annotations

import os
from pathlib import Path

import pytest

from restorepoint.repo import Repo

NOW = 1_700_000_000 * 1_000_000_000


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo.init(tmp_path / "store")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small source folder: text, a multi-chunk binary, an empty folder, nesting."""
    src = tmp_path / "src"
    (src / "docs" / "deep").mkdir(parents=True)
    (src / "empty").mkdir()
    (src / "a.txt").write_bytes(b"alpha\n")
    (src / "docs" / "b.txt").write_bytes(b"bravo\n")
    (src / "docs" / "deep" / "c.bin").write_bytes(bytes(range(256)) * 20000)  # 5,120,000 bytes
    (src / "zero.dat").write_bytes(b"")
    for f in src.rglob("*"):
        os.utime(f, ns=(1_600_000_000 * 10**9, 1_600_000_000 * 10**9))
    return src
