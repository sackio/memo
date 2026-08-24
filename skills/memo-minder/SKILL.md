---
name: memo-minder
description: The memo seat's daily maintenance cycle. Mines the fleet's Claude session logs for durable facts, indexes git activity, audits the corpus for capture misses and stale project state, reconciles what the day invalidated, and writes a checkpoint that makes the next run idempotent. Runs 06:17 EDT on server4 via cron. Orchestrates the transcript-recall and memo-writer subagents rather than reimplementing them.
argument-hint: "[--days N] [--dry-run] [--skip <phase>] [--full] [--daily-only]"
disable-model-invocation: false
---

# memo-minder

You are the `memo` seat. This is your daily cycle. **Execute it — do not describe it, assess it,
or ask whether to run it.** You are invoked from cron; there is no human at the terminal.

## ⭐ What this skill is now, and what changed 2026-08-21

**It orchestrates. It does not reimplement.** The mining, the corpus judgement and the writing are
done by subagents that exist for exactly those jobs:

| job | owner | why not here |
|---|---|---|
| read session transcripts, rank, return cited findings | **`transcript-recall`** | raw transcripts never enter your window |
| confirm a claim against the corpus, decide new-vs-edit, supersede, write | **`memo-writer`** | it already does dedup, supersession and provenance |
| per-project staleness judgement | **one subagent per project** | isolation per project, and cheap to run wide |

⛔ **A PREVIOUS VERSION OF THIS SKILL BANNED ALL OF THAT.** Phase C1 carried a 2026-07-28 ruling
from Ben that the judgement had to be *"this interactive Claude Code session"* inline, with
*"workflow/subagent fan-out"* explicitly banned. **Ben overruled it 2026-08-21 12:34 EDT:
*"my previous ruling is overruled."*** ⇒ Subagents are the intended mechanism now. Do not
reinstate the ban from an older copy of this file.

⭐ **WHY IT MATTERS, MEASURED.** The 2026-08-21 cycle pushed **~26 KB of raw transcript through the
seat's own context** to produce 3 memos, using a bespoke extractor. And the duplication had already
cost a real defect: memo-minder enumerated sessions with shell `find`, `transcript-passages` used
Python `glob`, and **the `find` path silently returned 0 on the busiest host** because the project
dirs there are symlinks. Two implementations of one job is how that survives.

⇒ **When you delegate a mechanism, its hard-won pitfalls must travel with it or be re-verified in
the new owner.** The `find -L` trap does not apply to `transcript-recall` (glob descends symlinks —
verified, 158 = 158, 2026-08-10), and it already drops hook transcripts and reports its
denominator. Those guarantees are documented in `/search-transcripts`; read it before assuming.

---

## Step 0 — Context

Print the header, then work. `date +%u` → 7 means Sunday (run WEEKLY phases too).

```
════════════════════════════════════════════
MEMO MINDER — <date>   <DAILY | FULL/Sunday>
host <hostname> · lookback <N>d · skips: <…>
════════════════════════════════════════════
```

**Args:** `--days N` (default 2) · `--dry-run` (analyse, change nothing) · `--full` / `--daily-only`
(force phase set) · `--skip <phase>` where phase ∈ `mine · capture-miss · liveness · gitpulse ·
write · perproject · checkpoint · sync`.

⚠️ **`--skip gmail --skip phony --skip atc --skip email` are still accepted and are NO-OPS.** Those
phases were handed to `assistant` on 2026-07-05 and the email was retired; the cron line still
passes the flags. Accept them silently rather than erroring — the flags are load-bearing only in
that removing them from cron would be a second change to make.

---

## Step 1 — Targets

**The memo API is `http://server4:8000`. There is no other.**

⛔⛔ **THIS WAS WRITTEN AS `http://office:8000` IN 7 PLACES UNTIL 2026-08-21**, including the
`POST`/`PATCH /documents` calls that store memos. office lost its memo container on 2026-08-10 —
recorded in this skill's own host table and **never propagated to the commands beside it**.
Measured 2026-08-21 12:13: `office:8000` is CONNECTION-REFUSED, nothing listens on 8000 there.
⭐ It never broke a cycle, which is exactly why it survived 11 days: every run used server4 because
the operator knew office was dead, so **the written instruction and the actual behaviour diverged
in silence.** ⇒ **When you retire an endpoint, grep for it.** The table documenting a change is not
the thing that obeys it.

| host | memo API | session logs |
|---|---|---|
| **server4** | ✅ `:8000` — the single global corpus | ✅ (dirs are SYMLINKS into `/fast4`) |
| server5 | proxy → server4 (`{"role":"proxy"}`) | ✅ |
| server3 | ❌ none | ✅ ~27 seats, as busy as server4 |
| office | ❌ none, port refused | ✅ |

⛔ **server3 is a session-log host.** It was missing from this list until 2026-08-10, and together
with the `find -L` bug the cycle saw **13 of 75 sessions (17%)** and reported clean.

---

## Phase A — Gather

### A.1 · Discover sessions — and check the denominator in BOTH directions

```bash
CUTOFF=$(date -d "$DAYS days ago" +%Y-%m-%d); touch -d "$CUTOFF" /tmp/bf_cutoff
for HOST in office server4 server5 server3; do
  CMD="/usr/bin/find -L /home/ben/.claude/projects -maxdepth 2 -name '*.jsonl' \
       -newer /tmp/bf_cutoff ! -path '*/subagents/*' -printf '%T@ %s %p\n'"
  if [ "$HOST" = "$(hostname)" ]; then eval "$CMD"
  else ssh -o ConnectTimeout=6 -o BatchMode=yes -p 4999 ben@$HOST \
       "touch -d '$CUTOFF' /tmp/bf_cutoff; $CMD" || echo "WARN: ssh $HOST failed"; fi
done
```

⛔ **`-L` IS LOAD-BEARING.** server4's per-project dirs are symlinks into `/fast4`. Bare `find`
returns **0 there** — measured 25 vs 1917 on 2026-08-21 — and a zero is indistinguishable from a
quiet day. Run both forms as a control whenever a count looks off.

⛔ **FILTER HOOK TRANSCRIPTS BY LINE COUNT, NOT BYTES.** A `claude -p` hook transcript is ~119 KB
with **14 lines**; a size filter passes it. Keep only `wc -l > 100`. (`claude -p` hooks are
hard-disabled since 2026-08-11, so the ratio is now ~60% real — but the filter stays, because the
hooks can come back and the failure is silent.)

⭐⭐ **CHECK THE DENOMINATOR WHEN IT MOVES IN EITHER DIRECTION.** The low-count rule is half-blind:
on 2026-08-11 the same defect made this measurement **33× too HIGH** (2,431 files, 73 real) and an
inflated count reads as *coverage*, which nobody questions. Compare against yesterday's checkpoint.

⚠️ **An ssh failure is a HOST problem, not an empty result.** Say which host was not searched.

Dedup against the last checkpoint on `<uuid>@<line_count>`. **NEW** = unseen; **GROWN** = seen with
a lower count — mine only the delta.

⛔⛔ **DO NOT FIND THE LAST CHECKPOINT BY SEARCHING FOR IT.** Vector search ranks by RELEVANCE,
never by recency: on 2026-08-22 `POST /search "Backfill checkpoint..."` returned the **06:47**
checkpoint above the **13:02** one, and deduping against the older produced **93 NEW / 0 GROWN** —
a claim that every session on the fleet was brand new. There is no error and no empty result;
both hits are real checkpoints with the right title. ⇒ **"Latest X" is an ORDERING, not a query.**

```bash
curl -sS 'http://server4:8000/documents?limit=20000' | python3 -c "
import json,sys
docs=json.load(sys.stdin)
cps=[x for x in docs if (x.get('title') or '').startswith('Backfill checkpoint — ')]
cps.sort(key=lambda x: x.get('created_at') or 0, reverse=True)
print(cps[0]['id'])"
```
Safer still, and what this cycle used: **union the dedup keys of every checkpoint from the last
48 h**, so a second same-day run cannot be missed.

⛔ **AND ANCHOR THE UUID WHEN YOU EXTRACT THE KEYS.** `([0-9a-f]{8})@(\d+)` against
`cb04bbc5-4390-45f4-9525-173f3b1a012e@17464` captures **`3b1a012e`** — the last 8 hex characters
before the `@`, not the uuid's first 8 — so every key silently fails to match and you get the same
**93 NEW / 0 GROWN** reading from a second, independent cause. Use the full form:
`([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}@(\d+)`.
⭐ Two different bugs, one identical and *plausible* symptom. Neither is visible to the standing
"an empty result is not a null" guard — the result was neither empty nor null. **What caught it
was a plausibility check against the prior day's number.** Always compare NEW/GROWN to the last
checkpoint's own figures before believing them.

### A.2 · Mine — `transcript-recall`, not your own extractor

Select by growth delta, cap ~16 sessions/cycle. Group by project and delegate:

```
Agent(subagent_type="transcript-recall",
      description="mine <project> for durable facts",
      prompt="""
Question: What durable facts, decisions, conventions, config values, or hard-won
pitfalls appeared in this project's sessions that a future seat would need and
could not re-derive cheaply?

Scope: --path /mnt/nas/data/code/<project> --since <CUTOFF> --hosts all
Caller's cwd: /mnt/nas/data/code/memo   Caller's host: server4

Return each finding with its session citation. Skip: failed attempts whose
resolution is already captured, ephemeral status, anything obvious from the
code or README, and routine chatter.
""")
```

⛔ **Keep the negative it gives you, and keep it PRECISE.** *"not discussed"* (searched, found
nothing) · *"nothing was searched"* (zero sessions read) · *"host X not searched"* are three
different results and only the first is a real negative.

⚠️ **Most seats now write their own memos.** The fleet banked 88 memos in 30 h on 2026-08-20, so
the yield here is *synthesis across seats*, not transcription — the finding no single seat could
see. Two seats hitting the same defect in different subsystems on the same day is the shape worth
writing; one seat's own bug usually is not, because they already recorded it.

### A.3 · Capture-miss audit — surfaces logistics data that never became a memo

⚠️ **USE THE WORKING IMPLEMENTATION**, `bin/capture_miss_scan.py`, per host:
`CAPMISS_CUTOFF=YYYY-MM-DD python3 - < bin/capture_miss_scan.py` over ssh; emits JSON.

⛔ **STANDING POSITIVE CONTROL, EVERY CYCLE:** `CAPMISS_CUTOFF=2026-07-22` on **server3** MUST return
a hit whose context contains *"this is where I parked at logan"*. **A detector that has never been
run against a known positive is not a detector** — that is exactly how this phase sat marked TODO
while silently matching nothing for weeks.

⛔⛔ **CORRECTED 2026-08-23 — THIS SAID `office` AND THE DATUM IS ON `server3`.** The `assistant`
seat migrated office→server3 on 2026-08-10, taking the Logan turn with it. Measured 2026-08-23:
server3 holds it at `2026-07-22T11:19:46.600Z`, **role=user, str content** — exactly what the
scanner's `if role == "user" and isinstance(c, str)` gate accepts, so it matches. office holds only
5 occurrences of the phrase, **all role=assistant with list content**, structurally unreachable by
that gate — so running the control there returns real-but-unrelated hits and no control match,
which reads as a broken detector.
⭐ **SAME HOST MOVE BROKE A.4 BELOW** (its script globs the local host only). One migration, two
phases silently pointing at the old host, both failing in the reassuring direction. Fix them
together; fixing one leaves the pair half-migrated.
⚠️ **AND MIND THE NULL.** I first "established" that the phrase did not exist anywhere by sweeping
four hosts in a serial loop that a 2-minute timeout killed while server3 — the last host — was
still running. Its line printed empty, I read that as *no match* rather than *not searched*, and
wrote a confident three-reason retraction of a report that had been right all along. **A truncated
loop and a genuine null are byte-identical.** If you fan this control across hosts, it must report
*searched-no-match* and *not-searched* as different outcomes, per host, and never let a timeout
render as a zero.

Classify each hit: **DEDICATED_HIT** (a memo was written within 5 turns) · **BURIED** (the datum
lives only in a calendar-event description) · **MISS** (nothing) · **RECOVERED** (a memo appeared
1–30 d later — still counts as a miss; do not launder it into a hit).

⚠️ The 2026-07-22 Logan case was *captured to the wrong store*: the agent OCR'd the photo, answered
Ben in Slack, and never wrote a memo. **"An agent extracted the datum and answered in-channel" is a
MISS.**

### A.4 · Liveness — `bin/assistant_liveness_hours.py`

Measure **hours containing an actual turn**, unioned across hosts. Flag a gap > 12 h.

⛔ Two wrong ways, both of which this phase has already produced: **mtime deltas** manufacture
phantom gaps (a session running 4 days has ONE mtime — this is where a bogus "26.5 h gap" came
from), and **coverage intervals** report no gaps ever, because `--resume` copies prior history.

⛔⛔ **RULE OUT A HOST MOVE BEFORE FLAGGING.** A seat that migrates leaves its old transcript frozen
forever, and that is indistinguishable from a dead seat *on that host*. 2026-08-10: `assistant`
looked 6 days dark on server4 and office; it had moved to server3 and was fine. **A liveness claim
derived from one host is not a liveness claim.** Cheap disproofs: `tmux ls` on each host, or an
`atc_query` post inside the "gap".

### A.5 · Git pulse — index only

```bash
for p in ~/code/*/; do [ -d "$p/.git" ] || continue
  out=$(timeout 120 git -C "$p" log --since="$SINCE" --oneline 2>/dev/null); rc=$?
  if [ $rc -eq 124 ]; then echo "⛔ TIMEOUT $(basename $p) — INCONCLUSIVE, not zero"; continue; fi
  n=$(printf '%s' "$out" | grep -c .); [ "$n" -gt 5 ] || continue
  echo "===== $(basename $p) ($n) ====="; timeout 120 git -C "$p" log --since="$SINCE" --format='%s'
done
```

⛔⛔ **READ `rc`. A TIMEOUT IS NOT A ZERO.** This phase silently reported "no repos qualified" on
2026-08-21 13:07 while `agentkit` alone had **≥20 commits** — every repo's `git log` was killed at
the old `timeout 8` before emitting anything, `wc -l` counted 0, and `-gt 5` skipped them all. The
output file was empty and read exactly like a quiet day.
⭐ **This is the same defect this cycle banked hours earlier** (`ce93df29`, and `scripts`' identical
`timeout N grep` finding): the timeout case exits **124**, the genuinely-clean case exits **0 with
no lines**, and a caller that only looks at the output cannot tell them apart. Piping straight into
`wc -l` throws away the one bit that distinguishes them.
⚠️ **NFS latency here is not stable.** Measured 2026-08-21 13:08: `git log` on `agentkit` did not
finish in **60 s**, while `ha` returned instantly. `agentkit` reported the same day that server5 is
driving sustained ~90% IO pressure on the NAS. ⇒ Any timeout tuned on a quiet moment starves under
load; report the starvation rather than absorbing it.

⛔ **PER-COMMIT MEMOS ARE OFF — Ben, 2026-08-11: *"leave in git for now."*** Write ONE index memo
per window (repo, count, verbatim subjects) and stop. Model: `8ffd0a3b`. **Do not re-raise this**;
it was deferred three times before being decided. The numbers: 469 substantive commits in a 2-day
window would have doubled a 9,100-doc corpus in ~6 weeks with exactly the low-value bulk that
degrades retrieval.

⚠️ `timeout 8` per repo is load-bearing — these live on NFS and an unbounded loop hangs the cycle.

---

## Phase B — Write, via `memo-writer`

⛔ **DO NOT `POST /documents` YOURSELF FOR A FINDING.** `memo-writer` confirms the claim against
transcripts and the existing corpus, decides new-vs-edit, supersedes what the fact invalidates, and
returns the id. Reimplementing that is what this rewrite removed.

```
Agent(subagent_type="memo-writer",
      description="bank finding: <short subject>",
      prompt="""Remember that <the claim, in plain words>.

How I know: mined from session <uuid8> on <host> by transcript-recall, <date>.
Evidence: <the citation it returned>.
Tag it session-sourced and session-<uuid8>.""")
```

⭐ **Say how you know.** Measured, the operator said it, or you inferred it — three claims with
three different durabilities, and the subagent cannot supply what you do not give it.

**Write directly (`POST /documents`) ONLY for this cycle's own bookkeeping**: the git-pulse index
memo and the checkpoint. Those are records, not findings, and have no dedup question.

⛔ **`memo_update(content=)` and `PATCH` are DESTRUCTIVE FULL REPLACES with no undo.** Prefer
`append=`. Before splitting or rewriting another seat's memo, back the content up to a file first
and verify afterwards that every non-empty line landed somewhere.

---

## Phase C — Per-project reconcile

Discover projects with sessions in the last 72 h (from A.1). Cap 15/cycle. Skip a project whose
memos already carry today's `reconciled-<date>` tag — the L1 SessionStop hook got there first.

For each, one subagent, in parallel:

```
Agent(subagent_type="memo-recall",
      description="reconcile <project>",
      prompt="""Fetch the 15 most-recently-updated memos tagged project-<slug>,
repo-<slug> or <slug> from http://server4:8000.

Identify ONLY cases where a NEWER memo makes an OLDER one wrong — a superseded
config value, an IP that moved, a decision reversed, a state that changed.

Return each as: older_id, newer_id, what is now false, and the one-line banner
that should be prepended to the older memo. Do not propose edits for memos that
merely overlap in topic — that is not staleness.

⛔ AGE ALONE IS NOT SUPERSESSION. If you cannot tell which is true, say so and
return both ids rather than choosing.""")
```

Apply at most **6 mutations per project**, as prepended banners (`> Superseded <date> by <id>: …`)
plus tags `reconciled-<date>` + `superseded`. ⛔ Bias hard toward the banner over DELETE.

---

## Phase D — Checkpoint

One memo per cycle, `POST /documents` directly:

- **Title** must start `Backfill checkpoint — ` (the reap key — see below).
- **Tags** must include `backfill-log` (dedup lookup) + `backfill-checkpoint` + `maintenance`.
- **Body**: every `<uuid>@<line_count>` processed, per host; coverage numbers with their
  denominators; what was written and patched; which phases were skipped and why.

⛔⛔ **REAP ON THE TITLE PREFIX *AND* THE TAG — NEVER THE TAG ALONE.** 136 corpus memos carry
`backfill-log` and only ~6 are mine; the rest are `assistant`'s sweep logs. Tag-only would delete
another seat's work. Keep the 3 most recent regardless; a candidate must also be a no-op or >14 d
old, and the surviving checkpoint must absorb its dedup keys first.

---

## Phase E — Sync check (read-only)

```bash
tail -3 /tmp/memo-v2-to-v1-sync.log
```

The reverse sync to the v1 standby runs hourly at `:47`, so a line up to ~60 min old is CORRECT.

⚠️ **`failed=N` is almost always the token ceiling.** v1 refuses a write over **8,192 tokens**, and
**the destination then keeps its stale copy — present, plausible, and undetectable downstream.**
The guard predicts it before attempting: look for `UPD-WILL-FAIL`. Remedy is to split the memo at a
semantic seam, never to trim.

⛔ **Count what is over the ceiling NOW, not what has ever tripped the guard.** Grepping every
`UPD-WILL-FAIL` id out of the log gave **13** on 2026-08-21 when the true answer was **2** — the
other 11 had been split or deleted. **A log accumulates events; a corpus holds state**, and the
inflated number was the more alarming one, which is why it went unquestioned.

⛔ v1 is 3-small/1536 via OpenRouter. **Never point it at `:31541` or `:31536`** — 2560-dim vectors
in a `FLOAT[1536]` store silently mis-rank the whole thing.

---

## Weekly (Sundays, or `--full`)

Auto-memory file ingest + audit · full-corpus near-duplicate reconcile at ≥0.78 · tag-vocabulary
and oversized-memo curation · purchasing-profile synthesis · non-log reaping (superseded
duplicates, ephemeral stubs, `*-fingerprint` memos >14 d).

⛔ **Phase H (project-DB promotion) is REMOVED** — it was a no-op for the skill's entire lifetime;
no `.memo.db` ever existed. Do not restore it.

---

## Close

```
CYCLE COMPLETE — <date> (<N>m) · <D> discovered/<R> real/<M> mined
findings <new>/<patched> · capture-miss <n> real (control PASS/FAIL)
liveness <gap|none> · per-project <p> projects/<m> mutations
git <c> commits/<r> repos · sync <status> · checkpoint <id>
```

Append one line to `~/.memo/minder.log`, then stop.

⚠️ **There is no digest and no email.** The email went to `assistant` on 2026-07-05; the 06:30
digest DM was **retired by Ben on 2026-08-21** because it printed zeros while 100+ memos/day were
written — its per-project table keyed on a `repo-`/`project-` tag that **0 of the last 103 memos
carried**. Reasoning in memo `f1303326`. ⛔ **Do not reinstate a digest here.** Ben receives nothing
daily from memo by design; the `11,41 * * * *` watch loop messages him on a real problem.

---

## Standing guardrails

- ⛔ **Never fabricate a fact to fill a phase.** A cycle that found nothing and says so is correct.
- ⛔ **An instrument agreeing with an absence is not evidence that it lies.** Before recording a
  tool as broken, construct a positive control — create the condition you expect to detect and
  re-run the same query. Four separate readings on 2026-08-21 alone were true observations with a
  wrong inference laid on top.
- ⛔ **`rm -f` an output file before writing to it, and assert bytes were written.** A file the
  current command did not create is not evidence about the current command; a stale `/tmp` file
  once let a total outage score as green.
- ⛔ `command grep` on specific files. `docker logs`/`exec`/`run` write **0 bytes to a redirect** —
  pipe. `docker inspect --format` returns EMPTY with exit 0 — pipe JSON to `python3`.
- ⚠️ SSH failures are non-fatal: log the host as unsearched, continue, and say so in the summary.
- ⚠️ Cron mode has no TTY — auto-skip anything that would prompt, after noting it.
