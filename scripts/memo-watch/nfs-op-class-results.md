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

---

## 2026-10-22 20:30 — two results about the HEARTBEAT ALERT itself

### 1. ⛔ RETRACTED 21:25 THE SAME EVENING — I TESTED THE WRONG VARIABLE

**What follows in this subsection was my conclusion at 20:30 and it is WRONG. Read
section 3 below for what replaced it.** I tested whether MOUNT-WIDE stalls cluster at
:20 — they do not, and that part stands. But the claim I drew from it, that the
heartbeat's :20 pattern is therefore an artifact, does not follow: the heartbeat-check
stats ONE SPECIFIC FILE, and arm A measured writes to the directory. Different
operation, different target, different question.

⭐ **THE LESSON, which is the durable part:** a null is only as good as the match
between what you measured and what you claimed. I refuted "minute 20 is special on
this mount" and reported it as "the :20 pattern is not real" — a strictly broader
claim about a target my instrument never touched. A null stated wider than its
instrument is the same error as an over-claimed positive, and it is harder to catch,
because a null feels like the conservative direction.

### 1 (as originally written). The ":20 past the hour" pattern — mount-wide test

All six of 2026-10-22's `heartbeat UNREADABLE` alerts landed at **:20**, against a
check that runs `*/20` (so :00, :20, :40). Six for six looks like p≈0.0014 and looks
like a scheduled job.

⛔ **It does not replicate.** Tested on arm A — an independent instrument, 37,637
samples, 6 hosts, 09:29–16:00, the same hours — with
`nfs-minute-of-hour.py`: **minute 20 ranks 24th of 60** at a >1s threshold and
**28th of 60** at >10s (chi² 1.84 and 0.22).

⭐ **Why it looked real, and the rule that generalises:** the pattern was found by
looking at the data. A p computed on a pattern you went hunting for — over an
unbounded set of patterns you might equally have noticed — is not a p. With 60
candidate minutes something always wins. ⇒ **The rank is the answer, not the p.**
And no job in any of the six crontabs fires hourly at :20 only; the `*/20` jobs all
hit :00 and :40 as well, so there was never a mechanism either.

### 2. ⚠️ EVERY ALERT TODAY FIRED WHILE I WAS PROBING — the apparatus may be the cause

The UNREADABLE detector shipped `bb246c1` on **2026-10-20** and ran clean through
the 21st — a genuine negative, not a missing detector (the `fresh:` lines prove it
was exercising).

| when | alerts | what was running |
|---|---|---|
| 10-20 (post-`bb246c1`), 10-21 | **0** | nothing of mine |
| 13:20, 14:20, 15:20 | 3 | arm A, 6 hosts, 09:29–16:00 |
| 17:20, 18:20 | 2 | op-class v3rot + v4, 16:37–19:39 |
| 20:20 | 1 | GETATTR probe, from 19:54 |

arm A writes into `/mnt/nas/data/memo-watch` — **the heartbeat's own directory** —
from six hosts every ~3.6s. Base write-stall rate measured there: **>1s in 95.1% of
host-minutes, >10s in 62.9%.**

⚠️ **Not directory contention** — v4 disconfirmed that (`create_idle` ≈
`create_shared`, p=0.55). If this is my load, it is **mount-wide**, not local to the
directory.
⛔ **Observational and confounded**: one probing day against two quiet days. This is
a hypothesis with a clean test, not a finding.

⭐ **PRE-REGISTERED DISCRIMINATOR, resolves the same night, costs nothing:** the
GETATTR probe ends 21:48 and nothing else of mine touches that mount afterward.
**Clean checks at 22:00 onward ⇒ the load hypothesis survives. An UNREADABLE at
22:20 or 22:40 with no probe running ⇒ it is dead and the mount does this alone.**
⚠️ The 22:05 credit cycle is not NAS-silent, so the window is good, not perfect.

⛔ **Until this resolves, leave the `heartbeat-check` alert text alone.** It
currently refuses to name a cause, which is the correct state.

### 3. 2026-10-22 21:25 — the :20 effect IS real, and the variable is the TARGET'S AGE

Measured over the last 300 heartbeat-checks (~2.8 days), joined to cronguard run
times and anchored from the END (1464 runs vs 1411 status lines — the join is only
sound backwards):

| slot | checks | UNREADABLE | mean heartbeat age when readable |
|---|---|---|---|
| :00 | 99 | **0** | 1,315s |
| :20 | 100 | **7** | **911s** |
| :40 | 99 | **0** | 1,667s |

Every failure is in the :20 slot. P(all 7 land in one given third) ≈ 5e-4.

⭐ **:20 IS NOT A CLOCK FACT, IT IS AN AGE FACT.** memo-watch `run` is cron'd
`11,41` and takes **23 seconds** (`run.out` is truncated per run: start 21:11:01,
mtime 21:11:24 — so an earlier guess that a long run overlaps the :20 check is also
dead). The heartbeat is written once per run, at ~:11:24 and ~:41:24. The three
check slots therefore stat a target of systematically different age:

- **:20 → youngest** (~8.6 min nominal) ← the only slot that ever fails
- :00 → middle (~18.6 min)
- :40 → oldest (~28.6 min)

The slot means above preserve that exact ordering. The mount is nfs4 with
**acregmin=acregmax=600**, and 600s falls between the :20 age and the other two.
⇒ This is the first evidence today that FAVOURS the attribute-cache guess, and it
sharpens it: the cost is not "a file whose mtime keeps changing", it is **a stat
landing within acregmax of the target's last write**.

⚠️ **Not proof, and two honest holes.** (a) The 911s mean is above 600s — but it
averages only the READABLE checks, and if failures are the young ones the mean is
biased upward by exactly the cases that matter. (b) Still observational.

⚠️ **Mount-wide stall rates do NOT explain it, which is why section 1 was wrong but
its measurements were not.** On the same mount: GETATTR >1s is 19/3,500 ≈ 0.5% with
minute 20 unremarkable (2/59, behind minutes 15 and 05); write stalls show no minute
effect at all. The heartbeat file fails at 25s on 7% of its :20 checks. The file is
behaving nothing like its neighbours, and only a per-target variable can do that.

⭐ **THE COMBINED MODEL THAT FITS EVERYTHING OBSERVED TODAY** — and it needs both
terms, which is why each one alone looked wrong:
- **WHY :20 AND NEVER :00/:40** → the target is youngest there (this section).
- **WHY TODAY AND NOT 10-20 OR 10-21** → my own probe load (section 2). All 7 fired
  inside probe windows; the detector was live and silent for two probe-free days.
⇒ A standing vulnerability at :20, tipped past the 25s timeout only when the mount
is already loaded.

⭐ **THE DIRECT TEST WAS ALREADY RUNNING BEFORE ANY OF THIS WAS WORKED OUT.** The
GETATTR probe pairs `getattr_hot` (touched every round, age ~seconds) against
`getattr_static` (written once at setup, age ~hours) **in the same directory**, which
is precisely recently-modified vs not. If the age mechanism is real, hot must stall
more than static. ⚠️ Caveat: hot's age is seconds and the heartbeat's is ~510s at the
edge of the window — the probe tests deep inside the regime, not at its boundary.
⛔ And check the discordant counts before reading any p: a calm window is
uninformative, not a null.

---

## 2026-10-22 21:50 — the GETATTR run: the standing guess is DISCONFIRMED

1,574 rounds, 9,446 samples, 6 hosts, 19:54–21:48, rotating order. All three stat
targets in ONE directory. ⛔ `stat -c %Y`, never `ls -l` (that is READDIRPLUS).

| arm | what it is | p50 | p95 | >1s | max |
|---|---|---|---|---|---|
| `getattr_hot` | same file every round, **rewritten every round** by `setattr_hot` | 4ms | 23ms | 0.2% | 8,532ms |
| `getattr_static` | same file every round, **written once at setup, never again** | 4ms | 20ms | 0.2% | 1,563ms |
| `getattr_cold` | `f$((r % 180))` — **a different pool file each round** | 5ms | **213ms** | **1.3%** | **29,378ms** |

| paired, same round | discordant | p |
|---|---|---|
| `getattr_hot` vs `getattr_static` | 3 / 3 | 0.68 |
| `getattr_cold` vs `getattr_static` | **20 / 2** | 0.0003 |
| `getattr_hot` vs `getattr_cold` | 3 / **21** | 0.0005 |

### What this settles

⛔ **BEING REWRITTEN DOES NOT MAKE A FILE DEARER TO STAT.** `hot` and `static` are
indistinguishable on every statistic. The heartbeat alert's standing guess — "a file
whose mtime keeps changing forces a round trip" — **is disconfirmed for GETATTR.**

⭐ **WHAT COSTS IS CACHE RESIDENCY, NOT MUTATION.** `hot` and `static` are both stat'd
every round (~4.3s apart) so both sit inside `acregmax=600`. The pool cycles every
180 × 4.3s ≈ **775s, just past the 600s timeout**, so every `cold` stat finds an
expired entry. That accident of POOL_N makes this a clean residency contrast, and the
expired arm carries the whole tail — including a **29,378ms** maximum, which is longer
than the heartbeat-check's own 25s timeout.

⭐⭐ **WHY THE NULL IS READABLE AT ALL — THE COLD ARM IS THE POSITIVE CONTROL.** Six
discordant rounds would normally be UNINFORMATIVE, not a null; that was pre-registered
before the run. What rescues it is that the same instrument, in the same window, on
targets in the same directory, resolved `cold` vs `static` at 20/2. ⇒ The instrument
demonstrably had the power to see a difference among these three files and found none
between hot and static. **A null needs a positive control in the same window or it is
just a quiet instrument.**

### ⛔ What this does NOT settle — the :20 asymmetry survives every mechanism I tried

The heartbeat and its static sibling are **both** stat'd every 1200s, so **both** are
cache-expired at check time. Residency therefore does not explain why one times out at
25s while the other answers in 4–31ms. Three candidate mechanisms, all killed today:

1. ⛔ **"A long `run` overlaps the :20 check."** Dead: `run.out` is truncated per run,
   start 21:11:01, mtime 21:11:24 — **the run takes 23 seconds.**
2. ⛔ **"The rewritten inode is dearer."** Dead: hot ≈ static, above.
3. ⛔ **"Stale filehandle after the atomic rename."** Does not fit: the intervals
   `:40→:00` and `:00→:20` each contain exactly one rename and `:20→:40` contains
   none, which predicts :00 and :20 both bad. Observed is :20 bad, :00 clean (0/99).

⇒ **The remaining difference between :00 and :20 is only how long ago the rename
happened — 18.6 min vs 8.6 min, with `acreg*=600`s between them.** That is a
suggestive coincidence and nothing more. **Do not promote it to a cause.**

### ⚠️ A design note on the control, for when this is next touched

The controls are measured **after** the heartbeat stat has already burned its 25s
timeout, so they describe the mount ~25s later, not at the same instant. Stalls arrive
in episodes, so a post-hoc control is systematically weaker than a simultaneous one and
will tend to read clean. ⛔ NOT changed tonight — the alert text stays frozen until the
probe-load test resolves.

## 2026-10-23 02:00 — the heartbeat-write failure has a START DATE, and CREATE carries the tail again

server3 DM'd at 02:00 EDT: *"memo-watch HAS NOT RUN for 79 min"*. **That headline is false.**
The watch ran at 01:11 and 01:41, both `verdict: 0 finding(s), 0 unreadable`. What failed was
the heartbeat WRITE, so the previous file aged past the 4200s staleness threshold and server3 —
whose only channel is that file — read staleness as not-running.

⛔ **A HEARTBEAT IS A CONJUNCTION — "the job ran" AND "the job could write" — AND ITS ABSENCE
CANNOT SAY WHICH CONJUNCT FAILED.** The two have different owners: one is cron/host, the other
is storage. A monitor that reports the conjunction as if it were the first term sends the wrong
seat looking, and it does so in confident language.

### The step change, from the watch's own log

`~/.memo-watch/cron.log` on server4, 1,012 runs, 2026-09-30T22:41 → 2026-10-23T01:41:

| date | runs | heartbeat-write failures |
|---|---|---|
| 09-30 → 10-17 | 757 | **0** |
| 10-18 | 43 | 1 |
| 10-19 | 44 | 2 |
| 10-20 | 48 | 5 |
| 10-21 | 48 | 2 |
| 10-22 | 48 | **14** |
| 10-23 (to 01:41) | 4 | 2 |

**757 consecutive clean runs, then onset on 10-18 and acceleration.** This is the start date the
postmortem did not have — the 10-22 evidence I sent `system`/`agents` began at 09:29 that day and
could not distinguish "bad all along" from "newly bad."

⭐ **NOT MINE, AND THE DATES ARE THE CONTROL.** My `nfs-latency` probes ran 19:54–21:48 on 10-22
only; the first automated credit cycle started 22:05 on 10-22. The onset precedes both by four
days, and 10-18 05:11, 10-19 12:41, 10-20 12:11/16:11 and 10-21 18:11 all fall outside the
22:00–06:00 batch window. ⚠️ Tonight's 2-in-4 may well be inflated by my own credit cycle; the
10-18→10-21 onset cannot be.

### Measured directly, in the operation that actually fails

20 rounds of "create a 45-byte file, then rename it" in `/mnt/nas/data/memo-watch/`,
2026-10-23 02:04 EDT from server4, while the credit cycle was running:

| op | n | p50 | p95 | max |
|---|---|---|---|---|
| **create** | 20 | 7.6 ms | 34,811 ms | **34,811 ms** |
| rename | 20 | 0.8 ms | 3,931 ms | 3,931 ms |

**One round in twenty blocked ~35 seconds writing 45 bytes.** The guard was `timeout 20`, so that
round would have been recorded as a failure — the mechanism, reproduced on demand, in the exact
call that fails.

⭐ **This independently replicates the run-v3rot/v4 finding above: the residual is CREATE.** A
different instrument (the heartbeat's own write, not the probe), a different day, same split —
create carries the whole tail, rename is an order of magnitude cheaper. Two instruments agreeing
is worth more than either one's p-value.

### Both fixes, and why the second one exists

1. **`timeout 20` → `timeout 90`.** 20s sat *below* the mount's observed stall (29,378 ms GETATTR
   on 10-22; 34,811 ms create today), so the ceiling itself was manufacturing failures. 90s is
   above every stall measured here and far inside cronguard's 600s for a run that takes ~23s.
2. **Log the write duration on EVERY run** (`~/.memo-watch/hb-write-latency.tsv`), and on the
   **second consecutive** failure DM the truth on the same channel the false alarm uses.

⛔⛔ **FIX 1 ALONE WOULD HAVE DESTROYED THE EVIDENCE FOR THIS ENTIRE SECTION.** Raising the ceiling
converts a 35s stall into a *slow success*, which is silent — so the signal that detected the
degradation would have disappeared at exactly the moment the mount got worse, and the table above
could never have been built again. **When you raise a threshold, start recording the quantity it
was thresholding, in the same commit.** A threshold is a detector you are about to switch off.

⚠️ The DM gate is `>= 2 consecutive` on purpose: that is precisely when the 4200s threshold is
about to be crossed and the misleading alarm fires, so the correction arrives with it rather than
14 times a day.

✅ **VERIFIED 2026-10-23 05:52 — the DM path works end to end.** This note previously read "what is
NOT verified", and that is now false. Drilled by extracting the script's own `PYHB` heredoc
verbatim, running it with synthetic failure args (`2 90000 memo <latency log>`), and POSTing the
result to `$BROKER/messages` with the identical curl form: **HTTP 201, `sent:1 live:1`, delivered.**
What that establishes: the builder emits valid JSON, the broker accepts this exact schema, delivery
is live, and — the part that matters — **the last 12 latency samples render inside the alarm**, so
it arrives actionable instead of merely loud.
⛔ The drill was addressed to `memo` (myself), never a peer, and carried `[DRILL - NOT A REAL ALARM]`
in the subject plus a disclaimer above the body. `MEMO_WATCH_NO_DM=1` remains mandatory for control
runs of the *script*: a forged alarm degrades the only channel this watch has. Exercising the
payload path directly, marked, to yourself, is how you test it without forging one.
⚠️ Untested residue: only the literal subject string differs between the drill and a real fire, and
it carries no mechanism. The first real fire is still the first real fire.
⭐ A first POST attempt failed because the extracting `sed` matched an EARLIER heredoc in the same
script and produced shell text; the broker rejected it `400 missing: to, from, content` and **sent
nothing**. Worth keeping: a schema-validating endpoint turned a bad test harness into a clean
refusal rather than a malformed alarm. Anchor heredoc extraction on the unique terminator (`PYHB`),
not on a line like `import json,sys` that recurs.

### Still not mine to fix

The cause of the NAS degradation is measured here, not explained. **Why** `/mnt/nas` began
stalling on CREATE around 2026-10-18 belongs to the seat that owns the mount.

## 2026-10-23 05:45 — the new latency log vindicates itself in eight samples, and the proxy cannot see it

Eight runs of the fixed heartbeat write (45 bytes, create + rename), 02:11 → 05:41, one
every 30 min. **All eight returned `ok`. Five of the eight exceeded the OLD `timeout 20`.**

| time | result | ms | under old code |
|---|---|---|---|
| 02:11 | ok | 101 | ok |
| 02:41 | ok | 2,525 | ok |
| 03:11 | ok | 41,572 | **FAIL** |
| 03:41 | ok | 44,001 | **FAIL** |
| 04:11 | ok | 11,482 | ok |
| 04:41 | ok | 62,464 | **FAIL** |
| 05:11 | ok | 58,404 | **FAIL** |
| 05:41 | ok | 24,353 | **FAIL** |

⭐⭐ **THIS IS THE CASE FOR RECORDING THE QUANTITY, AND IT ARRIVED WITHIN HOURS.**
Had `a945ba3` raised `timeout 20`→`90` and nothing else, tonight would read as a
**perfectly clean night — 8/8 ok, zero alarms** — while the write latency rose ~600×.
The alarm would have gone silent exactly as the mount got worse. The threshold was the
only detector, and raising it without replacing it would have destroyed the evidence
series in the same commit that "fixed" the bug.

⚠️ **62,464 ms is 69% of the new 90 s ceiling.** The headroom bought this morning is
already mostly spent. If the trend continues the new guard will breach too — but it will
then fail with the CORRECT message ("could not write heartbeat"), not "HAS NOT RUN".

⛔ **THE INDEPENDENT PROXY DOES NOT CORROBORATE THIS, AND THAT IS NOT A CONTRADICTION.**
`fleet-ctx-sync` timeouts per hour tonight: 00h=40, 01h=45, 02h=12, 03h=59, 04h=55,
05h=46 — flat, and indistinguishable from yesterday's 29–91/hour. It cannot see tonight's
rise because **it is another binary-at-a-threshold instrument** (`timeout 50`), and a
mount moving from 0.1 s to 62 s spends almost all of that journey below its cliff.
⇒ Do NOT read the flat proxy as evidence the mount is stable tonight. It is the same
null-from-a-threshold error this file already documents, arriving from the other side.
The proxy is a detector of *allocation difficulty past 50 s*; the latency log is a
*measurement*. Only one of them can answer "how bad is it now".

### Prediction, recorded BEFORE the test that settles it

The credit-cycle audit ends at 06:00. Every sample above is inside that loaded window —
**including 02:11 at 101 ms**, which is why "my load is the cause" does not already
explain a 600× rise *within* the load.

- **06:11 and 06:41 are the first quiet-arm samples**, and they are a better test than the
  `:20` binary arm this was originally designed around, because they are continuous.
- **If load is the cause**, latency should fall back toward the 0.1–2.5 s range by 06:41.
- **If the mount itself is degrading**, 06:11/06:41 stay in the tens of seconds.
  That would favour the chunk-exhaustion account and make the overnight trend
  independent of anything memo is doing.
- ⛔ Either way, a single pair is thin. The quiet arm runs to 09:22 and gets better the
  longer nothing else is launched at `/mnt/nas`.

⛔ **DEFERRED DELIBERATELY:** the paired create test (heartbeat dir vs a distant dir on the
same mount), which would settle whether the residual is one contended directory or the
whole mount — and therefore whether server3's frozen "contention is on THIS ONE FILE"
text can finally be corrected. Running it now would widen the loaded arm and contaminate
the cleanest natural A/B available. **Run it after 06:41, inside the quiet window, where
it is also a cleaner measurement.**
