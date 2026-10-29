"""Disaster-recovery scenario, end to end, with a reproducible transcript.

A small project folder is snapshotted twice, then "lost" (deleted). The latest snapshot is
restored and compared with what was lost. Then one stored chunk is damaged, to show that
`verify` names the damage, a restore refuses to hand back corrupt data, and untouched files
can still be recovered.

    python examples/acceptance.py --out docs/evidence

Everything is synthetic and deterministic (fixed content, times and names), so the transcript
is the same on every run; tests/integration/test_acceptance.py compares it with the recorded one.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from restorepoint.cli import main  # noqa: E402

EPOCH_NS = 1_793_304_000 * 1_000_000_000  # 2026-10-29T18:00:00Z
FILE_TIME_NS = 1_790_000_000 * 1_000_000_000


def build_project(root: Path) -> None:
    rng = random.Random(2026)  # noqa: S311 - deterministic test data, not security
    files = {
        "README.md": "# Quarterly accounts\nNotes for the accountant.\n",
        "config/app.toml": 'name = "accounts"\nretention_years = 7\n',
        "config/secrets.example": "API_KEY=changeme\n",
    }
    for month in range(1, 13):
        files[f"ledger/2026-{month:02d}.csv"] = "date,account,amount\n" + "".join(
            ledger_row(rng, month, day) for day in range(1, 29)
        )
    for rel, text in files.items():
        write(root / rel, text.encode())
    write(root / "scans" / "invoice-0001.bin", rng.randbytes(3_000_000))
    write(root / "scans" / "invoice-0002.bin", rng.randbytes(1_500_000))
    (root / "archive" / "empty-for-now").mkdir(parents=True)
    stamp_all(root)


def ledger_row(rng: random.Random, month: int, day: int) -> str:
    account = rng.randrange(1000, 9999)
    amount = rng.randrange(1, 90000) / 100
    return f"2026-{month:02d}-{day:02d},{account},{amount:.2f}\n"


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def stamp_all(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        os.utime(path, ns=(FILE_TIME_NS, FILE_TIME_NS))


def tree_digest(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "<dir>"
    return out


class Transcript:
    def __init__(self, work: Path) -> None:
        self.lines: list[str] = []
        self.work = work
        self.clock = EPOCH_NS
        self.last_out = ""

    def scrub(self, text: str) -> str:
        return text.replace(str(self.work), "<work>")

    def note(self, text: str) -> None:
        self.lines.append(text)

    def run(self, *argv: str) -> int:
        shown = " ".join(a.replace(str(self.work), "<work>") for a in argv)
        self.lines.append(f"$ restorepoint {shown}")
        out, err = io.StringIO(), io.StringIO()

        def tick() -> int:
            self.clock += 3_600_000_000_000  # each snapshot an hour after the last
            return self.clock

        real = time.time_ns
        time.time_ns = tick  # type: ignore[assignment]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--repo", str(self.work / "store"), *argv])
        finally:
            time.time_ns = real
        self.last_out = out.getvalue()
        for text in (out.getvalue(), err.getvalue()):
            if text.strip():
                self.lines.append(self.scrub(text.rstrip()))
        self.lines.append(f"[exit {code}]")
        return code


def first_chunk_of(store: Path, rel: str) -> Path:
    """The stored file holding the first chunk of ``rel`` in the newest snapshot."""
    import json

    newest = max(
        (store / "snapshots").glob("*.json"), key=lambda p: json.loads(p.read_text())["created_ns"]
    )
    entries = json.loads(newest.read_text())["entries"]
    key = next(e["chunks"][0] for e in entries if e["path"] == rel)
    return store / "chunks" / key[:2] / key[2:]


def run_scenario(work: Path) -> str:
    t = Transcript(work)
    project = work / "accounts"
    build_project(project)
    t.note("# A small, synthetic accounts folder: 12 ledgers, config, two scanned invoices")
    t.run("init")
    t.run("snapshot", str(project), "--label", "monday")

    t.note("# Tuesday: one ledger edited, one file added, one removed")
    write(project / "ledger" / "2026-12.csv", b"date,account,amount\n2026-12-01,1001,12.50\n")
    write(project / "notes-tuesday.txt", b"Asked the accountant about December.\n")
    (project / "config" / "secrets.example").unlink()
    stamp_all(project)
    t.run("snapshot", str(project), "--label", "tuesday")
    t.run("list")

    ids = [line.split()[0] for line in t.last_out.splitlines()]
    t.run("diff", ids[0], ids[1])

    expected = tree_digest(project)
    t.note("# Disaster: the whole folder is deleted")
    shutil.rmtree(project)
    t.run("restore", "latest", str(work / "recovered"))
    t.note(
        "# recovered tree identical to the lost one: "
        + ("yes" if tree_digest(work / "recovered") == expected else "NO")
    )
    t.run("verify", "--drill", "latest", "--scratch", str(work))

    t.note("# Later: one stored chunk of an invoice is damaged on disk")
    victim = first_chunk_of(work / "store", "scans/invoice-0002.bin")
    data = bytearray(victim.read_bytes())
    data[len(data) // 2] ^= 0xFF
    victim.write_bytes(bytes(data))
    t.run("verify")
    t.run("restore", "latest", str(work / "recovered-again"))
    t.run("restore", "latest", str(work / "just-ledgers"), "--only", "ledger")
    ok = tree_digest(work / "just-ledgers") == {
        k: v for k, v in expected.items() if k == "ledger" or k.startswith("ledger/")
    }
    t.note("# the untouched ledgers were recovered intact: " + ("yes" if ok else "NO"))
    t.run("prune", "--keep-last", "1")
    return "\n".join(t.lines) + "\n"


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="restorepoint-acceptance-")).resolve()
    try:
        text = run_scenario(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    (args.out / "acceptance-transcript.txt").write_text(text, encoding="utf-8", newline="\n")
    print(text)


if __name__ == "__main__":
    main_cli()
