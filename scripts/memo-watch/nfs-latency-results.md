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

## Arm B — a31 PAUSED, 2026-10-23 09:24 → 16:00 EDT (RUNNING at time of writing)

Logs: `~/.memo-watch/nfs-latency/<host>.B-a31-paused.log`, six hosts, BOUND=60
CADENCE=2 serial — identical to arm A. Verified at launch: one prober per host,
3–5 log lines per 10s, exactly one `# arm` header each.

**a31 state is MEASURED, not relayed.** `system`, 2026-10-23 09:22: SIGSTOP on
PIDs 8489/6425 at 04:08, state `T` confirmed at 08:07, paused until 16:05.
⚠️ The probes were launched 09:22 labelled `B-a31-unconfirmed`, killed by PID and
relaunched 09:24 once `system` answered. Nothing from the unconfirmed label
survives; its logs were removed before the 09:29 comparison window opened.
⭐ The label was not guessed in the meantime. "Paused", "running" and
"unconfirmed" are three states, and collapsing the third into either of the
first two is how an A/B gets silently corrupted — the arm would have read as
clean evidence either way.

### ⛔ DO NOT READ B-vs-A AS AN a31 EFFECT

Three things differ between the arms, not one. `system` supplied this list on
2026-10-23 09:22 and **corrected mine**: of the four NAS changes I proposed as
confounds, two do not apply, and two I did not know about are live.

| change | live in the 09:29–16:00 window? |
|---|---|
| a31 paused | ✅ yes — the intended variable |
| volume1 → `noatime` (06:02:49) | ✅ yes |
| tradedesk moves reading volume1 → overflow | ✅ yes, new since arm A |
| server4 db → server6 zfs send | ⚠️ server4-LOCAL, not NAS |
| volume1 space freed | ❌ no — snapshots still hold it, first release Mon 03:00 |
| 10-17 salvage rsync killed (04:34) | ❌ no IO change — it was already SIGSTOPped |
| files rsync 03:00 → 01:00 (task 14) | ❌ outside the window |

⇒ Record the result as **"a31 paused + noatime + desk reads"**. If arm B comes
back much cleaner than arm A's 17.22% of writes >1s (n=633), *"we turned on
noatime"* explains it at least as well as *"a31 stopped"*, and the design cannot
separate them.

⚠️ **This is why the confound list is written down at LAUNCH, not at analysis.**
By the time the numbers exist there is a result to explain, and a confound
recalled afterwards looks like an excuse for a number somebody does not like.

✅ ARCHIVED 2026-10-23 16:10 to
`scripts/memo-watch/nfs-latency-logs/armB-a31-paused-20261023.tar.gz` (238 KB),
six logs, 9,998–10,115 lines each, round-trip verified out of the tarball.

### Arm B result — 2026-10-23, collected 16:10

Both arms re-run through the same code path. Arm B trimmed to arm A's 09:29
start so the windows are paired to within 0.2 host-minutes; untrimmed B gives
the identical 13.4%, so the 5-minute head start is immaterial.

| | arm A (a31 running) | arm B (a31 paused + noatime + desk reads) |
|---|---|---|
| **TIME FRACTION STALLED** (the only poolable one) | **43.2%** | **13.4%** |
| host-minutes | 2345.6 | 2345.8 |
| episodes | 6,290 | 3,947 |
| episode p50 / p95 / max | 5.6s / 28.8s / 292.2s | 3.4s / 13.2s / 40.3s |
| `timeout 60` hits | 91 | **0** |
| probes landed | ~6,300/host | ~10,050/host |

⭐ The tail moved more than the headline: max episode 292s → 40s, and the 91
uninterruptible-sleep timeouts that made arm A's time fraction a FLOOR are gone
entirely, so arm B's 13.4% is a tighter figure than arm A's 43.2% rather than a
like-for-like one. The direction of that bias makes the gap a floor too.

⚠️ The probe count ROSE 6.3k → 10.1k per host. That is not extra load: a serial
sampler spends real time inside each stall, so fewer stalls buys more samples.
It is a consequence of the result, not an input to it.

⛔⛔ **THIS IS NOT AN a31 EFFECT AND THE DESIGN CANNOT MAKE IT ONE.** Per the
confound table written at launch above, three things changed together. A 3.2x
drop is exactly as consistent with `noatime` as with a31 stopping, and the
tradedesk overflow move is a third live candidate. Reported to `cluster`
2026-10-23 as "a31 paused + noatime + desk reads", not as an a31 finding.

⭐ **AN ARM C IS AVAILABLE AND IT IS TIME-LIMITED.** a31 resumed 16:05 while
`noatime` stays on permanently, so a third window — a31 RUNNING, noatime ON —
separates the two leading candidates with no new configuration at all:
  - back near arm A's 43% ⇒ a31 is the driver
  - still near arm B's 13% ⇒ noatime (or the desk reads) is
⚠️ It decays: every further NAS change folds into the comparison, and the
longer the gap the weaker the pairing against arms A and B.

### Arm C — LAUNCHED AND KILLED 2026-10-23 16:13, relaunch Mon 2026-10-26 09:29

`cluster` authorised arm C at 16:12 ("same probes, bound=60, cadence=2, same six
hosts, same length as arm A"). It was launched 16:13:39 for 391 minutes and killed
at 16:15, 90 seconds in.

⛔ **THE INSTRUCTION AND THE INSTRUMENT DISAGREED, AND THE INSTRUMENT WAS RIGHT.**
"Same LENGTH as arm A" gives 16:13–22:44. Arms A and B both ran **09:29–16:00**. The
probe script's own header carries `cluster`'s rule from 10-22: arms must run on the
SAME CLOCK WINDOW on different days, *because a market-hours-only a31 pause makes a31
perfectly collinear with time of day*. A 16:13–22:44 arm is largely post-market and
reintroduces exactly the collinearity the instrument was built to exclude — against
two market-hours arms. Flagged to `cluster` 16:14; they chose to kill and repair:
*"You're right, my mistake. Option 2: kill it now."*

⭐ **LENGTH AND PHASE ARE DIFFERENT PROPERTIES OF A WINDOW, AND ONLY ONE OF THEM WAS
SPECIFIED.** Matching duration feels like matching the window and is not: a 391-minute
block is comparable to another 391-minute block only if both sit at the same place in
the daily load cycle. The check that caught it was reading the instrument's own
contract before obeying an instruction about it.

⚠️ Saturday and Sunday are off-market, so the next *paired* window is Monday.
Armed in the broker as `memo`/`nfs-latency/arm-C-launch`, one-shot, Mon 2026-10-26
09:27 EDT (schedule `d719c6ec`).

⛔ The 90-second fragment was **renamed, not deleted**, on all six hosts to
`<host>.C-a31-running-noatime.log.aborted-20261023-1613` — the probe APPENDS to its
log, so a surviving fragment under the live name would silently prepend 90 seconds of
Friday evening onto Monday's series. Deleting it would have worked too; renaming also
leaves the evidence that the abort happened.

⭐ Probes were killed BY PID, re-read from `ps` immediately before the kill. The six
zero-byte `.lat.<host>` files the probe normally removes on exit were left behind —
`kill` skips the `rm` that sits after the sampling loop — and were cleaned by hand from
`/mnt/nas/data/memo-watch/`, which is the directory the heartbeat and its own static
control live in.

⛔ AT MONDAY'S LAUNCH: write EVERY NAS change between 2026-10-23 16:14 and the launch
into the header as explicit confounds (`cluster`, 16:14). Ask `cluster` and `system`
for that list — do not infer it.
