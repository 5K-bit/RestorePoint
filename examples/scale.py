"""Rough scale check: many small files plus one large file, snapshotted and restored.

    python examples/scale.py --out docs/evidence

Records what one machine did in one run; it is not a benchmark.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from restorepoint.checks import verify_repo  # noqa: E402
from restorepoint.repo import Repo  # noqa: E402
from restorepoint.restore import restore  # noqa: E402
from restorepoint.snapshot import create_snapshot, load_snapshot  # noqa: E402

FILES = 5000
BIG_MIB = 256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="restorepoint-scale-"))
    lines = [f"{FILES} small files + one {BIG_MIB} MiB file; one machine, one run"]
    try:
        rng = random.Random(1)  # noqa: S311
        src = work / "src"
        for i in range(FILES):
            d = src / f"d{i % 50:02d}"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"f{i:05d}.txt").write_bytes(rng.randbytes(rng.randrange(100, 8000)))
        block = rng.randbytes(1 << 20)
        with (src / "big.bin").open("wb") as fh:
            for i in range(BIG_MIB):
                fh.write(block[i:] + block[:i])  # distinct chunks, not trivially compressible
        repo = Repo.init(work / "store")

        def timed(label: str, fn):  # type: ignore[no-untyped-def]
            start = time.perf_counter()
            result = fn()
            lines.append(f"{label}: {time.perf_counter() - start:.1f} s")
            return result

        first = timed("first snapshot", lambda: create_snapshot(repo, src, now_ns=1))
        lines.append(f"  {first.files} files, {first.new_chunks} new chunks")
        (src / "d00" / "f00000.txt").write_text("edited")
        second = timed(
            "second snapshot (one file edited)", lambda: create_snapshot(repo, src, now_ns=2)
        )
        lines.append(f"  {second.new_chunks} new chunk(s), {second.reused_chunks} reused")
        timed("restore", lambda: restore(repo, load_snapshot(repo, "latest"), work / "out"))
        res = timed("verify (deep)", lambda: verify_repo(repo))
        lines.append(f"  ok={res.ok}, chunks checked {res.chunks_checked}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scale.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
