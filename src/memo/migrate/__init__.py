"""v1 -> v2 migration. [001/FR-038 001/FR-039 001/FR-040]

**FR-038 (separate deployment)** and **FR-040 (reversibility)** are properties
this package must not break, so they are anchored here:

* FR-038: the v2 worktree, container, port (8091) and data volume are entirely
  separate from v1. Migration READS v1 over HTTP and never writes to it.
* FR-040: v1 is untouched throughout, so rollback is not a data operation at
  all — the MCP-flip is the only production change, and reverting it restores
  v1 exactly. `--rollback` clears v2 so migration can be re-run from clean.

Both properties come from the same rule: **the migration is read-only on v1.**
Every function here that touches v1 does so through a GET.
"""
