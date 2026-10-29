# Architecture

RestorePoint is a small content-addressed store with one rule: **nothing is trusted until it has been hashed again.**

```
 folder ─► scan ─► 1 MiB chunks ─► SHA-256 ─► repository
                                    │          chunks/ab/cdef…     zlib, named by raw-content hash
                                    │          snapshots/<id>.json manifest, <id> = SHA-256 of its bytes
                                    ▼
                        manifest written last
 repository ─► restore ─► new folder ─► each file re-hashed as written
            ─► verify  ─► every chunk decompressed and re-hashed
            ─► drill   ─► restore to scratch, compare tree to manifest, delete scratch
```

## Repository layout

| Path | Contents |
|---|---|
| `repo.json` | `{"schema": 1}` |
| `chunks/ab/cdef…` | One chunk (≤ 1 MiB), zlib-compressed; the name is the SHA-256 of the **uncompressed** bytes. |
| `snapshots/<id>.json` | Canonical JSON manifest; the name is the SHA-256 of these exact bytes. |
| `tmp/` | Files being written; moved into place with an atomic rename once complete. |
| `lock` | Present while a writer runs. |

## The manifest

Schema 1: tool version, creation time, label, source folder name, a list of entries (`path`, `kind` file/dir/link, `size`, `mode`, `mtime_ns`, whole-file `sha256`, `chunks`, link `target`) and a `skipped` list. Entries are validated strictly when read: safe relative paths, a directory entry for every parent, no duplicates, chunk lists consistent with sizes.

## Why snapshots are safe to interrupt

Chunks are written first, each atomically; the manifest is written last. A crash leaves unreferenced chunks (reported by `verify`, freed by `prune`), never a snapshot pointing at missing data. A leftover `lock` file is explained in the error message.

## Modules

| Module | Responsibility |
|---|---|
| `repo.py` | Chunk and snapshot storage, hashing, compression limits, the writer lock. |
| `paths.py` | The path rules, shared by creating and reading. |
| `snapshot.py` | Manifest model and strict parsing; scanning a folder and writing a snapshot. |
| `restore.py` | Restoring into a new folder with per-file verification. |
| `checks.py` | `verify`, the restore drill, `diff`, `prune`. |
| `cli.py` | Parsing and printing; maps errors to exit codes. |

## Exit codes

`0` ok · `1` error · `2` integrity failure (damaged chunk, altered snapshot, failed drill).
