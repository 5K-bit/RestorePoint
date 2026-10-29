# Security Policy

## Intended use

RestorePoint copies folders you own into a repository you control, and copies them back. It makes no network connections and sends nothing anywhere.

## What RestorePoint protects, and what it does not

- **The repository is a copy of your data, unencrypted.** Chunks are compressed, not encrypted. Anyone who can read the repository can read what you backed up. Keep it on storage with the access control the originals have, or encrypt the disk. Encryption is on the roadmap.
- **It detects damage; it does not repair it.** Every chunk is checked against its SHA-256 whenever it is read, and `verify` re-reads them all. A damaged chunk is damaged for every snapshot that uses it (that is what deduplication means). Keep a second copy of the repository on other storage.
- **Tamper evidence, not tamper proofing.** A snapshot's id is the SHA-256 of its manifest, so altering a snapshot changes its id, and a manifest that was edited in place no longer matches its filename. Someone who can write to the repository can still delete snapshots or add a forged one under its own id. Record snapshot ids somewhere else if that matters.
- **Restores never overwrite.** A restore goes into a new or empty folder, files are created exclusively, and each is re-hashed as it is written. Stored paths are validated when a snapshot is read and again when written, so a forged manifest cannot place files outside the target. Symbolic links are recreated only with `--links` and only if they stay inside the restored tree.
- **Permissions are not security metadata here.** Modes are restored (without setuid/setgid bits); owners, ACLs and extended attributes are not stored.

The full analysis is in [docs/threat-model.md](docs/threat-model.md).

## Reporting a vulnerability

If you find a way to make a crafted repository or snapshot write outside the restore folder, overwrite existing files, crash or exhaust memory in `verify` or `restore`, or pass verification when data was altered, please report it privately through GitHub's **Security → Report a vulnerability** on this repository rather than a public issue.

Include the RestorePoint version (`restorepoint --version`), your OS and Python version, and the smallest repository or folder that reproduces it. Use synthetic data only; never attach real backups. Expect an initial response within a few days.
