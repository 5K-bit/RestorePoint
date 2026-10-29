# 4. `verify --drill` restores and compares

**Status:** accepted

**Context.** Checking chunk hashes shows the stored bytes are intact. It does not show that the manifest, the paths and the restore code produce the folder you lost.

**Decision.** The drill performs a real restore into a scratch folder, then compares every entry's type and size and every file's hash with the manifest and checks nothing extra appeared, then removes the scratch folder.

**Consequences.** A drill costs a full restore's time and disk space. It is the only check that exercises the same code path as an emergency.
