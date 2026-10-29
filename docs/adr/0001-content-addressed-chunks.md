# 1. Content-addressed chunks, with the manifest written last

**Status:** accepted

**Context.** Backups should not store the same data twice, and a crash must not produce a snapshot that looks complete but is not.

**Decision.** Split files into 1 MiB chunks named by the SHA-256 of their content. Write chunks first (atomic rename), then the manifest.

**Consequences.** Re-snapshotting an unchanged folder stores nothing new. Identical chunks are shared inside a file, across files and across snapshots, so damage to one chunk affects every snapshot using it. Fixed-size chunking means an insertion early in a large file changes all following chunks; content-defined chunking is a possible later change.
