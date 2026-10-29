# 2. A snapshot's id is the SHA-256 of its manifest

**Status:** accepted

**Context.** Editing a manifest to hide a change should be detectable without any extra index.

**Decision.** The manifest is canonical JSON (sorted keys, no whitespace) and its file name is its SHA-256. Reading a snapshot re-hashes it.

**Consequences.** Identical content and times give an identical id (the acceptance transcript depends on this). A manifest altered in place is detected. An attacker who can write the repository can still replace a manifest together with its name; ids recorded elsewhere are the defence.
