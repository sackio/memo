# Raw arms — `/mnt/nas` latency measurements, 2026-10-22

⛔ **These are here because the windows cannot be re-run.** The mount's state varies by the
hour — the same probe read SETATTR p50 at 510 ms and at 11 ms sixteen minutes apart — so a
lost log is a lost window, not a re-runnable experiment. The probes write host-local to
`~/.memo-watch/nfs-latency/` and that path is cleaned.

| tarball | probe | rows | re-analyse with |
|---|---|---|---|
| `armA-a31-running-20261022.tar.gz` | `nfs-latency-probe`, 6 hosts, 09:29–16:00 EDT, a31 RUNNING | `<iso8601> <host> <rc> <elapsed_ms>` | `../nfs-latency-episodes.py <dir>/*.log [--cadence-control]` |
| `opclass-20261022-v3rot.tar.gz` | `nfs-op-class-probe` label `20261022-v3rot`, 6 hosts, 17:26–18:15 EDT, **rotating order** — the run the conclusions rest on | `<iso8601> <host> <op> <rc> <elapsed_ms>` | `../nfs-op-class-analyse.py <dir>/*.log` |
| `opclass-20261022-v2-fixedorder.tar.gz` | same probe, **fixed order**, 16:37–17:20 EDT (collected 17:10, so it holds fewer rows than it ran) | same | same |

⭐ **The fixed-order tarball is kept deliberately, as the control's control.** It is the run
in which position and operation were the same measurement, and it is the only evidence that
rotating the order changed one finding (`setattr_hot` vs `setattr_cold`, p=0.81 → p=0.0034)
and left the others standing. ⛔ Do not pool it with the rotated run or quote its p50s;
`../nfs-op-class-results.md` says which columns transfer between runs and which do not.

Arm B of the A/B (a31 PAUSED) is a different day and is not here yet.
