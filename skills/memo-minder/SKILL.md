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
write · perproject · checkpoint · sync`. ⚠️ `sync` is now a NO-OP — see Phase E.

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

## Step 2 — How this cycle is INVOKED: headless, on API credits

⭐ **Since 2026-10-12 this cycle runs as a headless `claude -p` job on server4, paid from API credits,
not from a seat's Max quota.** Authorized by Ben in #agents: *"perhaps memo can use API credits"*
(12:49:43) and *"yes"* to ask `a2v` (12:50:40). Rationale: the cycle is a batch job with ~20 phases and
does not need a live seat; it was measured as the `memo` seat's single largest spend of 2026-10-12.

```bash
# server4 crontab. ⛔ The cwd is load-bearing — see the self-mining exclude in A.1.
17 6 * * * mkdir -p /tmp/memo-minder-cycle && cd /tmp/memo-minder-cycle && /usr/bin/flock -n /tmp/memo-minder-cycle/.lock /usr/bin/timeout --foreground -k 30 3600 /mnt/nas/data/code/memo/scripts/memo-audit/with-grant-key /home/ben/.local/bin/claude -p --output-format json --model sonnet --permission-mode bypassPermissions "Run the memo-minder daily cycle now: --days 2" >> /tmp/memo-minder-cycle/run.jsonl 2>&1; L=$(grep "$(date -I)" /home/ben/.memo/minder.log | tail -1); if [ -z "$L" ]; then echo "$(date -Is) memo-minder-cycle DID-NOT-COMPLETE: no minder.log entry for $(date -I)" >> /tmp/cronguard-misses.log; elif echo "$L" | grep -qE "MINING SKIPPED|mining BLOCKED|NO CYCLE RAN|NOT in Agent registry|[1-9][0-9]* real/0 mined"; then echo "$(date -Is) memo-minder-cycle MINED-NOTHING: wrote a minder.log line but Phase A.2 produced no findings -- $L" >> /tmp/cronguard-misses.log; fi

# ⛔⛔ THIS LINE USED TO READ `ANTHROPIC_API_KEY="$(cat .../api-keys/admin.key)"` AND THAT WAS REAL
# BILLING. MEASURED 2026-10-19: `admin` is admin@laurasack.com, org "Admin", holds only
# laura-api-key and has NO linked credit grant (unibrowse 2026-10-12, memo `34fa4ebc`) — so ~$5 a
# run, every day, was charged to Laura's account while ~$790/mo of free per-account grant went
# unspent. Authorized by Ben, Slack #memo 2026-10-19 14:24 ("yes you can edit it please just
# proceed"). Crontab fixed the same day; backup `scratchpad/crontab-backup-20261019T130402.txt`.
#
# ⭐ WHY A WRAPPER AND NOT A DIFFERENT KEY PATH. A cron line can only name a STATIC file, which is
# how it rotted the first time — swapping one hardcoded path for another just re-arms the same
# trap. `with-grant-key` resolves the grant account with the SOONEST renewal at run time and puts
# the key in the CHILD env only (never argv, never a log). `--which` prints the account without
# touching a key.
```

⛔ **TWO CRON TRAPS THIS LINE IS WRITTEN TO AVOID. Both were in my first draft of it, 2026-10-12.**
1. **`claude` MUST be an absolute path.** It lives at `/home/ben/.local/bin/claude` (a version-managed
   launcher; today 2.1.296). Cron's PATH is `/usr/bin:/bin`, so a bare `claude` is NOT FOUND — and the
   failure is a cron mail nobody reads plus an empty log, which is byte-identical to "the cycle ran and
   found nothing."
2. ⛔ **NO `%` ANYWHERE IN THE COMMAND FIELD.** crontab treats an unescaped `%` as a newline separator,
   so a dated log name like `run-$(date +%F).json` TRUNCATES the command at the `%` and silently
   installs a different job than the one you typed. It is escapable as `\%`, but this line avoids `%`
   entirely and appends to one `run.jsonl` instead — an escape that one later editor removes is a trap
   that re-arms itself.
3. ⛔⛔ **`--permission-mode bypassPermissions` IS NOT OPTIONAL, AND ITS ABSENCE FAILED AS A SUCCESS.**
   MEASURED on the FIRST headless run, 2026-10-13 06:17: without it, every `Bash` and `Write` call was
   refused for permission — down to a plain `ls /home/ben/.claude/projects` — so nothing was discovered,
   mined, written or checkpointed, `~/.memo/minder.log` was never appended, and **the result JSON still
   reported `"is_error": false, "subtype": "success"`** after 9 turns, 28 s and $0.1892 of API credits.
   A headless `claude -p` has nobody to approve a prompt, and no exit status can represent that.
   ⭐⭐ **THE EXIT STATUS OF A HEADLESS AGENT MEASURES THAT IT ANSWERED, NEVER THAT IT ACTED.** Same
   family as `timeout`'s 124 read as a zero (A.5) and a truncated loop read as a null (A.3): the
   instrument reports on its own completion, not on the work.
   ⇒ The line therefore carries a detector that asserts the cycle's OWN OUTPUT, not its rc:
   `grep -q "$(date -I)" ~/.memo/minder.log || echo ... >> /tmp/cronguard-misses.log`, the same misses
   file every other guarded job on this crontab writes to. ⛔ Do not replace it with a check on `$?`.
   ⚠️ `bypassPermissions` here is PARITY, not escalation — the live `memo` seat has always run in bypass,
   so the cron gains nothing the cycle did not already hold when it ran interactively. The commented
   `atc-minder` line on this same crontab uses the identical flag.
4. ⚠️ `flock -n` and `timeout --foreground -k 30 3600` were added the same day: a cycle that hangs on
   API credits bills until something kills it, and a stacked second copy would spawn a second set of
   miners and write a second checkpoint.
⚠️ Keep it on ONE physical line. A backslash continuation inside a crontab command field is not a line
continuation to cron.

⛔⛔ **NEVER EXPORT THE KEY.** Set it on that ONE command only. **An `ANTHROPIC_API_KEY` present in a
seat's environment OVERRIDES that seat's OAuth login**, so exporting it in a shell, a profile or
`settings.json` silently moves an interactive seat's spend onto the credits and off the account it is
supposed to bill. ⛔ Never write the key's VALUE into a memo, a Slack message, a commit or a transcript
— record only that it lives at `~/.claude/state/api-keys/<acct>.key`, mode 600, **HOST-LOCAL to
server4** (memo `0fb9e6f5`). Keys present as of 2026-10-12: `admin`, `agents`, `ben`; `admin` and `ben`
are the verified-working pair.

**Spend ledger — required, so the fleet's $40/day cap can see this job.** Append the run's
`total_cost_usd` from the JSON result to `~/.claude/state/api-keys/spend.jsonl` as
`{"day","ts","acct","model","usd"}`. One-shot judgement calls inside scripts should use
`agentkit/scripts/haiku`, which logs itself.

⛔⛔ **THE ATC SCHEDULE IS THE OTHER TRIGGER AND IT MUST STAY PAUSED.** `50166663` (`memo-minder`,
owner memo) is cron `17 6 * * *` America/New_York — **the identical slot to the crontab line above.**
Left armed, the cycle runs TWICE at 06:17: once headless on credits, once in the live seat on Max
quota, each spawning its own miners and each writing a checkpoint. PAUSED 2026-10-12 16:56Z, verified
`paused: true` / `health: paused`, fire_count frozen at 51.
⇒ **Exactly one of these two triggers may be armed at a time.** Re-arming either means pausing the
other in the same change. ⚠️ A paused schedule's `next_fire_at` stays frozen and reads as *overdue* in
a listing — that is cosmetic and is not evidence the pause failed; check `paused`/`health`.

⭐⭐ **PRIOR EVIDENCE ON MODELS, RECOVERED 2026-10-12 FROM THE CRONTAB'S OWN COMMENT, dated 2026-05-03:
"Pinned to Sonnet 4.6 (Haiku bailed on Sunday cycle, too important to risk)."** ⇒ **Haiku has already
been tried on this cycle and it failed**, five months before the 2026-10-12 decision to put the miners
on it. That is not a reason to reverse the miner change — miners rank and cite, which is not what
bailed — but it IS prior evidence that the A/B gate on the judging steps is load-bearing rather than
cautious, and whoever runs that A/B should know the experiment has a precedent that went badly.
⚠️ **And note where that evidence lived: in a crontab comment, on one host, attached to a line that had
been deleted.** It was not in memo and not in this skill. It survived only because the comment block
outlived its command.

⛔ **A RELATED TRAP, FOUND IN THE SAME PLACE AND WORTH THE PARAGRAPH:** the 2026-07-05 disable comment
read *"DISABLED until Sun 2026-07-05 (Ben, token-reduction) — re-enable by uncommenting:"* — and
**there was no line to uncomment.** The command had been deleted outright, so the instruction pointed
at nothing; verified 2026-10-12 by grepping the live crontab for any active or commented memo-minder
command (zero of either). From 2026-07-05 to 2026-10-12 the cycle therefore ran ONLY from the ATC
schedule into the live seat — which is the whole reason it was billing Max quota. ⭐ The shape: a
decommission that removes the mechanism but keeps the re-enable instruction leaves a doc that is
confidently, checkably wrong, and nothing about reading it reveals that. ⇒ When you disable something
by deletion, say DELETED and name what to restore; "uncomment this" is only true if the line is there.

⚠️ **What the live `memo` seat does afterwards: READ THE CHECKPOINT.** It does not re-run phases, re-read
the miners, or re-audit the corpus. The checkpoint memo is the cycle's report; the seat's job is to act
on what it names and otherwise leave it alone.

⚠️ **This moves WHO PAYS, not HOW MUCH WORK HAPPENS.** If the goal is less corpus churn per day rather
than lower cost, the thing to change is this skill's SCOPE, and that is the operator's call — do not
quietly trim phases to make a spend number look better. A phase skipped silently is indistinguishable
from a phase that found nothing.

---

## Phase A — Gather

### A.1 · Discover sessions — and check the denominator in BOTH directions

```bash
CUTOFF=$(date -d "$DAYS days ago" +%Y-%m-%d); touch -d "$CUTOFF" /tmp/bf_cutoff
for HOST in office server4 server5 server3; do
  CMD="/usr/bin/find -L /home/ben/.claude/projects -maxdepth 2 -name '*.jsonl' \
       -newer /tmp/bf_cutoff ! -path '*/subagents/*' \
       ! -path '*/-tmp-memo-minder-cycle/*' -printf '%T@ %s %p\n'"
  if [ "$HOST" = "$(hostname)" ]; then eval "$CMD"
  else ssh -o ConnectTimeout=6 -o BatchMode=yes -p 4999 ben@$HOST \
       "touch -d '$CUTOFF' /tmp/bf_cutoff; $CMD" || echo "WARN: ssh $HOST failed"; fi
done
```

⛔⛔ **`! -path '*/-tmp-memo-minder-cycle/*'` IS THE SELF-MINING EXCLUDE. DO NOT DROP IT.** Added
2026-10-12 with Ben's authorization (#agents ask `a2v`, his "yes" at 12:50:40) when this cycle moved to
a headless `claude -p` run on API credits. **A `claude -p` run writes its own session transcript into
`~/.claude/projects/<slug>/`**, so without this exclude the next night's A.1 DISCOVERS THE PREVIOUS
NIGHT'S CYCLE as an ordinary session and mines it — the cycle's own findings come back as fresh
evidence, with provenance laundered one generation per day and no error anywhere.
⚠️ **The line-count filter does NOT save you here.** It drops a ~119 KB / 14-line hook transcript, but a
full cycle transcript is hundreds of lines and passes cleanly. ⇒ The guard must be the PATH exclude.
⚠️ The exclude is keyed on the cycle's working directory, so the headless invocation MUST run from
`/tmp/memo-minder-cycle` (slug `-tmp-memo-minder-cycle`). Change the cwd and you silently disarm this —
if you move it, change both places in the same commit.
⭐ Why this population currently looks empty and will not stay that way: `claude -p` hooks have been
hard-disabled fleet-wide since 2026-08-11, so there are almost no `-p` transcripts to trip over today.
This cycle is about to start producing one every night.

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

⛔⛔ **AND READ *BOTH* FORMS, UNIONED — THE ANCHORED PATTERN ALONE CANNOT SEE A MONTH OF PAST KEYS.**
Authorized by Ben, Slack #memo 2026-10-01 ("yes", answering ask `aa`). MEASURED that day across all
17 surviving checkpoints: **11 of 17 carry short-form keys, 2,519 of them**, written by earlier
cycles before the write-side bug below was fixed. The anchored reader matches **zero** of those, so
every session they name is re-mined as NEW or GROWN on every subsequent cycle — spend that reads as
coverage. Union both, normalising the short form to a uuid-PREFIX match:
```python
FULL  = re.compile(r'\b([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}@(\d+)\b')
SHORT = re.compile(r'(?<![0-9a-f-])([0-9a-f]{8})@(\d+)\b')     # legacy, pre-2026-10-01 checkpoints
seen = {}
for rx in (FULL, SHORT):
    for u, n in rx.findall(text):
        seen[u] = max(seen.get(u, 0), int(n))                   # key on the uuid's FIRST 8 either way
```
⚠️ **Both forms key on the uuid's first 8 characters, so the union is safe** — a session's dedup key
is `basename(path)[:8]`, which both patterns yield. The short form loses the ability to DISAMBIGUATE
two sessions sharing an 8-hex prefix; at ~10k sessions that collision is remote and the alternative
is reading nothing at all. ⇒ Accept the prefix for READING history; always WRITE the full form.
⭐ This recovers the already-written month. It does not make the short form acceptable to emit — see
the write-side rule immediately below, which stops the set growing.
⭐ Two different bugs, one identical and *plausible* symptom. Neither is visible to the standing
"an empty result is not a null" guard — the result was neither empty nor null. **What caught it
was a plausibility check against the prior day's number.** Always compare NEW/GROWN to the last
checkpoint's own figures before believing them.

⛔⛔ **AND WHEN YOU *WRITE* KEYS BACK, EXTRACT WITH THE WHOLE MATCH, NEVER THE CAPTURE GROUPS.**
Authorized by Ben, Slack #memo 2026-10-01 ("2 yes"). `re.findall` on the anchored pattern above
returns its GROUPS, and group 1 is only the uuid's first 8 characters — so a writer that uses it
emits `e9d0935f@7257`, the **short form that the mandated anchored reader matches ZERO of.** Use
`re.finditer(...)` with `m.group(0)`, or a non-capturing pattern, whenever keys are written:
```python
KEY = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}@\d+\b')
keys = sorted(set(KEY.findall(text)))        # no groups -> findall returns whole matches
```
⭐ **THIS IS THE SAME DEFECT AS THE READ-SIDE ONE ABOVE, ARRIVING FROM THE OTHER DIRECTION** — keys
written in a shape only the forbidden reader can match. MEASURED 2026-10-01: it truncated 321 keys
during a reap absorption, and **the count matched perfectly** (322 extracted, 322 written), so no
cardinality check could see it. It was caught only because the reap guard re-verified each key with
the anchored pattern a future cycle would use, and refused the DELETE. ⇒ **An absorb-then-delete
step must assert the ROUND TRIP, not the count.** Counts survive truncation, case folding,
prefixing and re-quoting — every one of which destroys the join. Memos `ce0b9833`, `a744cae0`.
⚠️ **The read side is NOT fixed and was NOT authorized** — 11 of 17 surviving checkpoints still
carry short-form keys (2,519 of them, measured 2026-10-01), and the anchored reader reads those as
zero, so their sessions are re-mined every cycle. That recovery is still queued for Ben; this entry
stops the set growing, it does not shrink it.

### A.2 · Mine — `transcript-recall`, not your own extractor

Select by growth delta, cap ~16 sessions/cycle. Group by project and delegate:

```
Agent(subagent_type="transcript-recall",
      description="mine <project> for durable facts",
      model="haiku",
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

⛔⛔ **`model="haiku"` IS FOR THE MINERS ONLY. THE JUDGING STEPS STAY ON SONNET.** Authorized
2026-10-12 by Ben (#agents `a2v`). Miners rank passages and cite — cheap judgement, verifiable against
the citation. **Phase B `memo-writer` and Phase C `memo-recall` must NOT be downgraded until one A/B
run shows a cheaper model still REFUSES.** The refusal behaviour is the control that makes this cycle's
output trustworthy, and it is the first thing a cheaper judge drops. Measured on the 2026-10-12 cycle,
both in one run: a Phase C reconciler DECLINED to pick between a "5" and a "7" DRA device count and
returned both ids — vindicated within the hour by `cluster`'s own measurement, which showed 5 was not
producible by any DeviceClass; and a Phase B writer REFUSED a figure I had relayed to it ("3 corrupt
shards, all on server3") and corrected it from the corpus to 7 shards spanning server5.
⇒ **The A/B is the gate, not a formality**: run one cycle both ways on the SAME inputs and compare
REFUSAL COUNT and CORRECTION COUNT, not output volume. A cheaper judge that writes more and refuses
less is the failure mode, and it reads as productivity.

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

⛔ **STANDING POSITIVE CONTROL, EVERY CYCLE — RUN IT AGAINST THE NAS ARCHIVE, NOT A LIVE HOST.**
Point the scanner's glob at `/mnt/backup/claude-logs/server3/projects/*/*.jsonl` with
`CAPMISS_CUTOFF=2026-07-22`; it MUST return a hit from session `aa9d2ec0` at `2026-07-26T02:13:52`
whose context contains **"Level 6"** and **"Logan/Central/Level 6/Row R"**. **A detector that has
never been run against a known positive is not a detector** — that is exactly how this phase sat
marked TODO while silently matching nothing for weeks.

⛔⛔ **CORRECTED 2026-09-09 — THE CONTROL USED TO NAME A LIVE HOST AND IT EXPIRED THERE.** Measured
this cycle: running it against server3's LIVE `~/.claude/projects` completes cleanly (rc=0, 8 hits)
and matches the control ZERO times, because `find -L ... -name 'aa9d2ec0*'` finds nothing there any
more — `claude-cleanup.sh` deletes `projects/*.jsonl` older than 30 days, and server3's oldest
surviving live transcript is now 2026-08-09.
✅ **THE DATUM IS NOT LOST — IT IS ARCHIVED.** `archive-claude-logs` (hourly, `:43`) rsyncs each
host's own `~/.claude/projects` to `/mnt/backup/claude-logs/<host>/` with `--append-verify` and
**no `--delete`**, precisely so the 30-day cleanup cannot destroy history. The anchor is there on
BOTH server3 and office at
`/mnt/backup/claude-logs/server3/projects/-mnt-nas-data-code-assistant/aa9d2ec0-d808-4dbf-b897-87bca3c1a983.jsonl`
(34,139,655 bytes). Verified 2026-09-09 by running the scanner against that path: **CONTROL PASSES**,
matching `2026-07-26T02:13:52.833Z`, trigger `parking`, match `Level 6` — the exact anchor named above.
⭐ **A CONTROL ANCHORED ON LIVE PRODUCTION DATA INHERITS THAT DATA'S RETENTION POLICY**, so it carries
an expiry nobody wrote down, and when it expires it fails in the reassuring direction —
indistinguishable from the detector genuinely finding nothing. Anchor it on the ARCHIVE, which is
append-only and exists for exactly this reason.
⚠️ Do NOT replace this with a synthetic fixture. One was built in `/tmp` on 2026-09-02 as a
workaround and it is the weaker answer: it lives outside git, dies with `/tmp`, and asserts a datum
someone wrote rather than the real one. The archive needs no maintenance and is the actual turn.

⛔⛔ **CORRECTED 2026-08-27 — THE CONTROL USED TO ASSERT THE PHRASE *"this is where I parked at
logan"* AND THAT DATUM IS UNREACHABLE ON EVERY HOST.** Measured 2026-08-27 across all four:
the phrase survives only in `assistant/23a9ee1d` and `assistant/aa9d2ec0` (server3) and
`aa9d2ec0` (office), and **every** occurrence is `role=assistant` with list content, or
`role=user` with LIST content (a tool_result) — all structurally unreachable by the scanner's
`if role == "user" and isinstance(c, str)` gate. Ben's original turn was never captured at all;
the session record says it *"hit a dead inbox, no live session to process it"*.
⇒ The replacement above is a **different turn in the same file**, `role=user` with STRING content,
matching on the `Level 6` designator — it passes the gate and names the same Logan parking datum.
⚠️ A cycle on 2026-08-21 reported *"positive control on office PASSES, 22 hits"*. That claim does
not survive measurement and is **retracted** — office's copies are all list-content.
⭐ The lesson that outlives both: **a control asserting a literal string is only as durable as that
string.** Assert the datum the detector must find, not the sentence someone happened to write.

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

⛔ **PER-COMMIT MEMOS ARE OFF — Ben, 2026-08-11: *"leave in git for now."*** **Do not re-raise
this**; it was deferred three times before being decided. The numbers: 469 substantive commits in a
2-day window would have doubled a 9,100-doc corpus in ~6 weeks with exactly the low-value bulk that
degrades retrieval.

⛔⛔ **AND THE VERBATIM SUBJECT LIST IS OFF TOO — Ben, Slack #memo 2026-10-20 23:06 EDT, answering
ask `gp` with "yes".** This phase used to write ONE index memo per WINDOW carrying every commit
subject. Write **one SHORT index memo PER REPO per window** instead: repo, commit count, the window,
`rc` per repo, and **the `git log` command that reproduces the subjects**. No subject list.

⭐ **MEASURED 2026-10-20, AND THIS IS WHY.** The index memos were **164 documents, 903,549 qwen3
tokens — 5.0% of the entire corpus by volume — drawing 350 reads, 0.56% of all reads.** A 9x
over-representation in bulk against consultation. They average **2.1 reads each, ever**, where a
comparable big memo averages 62. They are write-only.
⇒ **THE SUBJECTS WERE NEVER THE SCARCE THING. GIT HAS THEM, AUTHORITATIVELY, FOREVER.** Copying
them into memo bought a second copy that cannot be corrected at its source, and spent 5% of the
corpus to do it. A command that regenerates the list is strictly better than a stale transcription
of it, and it is two lines instead of sixteen thousand tokens.

⚠️ **PER REPO, NOT PER WINDOW — that is the other half of the fix.** The single-window index hit the
16,384-token ceiling and got cut blindly into `[a]` and `[b]` parts at a byte boundary (see
`d2b8a190` / `bf8052ca`, which say so in their own text: *"Split across parts because the single
index was refused 413"*). A split chosen by the write path rather than by meaning is not a split.
Per-repo memos are small by construction and never reach the ceiling.

⚠️ **THE 164 EXISTING ONES STAY.** Ben authorised changing what this phase WRITES. He did not
authorise deleting what is already there, and ⛔ AGE IS NEVER SUPERSESSION. Leave them.

⚠️ **A PER-REPO `timeout` IS LOAD-BEARING** — these live on NFS and an unbounded loop hangs the
cycle. ⛔ **THE VALUE IS 120, NOT 8.** This line read "`timeout 8` is load-bearing" until
2026-10-20 while the block above it used `timeout 120` — and 8 is the value whose silent-zero
failure the same section documents three paragraphs up. Corrected with Ben's authorization,
Slack #memo 2026-10-20 23:12 EDT (ask `a0o`).
⭐ A NUMBER IN PROSE BESIDE THE CODE IT DESCRIBES IS A SECOND COPY THAT NOTHING KEEPS IN SYNC,
and this one survived its own correction: the cycle that raised the timeout fixed the command
and left the sentence. Read the block, not the sentence.

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

## Phase E — Sync check: **RETIRED. THERE IS NOTHING TO CHECK.**

⛔⛔ **v1 (`:8096`) AND THE HOURLY SYNC WERE SHUT DOWN 2026-08-25 17:55 EDT** — Ben, Slack #memo:
*"yes retire it"*. This phase used to `tail /tmp/memo-v2-to-v1-sync.log` and judge `failed=N` against
v1's write ceiling. **Do not do that.** The log is frozen at 2026-08-25T17:47 and `:8096` refuses
connections (curl rc=7); both are the CORRECT state now.
⇒ **A cycle that reports either as a problem is MANUFACTURING a finding**, which is the exact defect
this loop was corrected for three separate times on 2026-08-25.
✅ The only thing worth asserting, and it is optional: `curl -sS --max-time 5 http://localhost:8096/health`
should FAIL. Decide on curl's **EXIT CODE** (rc=7 = refused), never on the body of `-w '%{http_code}'` —
on a refused connection curl prints `000` *and* exits non-zero, so `$(... || echo 000)` yields `"000000"`
and the `= "000"` test takes the wrong branch. Measured: that bug reported a false "someone restarted it"
on a correctly-retired v1.
⚑ If `:8096` ever ANSWERS again, that is unexpected but is not an outage. Say so and move on.

⛔⛔ **AND THE 8,192-TOKEN CEILING RETIRED WITH IT. Do not carry it into any advice.** It was v1's
limit, not memo's. ⚠️⚠️ **CORRECTED 2026-10-09, authorised by Ben, #cluster ask `a97`. THERE *IS* A FIXED NUMBER:
16,384 QWEN3 TOKENS, MEASURED ON THE WHOLE RESULTING DOCUMENT.** This passage read *"Neither is
'~12.8k', nor 15,418, nor `MAX_INPUT_TOKENS = 16384` — there is no fixed number"*, and offered a
document living at 15,418 tokens as the disproof. **That disproof was a UNIT ERROR.** Stored
`token_count` is cl100k; the gate is the embedder's qwen3 context. 15,418 cl100k is ~16.3k qwen3 — so
that document was sitting AT the ceiling, not comfortably past it. `MAX_INPUT_TOKENS = 16384` was right
the whole time and was dismissed here as a myth.
⛔⛔ **DO NOT CONVERT A cl100k READING INTO qwen3 — NOT EVEN WITH A CONSERVATIVE FACTOR.** Measured
qwen3/cl100k ratios on four documents: **1.0614 · 1.1151 · 1.1721 · 1.256** — an 18% spread. A ~1.107
factor reports ~1,755 tokens of headroom that do not exist, and it fails in the direction that LOSES
THE WRITE. ⛔ Do not use stored `token_count` for this at all.
⭐ **Tokenize instead:** `POST http://192.168.1.5:31536/tokenize {"prompt": "<content>"}`, which also
returns `max_model_len`.
⚠️ A 413 reporting 400 and one reporting 413 about 1.3 ms apart are **ONE event re-surfaced**, not two.
⇒ **Measure with the real tokenizer before a long write. The 413 is still the backstop, and a REPEATED
413 on one document means SPLIT it, never retry.** To continue a full document, keep it and
**retitle + prepend** a pointer to the new part — ⛔ never `content=`.
⭐ The shape of both errors in this thread: a CHEAP PROXY for an exact measurement, each failing
*reassuringly*. A short-document tokenizer check reported ~3,900 tokens of headroom on a document with
98; a ratio conversion reports 1,755 that are not there. A proxy that errs toward "you have room" is
worse than no proxy. Memos `373eaeeb` / `5cd365a0` / `60c0127d`.

⚠️ **WHY THIS SECTION SURVIVED 16 DAYS AFTER THE THING IT DESCRIBES WAS SHUT OFF** (corrected
2026-09-10, authorized by Ben): every cycle read it, saw a stale log and a refused port, correctly
judged both benign, and moved on — so the wrong instruction never produced a wrong outcome and nothing
forced it to be read again. ⭐ Same shape as this skill's own Step 1 lesson about `office:8000`: the
written instruction and the actual behaviour diverged in silence. **When you retire a thing, grep for
every doc that tells someone to check it.**

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
git <c> commits/<r> repos · checkpoint <id>
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
