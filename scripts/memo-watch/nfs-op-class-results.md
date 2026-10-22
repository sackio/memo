# Which NFS operation class pays the /mnt/nas stall tax

Two rotating-order runs on 2026-10-22, six hosts each (gpu1 office server1 server3 server4
server5), probe `nfs-op-class-probe`, analyser `nfs-op-class-analyse.py`, raw logs in
`nfs-latency-logs/`.

| run | label | window | ops | samples | rounds |
|---|---|---|---|---|---|
| **v3rot** | `20261022-v3rot` | 17:26–18:15 EDT | 5 | 2,211 | 439 |
| **v4** | `20261022-v4idle` | 18:34–19:39 EDT | 6 (adds `create_idle`) | 2,680 | 444 |

A third run, `20261022-v2`, used a FIXED order and is kept only as the control's control.

## The question

Arm A of the latency A/B (`nfs-latency-probe`: SETATTR on an existing per-host file) saw
**65.2% of minutes stalled**; `cluster`'s `nas-write-tail-sample` saw **87.3%** over the same
window, with cadence and the stall definition already matched. Their op is CREATE + UNLINK of
a `$$`-suffixed name and writes no data; ours is a SETATTR on an inode that already exists.

## Answer — the residual is the operation, and it is CREATE. Replicated.

Paired within a round (McNemar on discordant rounds, stall = >1,000 ms). The ops are 2
seconds apart inside a round and see the same conditions, which is the only comparison that
is not dominated by episode clustering.

| A vs B | v3rot (439 rounds) | v4 (444 rounds) | |
|---|---|---|---|
| `setattr_cold` vs `create_own` | 41 / **165**, p<0.0001 | 37 / **189**, p<0.0001 | ✅ replicated |
| `create_own` vs `create_shared` | **128** / 60, p<0.0001 | **135** / 52, p<0.0001 | ✅ replicated |
| `create_own` vs `datawrite` | **189** / 40, p<0.0001 | **186** / 31, p<0.0001 | ✅ replicated |
| `create_idle` vs `create_own` | — | 48 / **140**, p<0.0001 | v4 only |
| `create_idle` vs `create_shared` | — | 85 / 94, **p=0.55** | v4 only |
| `setattr_hot` vs `setattr_cold` | 50 / 85, p=0.0034 | 79 / 68, **p=0.41** | ⛔ did NOT replicate, direction flipped |
| `setattr_cold` vs `datawrite` | 81 / 56, p=0.040 | 86 / 83, **p=0.88** | ⛔ did NOT replicate |

⇒ **A directory mutation is an order of magnitude dearer than an inode mutation on this
mount.** In v4 `create_own` took 27.6% of the window's wall clock against `setattr_hot`'s
2.4%, and in 189 rounds the create stalled where the setattr two seconds beside it did not.
**That is the arm-A/`cluster` residual. It needs no second cause.**

⚠️ **Only the paired columns travel between runs. The magnitudes do not.** The same probe read
`setattr_hot` at p50 510 ms, 11 ms and 112 ms in three windows on one afternoon. Every p50 is
a statement about its hour.
⚠️ **Every magnitude is a FLOOR** — `timeout 60` cannot interrupt uninterruptible sleep, and
v4 recorded a 201,093 ms sample against a 60-second bound.

## ⭐⭐ The instrument's noise floor, measured rather than assumed

Two runs, same probe, same hosts, same design, ~440 rounds each, 65 minutes apart. **Every
effect at p<0.0001 replicated with the same direction. Both effects between p=0.003 and
p=0.05 failed to replicate, and one flipped direction.**

⇒ On this instrument, at this sample size, **a p of 0.003 is not yet a finding.** Treat
anything short of p<0.001 that has not run twice as a hypothesis. This is why the hot-inode
claim below is retracted rather than defended.

## ⛔ RETRACTED: there is no established hot-inode vs cold-inode effect

The fixed-order run measured `setattr_hot` vs `setattr_cold` at p=0.81 and that null was
reported as "no inode-heat effect". v3rot measured p=0.0034 with the COLD inode dearer, and
that was reported here as the fixed-order null being an artifact — **the direction the physics
predicts, which is exactly what made it easy to believe.** v4 measures p=0.41 with the
direction flipped.

**Three runs, three answers. Nothing is established.** The honest state is that any
hot/cold difference is below what this instrument resolves at ~440 rounds. ⛔ Do not cite the
v3rot figure; it is in the table above only so nobody rediscovers it as news.

⭐ The transferable part: **a result that matches the mechanism you expected gets less scrutiny
than one that does not.** The v2 null was challenged and overturned because it looked wrong;
the v3rot finding was accepted because it looked right. The second needed the same second run
the first one got.

## ⛔ DISCONFIRMED: cross-host directory contention costs nothing measurable here

The hypothesis was that a directory **six hosts** mutate would be dearer than one **this host
alone** mutates. `create_shared` (six hosts creating and unlinking in one directory) and
`create_idle` (a per-host directory nothing else touches) are **indistinguishable: 85 vs 94
discordant rounds, p=0.55.** Six-way contention on one parent directory is not visible at all
at this cadence.

`cluster` had named exactly this contention as the confound inflating their own 87.3%.
**Whatever inflates that figure, it is not that.**

## ✅ SETTLED: the own-vs-shared gap is self-inflicted heat, not visit frequency

v3rot found `create_own` dearer than `create_shared` — backwards from the contention
hypothesis — and that survived rotation, so it was not position. Two rivals remained: the
shared directory is visited six times as often and might stay warm in the server's cache
(visit frequency), or `$OWN` is penalised for being the directory this probe hammers
(self-inflicted heat). `create_idle` separates them, and it is a clean answer:

- `create_idle` ≈ `create_shared` (p=0.55) — an idle one-host directory is as cheap as the
  six-host one, so **visit frequency buys nothing**.
- `create_idle` ≪ `create_own` (48 vs 140, p<0.0001) — so **`$OWN` is the outlier**, and the
  only thing distinguishing it from `$IDLE` is that four of the six ops work inside it.

⇒ **The cost attaches to a directory whose CHILDREN are being actively modified**, even though
modifying a child is not a modification of the parent. `$OWN` holds `.hot` (rewritten every
round), `.dw` (data-written every round) and `pool/` (180 files, one touched every round);
`$IDLE` holds nothing but the transient create and unlink.

⚠️ **The MECHANISM is a hypothesis, the LOCATION is measured.** Plausible: attribute- or
dentry-cache invalidation on the parent propagating from child updates. Not measured.
DISCRIMINATOR: split `$OWN`'s load — one directory whose children get SETATTR only, one whose
child gets the data write only — and see which carries the penalty.

⭐ **This is why `$OWN` was never the "uncontended control" the first version of the probe
called it.** A directory that the instrument itself is busy in is not a quiet baseline, and
the label said it was.

## The data write is not where the tax is

A 4 KiB write with `conv=fsync` to an existing file: 2.3% of wall clock in v4, p50 29 ms, and
`create_own` beat it 186 to 31 in paired rounds across both runs. **The payload is not the
problem; the namespace is.**

## ⛔ Two analyser defects, both of which failed quietly

**The round-pairing was wrong for a rotated log.** It opened a new round whenever it saw
`setattr_hot`, which is a round boundary only in the FIXED-order probe. Under rotation
`setattr_hot` sits at position `(5-r)%5`, so the gap between its appearances runs 9 samples,
then 4, then 4 — the "rounds" it built held some ops twice (the second silently overwriting
the first) and others not at all. It paired 262–354 rounds out of 439 and compared ops from
*different* rounds, the exact confound McNemar is there to avoid. Every p moved when fixed;
no direction did. A round is now **N consecutive samples required to hold N distinct ops**,
self-validating, with the detected order printed.

**The op set and round length were hardcoded to five.** The probe then grew a sixth op. A
hardcoded five would have chopped every six-op round into misaligned blocks and reported the
result with the same confidence. Both are now derived from the log, and a pair naming an op
the log does not contain is **skipped and named** — "the comparison is absent" and "the
comparison found nothing" are different results.

⭐ **Fixing the probe broke an assumption in the analyser that nothing recorded.** A helper
derived from the shape of the data is a silent dependency on that shape.

## The one invariant, now asserted

The ops are serial on one timeline, so their stalled-time shares divide one wall clock and
cannot sum past 100%. The first version of this analyser printed **262%** by dividing by 4 to
give each op "its own" quarter. The sum is now printed every run and refused when violated
(v3rot 73.8%, v4 72.8%), naming what it does *not* invalidate: `%>1s` and every paired result
are unaffected, because they do not use the span.
