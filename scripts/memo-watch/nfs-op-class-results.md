# Which NFS operation class pays the /mnt/nas stall tax

**Measured 2026-10-22, six hosts (gpu1 office server1 server3 server4 server5), 2,211 samples
over 17:26:16–18:15:33 EDT.** Probe `nfs-op-class-probe`, label `20261022-v3rot`, rotating order.
Analyser `nfs-op-class-analyse.py`. Raw logs preserved in `nfs-latency-logs/`.

## The question

Arm A of the latency A/B (`nfs-latency-probe`: SETATTR on an existing per-host file) saw
**65.2% of minutes stalled**; `cluster`'s `nas-write-tail-sample` saw **87.3%** over the same
window. Cadence and the stall definition were already matched, and `cluster` named a confound
inside its own figure. The remaining difference is the **operation**: theirs is CREATE + UNLINK
of a `$$`-suffixed name and writes no data; ours is a SETATTR on an inode that already exists.

## Answer — the residual is the operation, and it is CREATE

| op | n | p50 ms | p95 ms | max ms | %>1s | % of wall | 60s floor |
|---|---|---|---|---|---|---|---|
| `setattr_hot` (one constantly-rewritten inode) | 443 | 11 | 8,112 | 18,585 | 18.7% | 2.5% | 0 |
| `setattr_cold` (pool of 180, revisited ~every 180 rounds) | 443 | 13 | 6,176 | 54,685 | 26.6% | 3.3% | 0 |
| `create_own` (fresh name in this host's own dir) | 443 | 3,131 | 71,568 | 107,752 | 54.6% | 35.8% | 26 |
| `create_shared` (fresh name in the dir all six share) | 440 | 70 | 78,798 | 113,136 | 38.9% | 30.1% | 33 |
| `datawrite` (4 KiB `conv=fsync` to an existing file) | 442 | 13 | 5,621 | 50,764 | 20.1% | 2.1% | 0 |

**Paired within a round** (McNemar on discordant rounds, stall = >1,000 ms, 439 rounds):

| A vs B | A only | B only | chi2 | p |
|---|---|---|---|---|
| `setattr_cold` vs `create_own` | 41 | **165** | 73.44 | <0.0001 |
| `create_own` vs `create_shared` | **128** | 60 | 23.88 | <0.0001 |
| `setattr_hot` vs `setattr_cold` | 50 | **85** | 8.56 | 0.0034 |
| `setattr_cold` vs `datawrite` | **81** | 56 | 4.20 | 0.040 |
| `create_own` vs `datawrite` | **189** | 40 | 95.65 | <0.0001 |

⇒ **A directory mutation is an order of magnitude dearer than an inode mutation on this mount.**
`create_own` consumed **35.8% of the window's wall clock** against `setattr_hot`'s **2.5%**, and in
165 rounds the create stalled where the setattr beside it, two seconds away, did not. That is the
arm-A/`cluster` residual, with the right sign and a paired test. **It is not necessary to invoke a
second cause for the 65.2% vs 87.3% gap.**

⚠️ **Only the paired columns travel between runs. The magnitudes do not.** The same probe in a
fixed order 16 minutes earlier read `setattr_hot` at p50 510 ms, not 11 ms — the mount was simply
in a worse state. Every p50 here is a statement about 17:26–18:15 and nothing else.

⚠️ **Every magnitude is a FLOOR.** `timeout 60` cannot interrupt uninterruptible sleep; samples up
to 155,892 ms have been recorded against a 60-second bound.

## The data write is not where the tax is

`datawrite` is a 4 KiB write with `conv=fsync` to a file that already exists, and it is the
**cheapest op in the set by time fraction (2.1%)** and the **only op whose conditional stall rate
falls when its neighbour stalled** (0.81x against 1.26–1.76x for the rest). The payload is not the
problem; the namespace is. This also closes out the term that was carried only because it was
nearly free to carry.

## ⛔ The directory-contention hypothesis is DISCONFIRMED, not merely unproven

The hypothesis was that a directory **six hosts** mutate would be dearer than one **this host alone**
mutates. It is the other way round, by p<0.0001, and **it survived the control built specifically to
kill it**: the fixed-order run read the same direction, the rotating-order run reads it again.

⭐ **A rival explanation that is still standing is not the same as a finding.** What rotation ruled
out was POSITION. The *cause* of the own-vs-shared gap is unsettled, and the plausible reading now
runs opposite to the original one: the shared directory is touched six times as often, so it may
simply stay warm in the server's cache while each per-host directory goes cold between visits —
**contention and cache warmth push in opposite directions and warmth appears to win.** That is a
hypothesis. It has not been measured.

**DISCRIMINATOR:** add a third create target — a per-host directory this host touches with nothing
else. If idle-own ≈ shared, the term is self-inflicted heat on `$OWN`. If idle-own ≈ own, the term
is visit frequency, i.e. the warmth reading.

⚠️ **The extreme tail does NOT follow the body, and is not settled either way.** `create_shared`
hit the 60-second floor more often (33 vs 26) and has the larger p95 and max — but paired, that is
31 vs 21 discordant rounds, **chi2 1.56, p=0.21**. Do not report the shared directory as worse at
the tail; the sample does not support it.

## ⛔ Hot inode vs cold inode: the earlier NULL was an artifact of the fixed order

The fixed-order run measured `setattr_hot` vs `setattr_cold` at **p=0.81** — a clean null, which
was reported as "no inode-heat effect". Under rotation the same comparison is **p=0.0034**, and
**the COLD inode is the dearer one** (85 discordant rounds to 50).

Why the null: in a fixed order `setattr_hot` was always position 0 and `setattr_cold` always
position 1, and the order check shows they carried systematically different position penalties
(lift 1.06x vs 1.81x). A position penalty on hot plus a coldness penalty on cold summed to zero
difference. **Two real effects cancelled and the result read as the absence of both.**

⇒ The direction is now the one the physics predicts: a cold inode needs a round trip, a hot one
may not. ⛔ **And it is the opposite of what the memo-watch heartbeat alert needs.** That alert's
standing guess is that the heartbeat file is slow to `stat` *because* it is constantly rewritten.
Nothing here supports that, and the one adjacent thing measured points the other way. It remains a
GETATTR-vs-SETATTR extrapolation either way; the discriminator is still a mount with the caching
options removed.

## ⛔ The round-pairing was wrong for a rotated log, and it failed quietly

`nfs-op-class-analyse.py` opened a new round whenever it saw `setattr_hot`. That is a round
boundary **only in the fixed-order probe**. Under rotation `setattr_hot` sits at position `(5-r)%5`,
so the gap between two of its appearances runs 9 samples, then 4, then 4 — the "rounds" it built
held some ops twice (the second silently overwriting the first) and others not at all.

MEASURED on these logs: it paired **262–354 rounds out of 439**, and compared ops from *different*
rounds — which is the exact confound McNemar is here to avoid. Every p moved; none of the
directions did.

⇒ A round is now **five consecutive samples required to hold five distinct ops**. That is correct
for both probes, it reports the order it detected, and it validates itself — a misaligned log
cannot produce five distinct ops and is **named and excluded, never absorbed as agreement**.
Re-running the fixed-order logs through the corrected code reproduces them as `FIXED, 189 rounds`.

⭐ **A helper derived from the shape of the data is a silent dependency on that shape.** The fix to
the probe (rotating the order) broke an assumption in the analyser that nothing recorded, and the
only symptom was round counts that looked plausible.
