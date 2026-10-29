# 3. Restore only into a new or empty folder

**Status:** accepted

**Context.** The moment you need a restore is the moment you are least careful. A restore that can overwrite is one wrong path from making things worse.

**Decision.** `restore` refuses a non-empty target and creates files exclusively. Getting a file back into place is a deliberate second step.

**Consequences.** One extra copy after a restore. In exchange, a restore can never destroy what is there, and a half-finished restore can be deleted without thought.
