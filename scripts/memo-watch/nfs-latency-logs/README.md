# Raw arms — `/mnt/nas` latency measurements, 2026-10-22

⛔ **These are here because the windows cannot be re-run.** The mount's state varies by the
hour — the same probe read SETATTR p50 at 510 ms, 11 ms and 112 ms in three windows on one afternoon — so a
lost log is a lost window, not a re-runnable experiment. The probes write host-local to
`~/.memo-watch/nfs-latency/` and that path is cleaned.

| tarball | probe | rows | re-analyse with |
|---|---|---|---|
| `armA-a31-running-20261022.tar.gz` | `nfs-latency-probe`, 6 hosts, 09:29–16:00 EDT, a31 RUNNING | `<iso8601> <host> <rc> <elapsed_ms>` | `../nfs-latency-episodes.py <dir>/*.log [--cadence-control]` |
| `opclass-20261022-v3rot.tar.gz` | `nfs-op-class-probe` label `20261022-v3rot`, 6 hosts, 17:26–18:15 EDT, **rotating order** — the first clean run; its CREATE findings are confirmed by v4 | `<iso8601> <host> <op> <rc> <elapsed_ms>` | `../nfs-op-class-analyse.py <dir>/*.log` |
| `opclass-20261022-v4idle.tar.gz` | same probe plus `create_idle`, 6 hosts, 18:34–19:39 EDT, **rotating order** — the run that settled the own-vs-shared cause and retracted the hot/cold claim | same + a `# setup` row | same |
| `getattr-20261022.tar.gz` | `nfs-getattr-probe` label `20261022-getattr`, 6 hosts, 19:54–21:48 EDT, **rotating order** — the run that DISCONFIRMED the hot-inode guess for GETATTR and isolated cache residency as the cost | `<iso8601> <host> <op> <rc> <elapsed_ms>` + a `# setup` row | `../nfs-op-class-analyse.py <dir>/*.log` |
| `opclass-20261022-v2-fixedorder.tar.gz` | same probe, **fixed order**, 16:37–17:20 EDT (collected 17:10, so it holds fewer rows than it ran) | same | same |

⭐ **Keep all three: two rotated runs are what makes the results readable, and the fixed-order
one is the control's control.** The CREATE effects replicate across v3rot and v4 at p<0.0001;
every effect between p=0.003 and p=0.05 failed to replicate and one flipped direction, which
is the measured noise floor of this instrument at ~440 rounds and the reason the hot-inode
claim is RETRACTED. ⛔ Do not pool the runs or quote any p50 across them —
`../nfs-op-class-results.md` says which columns transfer and which do not.

⭐ **The GETATTR run carries its own positive control and that is why its null is readable.**
`getattr_hot` vs `getattr_static` is 3/3 discordant — six events, which alone would be an
UNINFORMATIVE quiet instrument rather than a null. `getattr_cold` vs `getattr_static` is 20/2
in the same window on a target in the same directory, which proves the instrument could
resolve a difference and did not find one. ⛔ Re-analyse the three arms TOGETHER; the null
means nothing without the cold arm beside it.

Arm B of the A/B (a31 PAUSED) is a different day and is not here yet.
