# Threat model

RestorePoint's job is to give back exactly what was stored, or to say clearly that it cannot. The main threats are decay, mistakes and a hostile repository.

## Assets

The data in the source folders, the repository that holds copies, and whatever sits at the restore target.

## Threats and mitigations

| Threat | Mitigation | Residual risk |
|---|---|---|
| Bit rot, truncated or missing chunk files | Chunks are verified against their SHA-256 on every read; `verify` re-reads all of them | Detected, not repaired. A chunk shared by many snapshots is damaged for all of them; keep a second copy of the repository. |
| Altered snapshot manifest | The manifest's name is the hash of its bytes; a mismatch is an integrity failure | Someone with write access can replace a manifest *and* its name, or delete one. Record snapshot ids elsewhere to notice. |
| Hostile repository: path traversal (`../`), absolute paths, control characters | Paths are validated when a manifest is parsed and again when joined to the target | None known |
| Hostile repository: decompression bomb | Decompression is capped at the chunk size and must end exactly | None known |
| Hostile repository: symlink tricks | Links are restored last, only with `--links`, only if relative and inside the tree; a link entry cannot be the parent of another entry; files are created exclusively | A restored link can later be edited by you |
| Restore overwrites good data | The target must be new or empty; files are never opened for overwrite | None |
| Two writers corrupt the repository | An exclusive lock file; chunk and manifest writes are atomic renames | A stale lock needs manual removal |
| Source changes during a snapshot | Sizes are compared; changed files are reported; what was read is stored consistently with its own hash | The folder is not frozen; use a filesystem snapshot for databases |
| `prune` deletes data still needed | Refuses to run if any snapshot cannot be read; keeps every chunk used by a kept snapshot; dry run by default | None known |
| A backup that cannot be restored | `verify --drill` restores to scratch and compares the tree | A drill proves this repository and this machine today |

## Out of scope

Encryption, authentication, hiding file names and sizes from someone who can read the repository, ownership and ACL preservation, and live databases.
