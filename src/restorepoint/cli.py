"""Command-line interface. Thin: parsing and printing only."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .checks import diff_snapshots, drill, prune, verify_repo
from .errors import IntegrityError, LockedError, RestorePointError
from .repo import Repo
from .restore import restore
from .snapshot import (
    create_snapshot,
    list_snapshots,
    load_snapshot,
)

EXIT_OK, EXIT_ERROR, EXIT_INTEGRITY = 0, 1, 2


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"  # pragma: no cover


def stamp(ns: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ns // 1_000_000_000))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="restorepoint", description="Verified folder snapshots. By OBEOS."
    )
    p.add_argument("--version", action="version", version=f"restorepoint {__version__}")
    p.add_argument(
        "--repo",
        default=os.environ.get("RESTOREPOINT_REPO", "restorepoint.rpstore"),
        help="repository folder (default: $RESTOREPOINT_REPO or ./restorepoint.rpstore)",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create an empty repository")

    s = sub.add_parser("snapshot", help="record the current state of a folder")
    s.add_argument("source")
    s.add_argument("--label", default="")
    s.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="glob to skip (repeatable); a trailing / matches directories only",
    )
    s.add_argument("--strict", action="store_true", help="exit 1 if anything was skipped")

    sub.add_parser("list", help="list snapshots")

    s = sub.add_parser("show", help="describe one snapshot")
    s.add_argument("snapshot", help="id, id prefix (6+ characters) or 'latest'")
    s.add_argument("--files", action="store_true")

    s = sub.add_parser("diff", help="what changed between two snapshots")
    s.add_argument("old")
    s.add_argument("new")

    s = sub.add_parser("restore", help="restore a snapshot into a new folder")
    s.add_argument("snapshot")
    s.add_argument("target")
    s.add_argument(
        "--only", action="append", default=[], metavar="PATH", help="restore just this path"
    )
    s.add_argument("--links", action="store_true", help="also recreate symbolic links")

    s = sub.add_parser("verify", help="check the repository's integrity")
    s.add_argument(
        "--shallow", action="store_true", help="check chunks exist, without re-reading them"
    )
    s.add_argument(
        "--drill",
        metavar="SNAPSHOT",
        help="also restore this snapshot to a scratch folder and compare",
    )
    s.add_argument("--scratch", help="where the drill restores to (default: system temp)")

    s = sub.add_parser("prune", help="forget old snapshots and free unused data")
    s.add_argument("--keep-last", type=int, required=True)
    s.add_argument(
        "--apply", action="store_true", help="actually delete (default: show what would go)"
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except IntegrityError as exc:
        print(f"restorepoint: integrity failure: {exc}", file=sys.stderr)
        return EXIT_INTEGRITY
    except (RestorePointError, LockedError) as exc:
        print(f"restorepoint: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:
        return EXIT_OK
    except OSError as exc:  # unwritable target, full disk, unreadable repository file
        where = f" ({exc.filename})" if exc.filename else ""
        print(f"restorepoint: {exc.strerror or exc}{where}", file=sys.stderr)
        return EXIT_ERROR


def _run(args: argparse.Namespace) -> int:
    root = Path(args.repo)
    cmd = args.command
    if cmd == "init":
        Repo.init(root)
        print(f"Created repository {root}. Keep it on a different disk from what it protects.")
        return EXIT_OK
    repo = Repo.open(root)
    if cmd == "snapshot":
        res = create_snapshot(
            repo,
            Path(args.source),
            label=args.label,
            excludes=args.exclude,
            now_ns=time.time_ns(),
        )
        for warning in res.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(
            f"snapshot {res.id[:12]}: {res.files} files, {res.dirs} folders, {human(res.bytes)}; "
            f"{res.new_chunks} new chunks ({human(res.stored_bytes)} stored), "
            f"{res.reused_chunks} reused"
        )
        return EXIT_ERROR if (args.strict and res.warnings) else EXIT_OK
    if cmd == "list":
        for snap in list_snapshots(repo):
            print(
                f"{snap.id[:12]}  {stamp(snap.created_ns)}  {len(snap.files):>6} files  "
                f"{human(snap.total_size):>10}  {snap.label or snap.source}"
            )
        return EXIT_OK
    if cmd == "show":
        snap = load_snapshot(repo, args.snapshot)
        print(f"snapshot {snap.id}\ncreated  {stamp(snap.created_ns)}  by {snap.tool}")
        print(f"source   {snap.source}  label {snap.label or '-'}")
        print(f"contents {len(snap.files)} files, {human(snap.total_size)}")
        for item in snap.skipped:
            print(f"skipped  {item}")
        if args.files:
            for e in snap.entries:
                print(f"  {e.kind:<4} {e.size:>10}  {e.path}")
        return EXIT_OK
    if cmd == "diff":
        d = diff_snapshots(load_snapshot(repo, args.old), load_snapshot(repo, args.new))
        for sign, names in (("+", d.added), ("-", d.removed), ("~", d.modified)):
            for name in names:
                print(f"{sign} {name}")
        if d.same:
            print("no differences")
        return EXIT_OK
    if cmd == "restore":
        snap = load_snapshot(repo, args.snapshot)
        rres = restore(repo, snap, Path(args.target), only=args.only, links=args.links)
        for warning in rres.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(
            f"restored {rres.files} files ({human(rres.bytes)}), "
            f"every file checked, to {args.target}"
        )
        return EXIT_OK
    if cmd == "verify":
        return _verify(repo, args)
    if cmd == "prune":
        pr = prune(repo, keep_last=args.keep_last, apply=args.apply)
        verb = "removed" if pr.applied else "would remove"
        print(
            f"{verb} {len(pr.removed)} snapshot(s) and {pr.chunks_freed} unused "
            f"chunk(s) ({human(pr.bytes_freed)})"
        )
        if not pr.applied:
            print("dry run: nothing was deleted. Add --apply to delete.")
        return EXIT_OK
    raise RestorePointError(f"unknown command {cmd!r}")  # pragma: no cover


def _verify(repo: Repo, args: argparse.Namespace) -> int:
    res = verify_repo(repo, deep=not args.shallow)
    print(
        f"snapshots: {res.snapshots}  chunks checked: {res.chunks_checked}  "
        f"unused chunks: {res.orphan_chunks}"
    )
    for problem in res.problems:
        print(f"PROBLEM: {problem}")
    ok = res.ok
    if args.drill:
        snap = load_snapshot(repo, args.drill)
        dr = drill(repo, snap, scratch=Path(args.scratch) if args.scratch else None)
        print(
            f"restore drill {snap.id[:12]}: {dr.files} files, "
            f"{human(dr.bytes)} restored and compared"
        )
        for problem in dr.problems:
            print(f"PROBLEM: {problem}")
        ok = ok and dr.ok
    print("integrity: OK" if ok else "integrity: FAILED")
    return EXIT_OK if ok else EXIT_INTEGRITY
