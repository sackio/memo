# /mnt/nas write-latency arms — results

Instrument: `nfs-latency-probe` (bounded `touch` on `/mnt/nas/data/memo-watch`, BOUND=60s,
CADENCE=2s serial). Analysis: `nfs-latency-episodes.py`.
⛔ BOUND and CADENCE are a contract across arms. A changed bound looks exactly like a
changed tail.

---

## Arm A — a31 RUNNING, 2026-10-22 09:29:05 → 16:00:01 EDT

Six hosts, all collected, one start header and one ENDED marker each (no duplicate
probers). 37,637 probes over 2,345.7 host-minutes.

| host | probes | span_m | episodes | ep/min | % wall stalled | p50 ms | p95 ms | max ms | mean ep s | timed out |
|---|---|---|---|---|---|---|---|---|---|---|
| gpu1    | 6516 | 390.9 | 1081 | 2.77 | 42.0 |  7 |  9030 | 154699 |  9.1 | 13 |
| office  | 6393 | 391.0 | 1064 | 2.72 | 42.9 | 18 |  9265 | 155828 |  9.5 | 14 |
| server1 | 6491 | 390.9 | 1076 | 2.75 | 42.0 |  8 |  9026 | 155822 |  9.2 | 13 |
| server3 | 5820 | 391.0 | 1002 | 2.56 | 47.8 | 23 | 10666 | 146812 | 11.2 | 23 |
| server4 | 6046 | 390.9 | 1020 | 2.61 | 41.5 |  7 |  9346 | 155892 |  9.5 | 14 |
| server5 | 6371 | 391.0 | 1047 | 2.68 | 42.8 | 47 |  9366 | 145874 |  9.6 | 14 |

**Pooled: 6,290 episodes, 2.68/min, TIME FRACTION STALLED 43.2%.**
Episode duration: p50 5.6s, p95 28.8s, max 292.2s, mean 9.7s.

⭐ **All six hosts land in 2.56–2.77 ep/min and 41.5–47.8% of wall clock.** gpu1 and
server1 share no controller or chassis with server4, so the degradation is in the shared
NAS path, not in any one client. server4 is not an outlier on this instrument.

⚠️ **Every magnitude is a FLOOR.** `timeout 60` returned samples of up to 155,892 ms:
SIGTERM cannot interrupt uninterruptible sleep. The 13–23 "timed out" rows per host are
rc≠0 and their true latency is unknown and larger.

⛔ **This is a baseline, not a comparison.** Arm B (a31 PAUSED) must run the same
09:29–16:00 clock window on another day — a market-hours-only pause makes a31 collinear
with time of day, so the arms are only separable on a matched window (design: `cluster`).

⚠️ Against the morning 2026-10-22 09:12–09:15 A/B (RUNNING 1.83/min, 26.4%; STOPPED
1.87/min, 31.3%): arm A is worse on both. Do not read that as drift — the morning arms
spanned about three minutes each, far too short for a phenomenon whose median episode is
5.6s and whose p95 is 28.8s.

### ⛔⛔ Cadence sensitivity — measured, and it invalidates pooling across instruments

`nfs-latency-episodes.py --cadence-control` thins this same 2s series to one sample per
minute and recomputes:

| quantity | at 2s cadence | same data at 1/min | direction |
|---|---|---|---|
| episodes/min | 2.56–2.77 | 0.21–0.22 | **12× lower** |
| % of minutes containing a stall | 94.8–95.8% | 55.1–61.7% | **~39 pp lower** |
| time fraction stalled | 41.5–47.8% | (unchanged by construction) | **stable** |

An episode rate is **ceilinged by cadence**: a per-minute sampler needs a clean sample
between two episodes, so it can never report above 0.50/min, and arm A's median episode is
5.6s — an order of magnitude below that resolution.

⇒ **Only the time fraction pools across instruments.** The episode rate and the
minute-indicator are both properties of the sampler as much as of the NAS, and they move in
opposite directions with cadence, so averaging them across two samplers is meaningless.
This corrects the standing instruction to pool episodes "because they are defined on the
latency series, not on the cadence" — measured here, they are not.

### Residual against `cluster`'s independent sampler — ✅ RECONCILED 2026-10-22

`cluster`'s `bin/nas-write-tail-sample` reported 87.3% of minutes stalled (union over 4
hosts, 1/min, same window). Matching its cadence and its definition on this dataset gives
**65.2%** (union over the same 4 hosts, thinned to 1/min, 253 of 388 minutes).

The 22-point residual is **not** statistical: cadence and definition are matched. It is the
probe OPERATION, and the two operations are further apart than they look:

| | this instrument | `nas-write-tail-sample` |
|---|---|---|
| op | `touch` an existing per-host file | `touch` a new `$$`-suffixed name, then `rm` it |
| syscall class | SETATTR on one inode | CREATE + UNLINK, **mutating the parent directory** |
| filename | stable per host | new every run |
| contention | per-host files | all 4 hosts in **one shared directory** |

⛔ **Both are metadata operations; they are not the same class.** CREATE and UNLINK mutate
the parent directory and need directory-level serialisation on the server, where SETATTR
touches a single inode. On a degraded filesystem the directory-modifying op is far more
expensive. (Read from `cluster`'s script by `cluster`, 2026-10-22, correcting an inference
of mine that its probe performed a data write — it writes no data at all.)

⚠️ `cluster` also names a confound inside its own figure that inflates it: its four hosts
create and unlink in the SAME directory every minute, so they contend on one parent
directory lock and that contention is part of the 87.3%. That number is an upper bound on
the shared-directory case, not a clean per-client reading.

⭐ DISCRIMINATOR, restated: **SETATTR-on-existing vs CREATE+UNLINK-of-a-new-name**, same
directory, same host, same window — not touch-vs-touch-plus-write. `cluster` removed its
sampler's cron at 16:03, so this instrument is the one that could run it at 2s cadence.

✅ **IT RAN TWICE, AND THE RESIDUAL IS THE OPERATION.** `nfs-op-class-probe`, six hosts,
rotating order so no op owns a position: 2,211 samples 17:26–18:15 and 2,680 samples
18:34–19:39 the same day. Paired within a round, CREATE stalled where the SETATTR two seconds
beside it did not in **165 rounds against 41**, then **189 against 37** — McNemar p<0.0001 in
both, taking 35.8% and 27.6% of the window's wall clock against 2.5% and 2.4%. The 22-point
gap needs no second cause.
Figures, the controls, and what is still open: **`nfs-op-class-results.md`**.

⛔ **Two things in the section above did not survive those runs.** (1) The shared-directory
contention `cluster` names as inflating its own 87.3% is **not measurable at all** here: a
six-host shared directory and a per-host directory nothing else touches are indistinguishable
(85 vs 94 discordant rounds, p=0.55). The per-host directory the probe itself works in is the
expensive one, and that is self-inflicted heat, not contention. Whatever inflates their
figure, it is not four hosts sharing a parent. (2) A `datawrite` term was carried as the
obvious next question and is **closed**: a 4 KiB fsync'd write is the cheapest op measured.

⛔ **Still do not merge the two series or quote one figure for the other** — that was never
about the residual. It is the cadence result above: a probe-level stall rate is ceilinged by
cadence and the minute-indicator moves the other way. Only the DURATION RATIO pools.
