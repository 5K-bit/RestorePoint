# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- An operating-system error during `restore` or `snapshot` (an unwritable target, a full disk) ended in a Python traceback. It is now one line on stderr and exit status 1.

## [0.1.0] - 2026-10-29

First working version.

### Added

- Content-addressed snapshot repository: 1 MiB chunks named by SHA-256, compressed, deduplicated across files and snapshots; snapshot manifests named by the SHA-256 of their own bytes.
- `restorepoint snapshot` with `--exclude` and `--strict`; unreadable files and special files are skipped and recorded in the snapshot rather than silently dropped.
- `restore` into a new folder with per-file checksum verification, `--only` for single paths, and opt-in `--links` for symbolic links that stay inside the tree.
- `verify` (deep or `--shallow`) and `verify --drill`, which restores a snapshot to a scratch folder and compares the tree with the manifest.
- `list`, `show`, `diff`, and `prune --keep-last N` (dry run unless `--apply`; refuses to run when a snapshot is damaged).
- Exit codes: 0 ok, 1 error, 2 integrity failure.
