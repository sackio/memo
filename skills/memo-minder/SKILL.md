---
name: memo-minder
description: Master memo agent — ingests Claude session logs (agent-service-aware, size-scaled chunks, overseer-mode synthesis-scoring, noise-filtered), git activity in ~/code with commit-context pairing, auto-memory files, Gmail (incl. self/family @sack.io), Phony SMS/voicemail, ATC board statuses + DM threads across all 3 LAN servers; reconciles new memos chronologically each cycle (Phase D-lite) and full-corpus weekly; investigates conflicts and outdated claims via live probes; reaps ephemeral and superseded memos with idempotency-preserving checkpoints; syncs global memos across all 3 hosts; emails a per-source-broken-down consolidated report to ben@sack.io. Runs daily at 06:17 EDT on server4 via cron. DAILY phases: ingest + git-pulse + reconcile-lite + sync + email. WEEKLY phases (Sundays only): investigate, full reconcile, curate, reap, audit auto-memory, audit memo logs, mine corrections.
argument-hint: [--dry-run] [--days N] [--full] [--daily-only] [--skip ingest|gmail|phony|atc|gitpulse|investigate|reconcile|curate|reap|sync|email] [--host office|server4|server5|all] [--no-email]
---

Master memo agent. Daily cron at 06:17 EDT on server4. Replaces memo-backfill, memo-audit, memo-curate, memo-reconcile, memo-maintain. Emits a 3-list digest (added / edited / removed) email to ben@sack.io.

## EXECUTE — do not describe, assess, or ask

When this skill is invoked, you ARE running from cron. Your one job is to execute the cycle below — run the bash/curl/SSH/MCP calls, classify the results, write the memos, sync, send the email. Do NOT:
- Write an "Assessment" of what memo-minder is or does
- Explain what *would* happen if you ran the skill
- Ask the user whether to schedule it, run it interactively, or set up credentials
- Bail because tools "look like they need OAuth" or "look like they require scheduler context" — they don't, the tools work; just call them
- Output a description of the skill instead of running the cycle

The cron has already authenticated (Claude Max OAuth via `~/.claude/.credentials.json`), the MCPs (gmail, phony, atc, memo) are live, and the SSH keys are in place. **Execute. Do not narrate.**

If a specific tool call fails (e.g. SSH timeout to one host, Gmail rate limit), log the failure, surface it in the final email's Errors section, and continue with the rest of the cycle. Failures are non-fatal — partial completion beats no completion.

The expected end state is: checkpoint memo posted on each reachable host, email digest sent to ben@sack.io, ATC status revoked, "CYCLE COMPLETE" summary printed. Anything short of this is a regression.

## Arguments

- `--dry-run` — analyze and report only; make no changes; still emails the report (marked DRY RUN)
- `--days N` — Phase A lookback (default 2; daily cron with 2-day window covers boundary overlap, dedup handles repeats)
- `--full` — run ALL phases regardless of day
- `--daily-only` — run only daily phases regardless of day
- `--skip <phase>` — skip one phase: `ingest`, `gmail`, `phony`, `atc`, `dm`, `gitpulse`, `automemory`, `reconcile-lite`, `investigate`, `reconcile`, `curate`, `reap`, `sync`, `email`
- `--host <target>` — limit to: `office`, `server4`, `server5`, or `all` (default: all)
- `--no-email` — skip the email report (logs still written)

## SCOPE UPDATES 2026-07-05 (Ben's Sunday memo-maintenance redesign)

Coordinated with `assistant` and `memo` sessions to divide labor cleanly. Read this before diffing behavior against older runs.

**HANDED OFF to `assistant`** (skip these phases every cron):
- Phase A.4 (Gmail — Ben + Laura via workspace-service-account)
- Phase A.5 (Phony SMS + voicemail)
- Phase A.5b (ATC board statuses)
- Phase A.5c (ATC DM threads)
- Phase I (email digest → `assistant` publishes Slack digests instead)

Cron on server4 gained `--skip gmail --skip phony --skip atc --skip email` on 2026-07-05.

**COMPLEMENTED by new memo-agent workstreams** (fire OUTSIDE memo-minder cron):
- **L1 SessionStop reconciler hook** (`~/.claude/hooks/memo-reconciler.py`) — fires on session end (>50 substantive turns). PATCH/DELETE/SUPERSEDE project memos the session invalidated. Opt-in via `MEMO_RECONCILER_ENABLED=1`. See hook file header for env vars.
- **L2 Phase C1 per-project daily reconciler** — planned new DAILY phase (Workflow fan-out, one subagent per active ~/code project with sessions in last 3d).
- **L3a ATC event listener** — memo-session subscription to `network`/`cluster`/`servers` zones; on infra-change posts, PATCH corpus IPs/hostnames immediately. Event-driven.
- **L3b Daily targeted infra probe sweep** — separate cron script (not part of memo-minder). Probes memos tagged `network|infrastructure|k8s|barn-cluster|nas|ip|hostname`; 3-day-fail = `[UNREACHABLE]` prefix, 7-day-fail = DELETE (pure-infra) or HISTORICAL (decisions).

**Retained + refined** (memo-minder keeps doing):
- Phase A.1 session-log ingest + A.1b git-pulse + A.2 auto-memory + A.6 apply + A.6.5 reconcile-lite + A.7 checkpoints
- Phase G cross-host sync (near-no-op post-2026-06-29 single-global refactor)
- Sunday deep phases: B.3 supplant, C conflict, D full reconcile, E curate quality/tags, F reap non-log
- **Trim Phase B.1**: infra facts now covered daily by L3b. B.1 keeps other types (contractor commitments, dollar amounts, phone numbers).
- **Trim Phase E broad staleness scan**: L2 owns per-project staleness. E keeps global tag-vocabulary + missing-tags + oversized-memo checks + purchasing profile synthesis.

**Retired 2026-07-05**: `/memo-maintain` skill (superseded, 0 invocations).

---

## Schedule (cost-aware)

Heavy phases run weekly on Sundays only. Use `date +%u` (1=Mon..7=Sun) at start; flag `IS_SUNDAY=1` if `date +%u` returns `7`.

- **DAILY (every run)**: Phase A.1/A.1.4/A.1.5/A.1b/A.3/A.4/A.5/A.5b/A.5c/A.6/A.6.5/A.7 (ingest + capture-miss + liveness-gap + git-pulse + apply + reconcile-lite + checkpoint), Phase G (sync), Phase I (email)
- **WEEKLY (Sundays, or `--full`)**: A.2 + A.2b + A.2c + A.2d (auto-memory + memo-log + correction-mining), Phase B (investigate), Phase C (conflict), Phase D (full reconcile across corpus), Phase E (curate + purchasing profile), Phase F (reap non-log). Phase H removed 2026-05-31 (no project DBs ever existed).

`--daily-only` forces daily-only regardless of day. `--full` forces all phases regardless of day.

Phase headers below state `[DAILY]` or `[WEEKLY]`. Skip `[WEEKLY]` phases unless `IS_SUNDAY=1` or `--full`.

---

## Step 0 — Session context

Invoke `/date` and `/whoami`. Print the cycle header:

```
════════════════════════════════════════════
MEMO MINDER CYCLE
Date:     <date>  (Sunday=full / weekday=daily-only)
Machine:  <hostname>
Mode:     <normal | DRY RUN>
Lookback: <N> days
Phases:   <DAILY | FULL>
════════════════════════════════════════════
```

Post an ATC status:
```
atc_post(zones=["files"], agent_id="memo-minder",
         content="memo-minder cycle in progress on <hostname>",
         priority="info", ttl_seconds=7200)
```

Capture start timestamp for the email report.

---

## Step 1 — Discover targets

### Memo servers

> ⛔⛔ **THE API BASE IS `http://server4:8000`. IT WAS WRITTEN AS `http://office:8000` IN 7 PLACES
> UNTIL 2026-08-21, INCLUDING THE `POST`/`PATCH /documents` CALLS THAT STORE MEMOS.**
> office lost its memo container on 2026-08-10 — recorded in the table below and never propagated
> to the commands beside it. Measured 2026-08-21 12:13: `office:8000` is CONNECTION-REFUSED and
> nothing listens on 8000 on that host at all.
> ⭐ **This never broke a cycle, which is why it survived**: every run used server4 because the
> operator knew office was dead, so the written instruction and the actual behaviour diverged in
> silence. A fresh session following the text literally would have failed on every write.
> ⇒ **A fact recorded in one place does not reach the place that acts on it.** When you retire an
> endpoint, grep for it — the table that documents the change is not the thing that obeys it.

| Name    | URL                  | SSH host | memo API? | session logs? |
|---------|----------------------|----------|-----------|----------------|
| office  | — (no memo API)      | office   | ❌ **no container** (verified 2026-08-10; port 8000 CONNECTION-REFUSED 2026-08-21) | ✅ yes |
| server4 | http://server4:8000  | server4  | ✅ 200 | ✅ yes (**symlinked → `/fast4`, needs `find -L`**) |
| server5 | http://server5:8000  | server5  | ✅ 200 | ✅ yes |
| server3 | — (no memo API)      | server3  | ❌ n/a | ✅ **yes — 27 tmux seats** |

Always use the explicit hostnames so log output stays distinguishable.

⛔ **`server3` IS A SESSION-LOG HOST AND WAS MISSING FROM THIS LIST UNTIL 2026-08-10.**
It runs ~27 agent seats (`assistant`, `aloha`, `cars`, `curator`, `pool`, `garden`,
`house`, `gigs`, `appliances`, …) and is as busy as server4 — **31 sessions / 284 MB in a
2-day window**. It has **no memo API**, so it is a *source* host only: SSH it for session
logs, never health-check it for `/health`. `~/scripts/agents` has carried
`SERVER3_SESSIONS` blocks for some time; only this skill was stale.

⭐ **The two 2026-08-10 defects compounded, and each one alone looked like a quiet day:**
server4 silently returned 0 (missing `-L`) while server3 was never asked at all ⇒ the cycle
saw **13 of 75 sessions (17%)** and **210 of 934 MB (22%)** and reported no error.
**A coverage bug does not announce itself — it looks exactly like low activity.** When a
count looks low, verify the *denominator* before believing it.

⚠️ **`office` no longer runs a memo container at all** (`docker ps -a` shows none, 2026-08-10),
so `office:8000` health-checks fail by design, not by fault. server4 and server5 return
byte-identical `/documents` payloads (9,174 docs) — one global corpus, per the 2026-06-29
single-global refactor. Do not "fix" office by standing a container back up without asking.

Health-check (5s timeout): `curl -sf --max-time 5 <url>/health`. Mark unreachable as skipped, do NOT abort.

### Project DBs on NAS

```bash
find ~/code -maxdepth 4 \( -name "*.memo.db" -o -name ".memo.db" \) 2>/dev/null
```
For each, verify it has docs: `curl -sf "http://server4:8000/index?db_path=<path>&limit=1"`.

**Lost-write audit (added 2026-05-11)**: during A.3 session extraction, capture every `db_path=...` mentioned in `memo_store` / `memo_update` tool calls. After Step 1's target-list build, cross-check: any `db_path` referenced by a session but **not** in the on-disk `.memo.db` find result is a **lost write** — the session believed it stored a memo, but no DB existed at that path. Surface to email under `🗂 Lost writes — project DB not on disk` with: session UUID, host, intended path, count of affected store calls. The user can then decide whether to (a) create the missing DB and re-store, or (b) treat the writes as intentionally lost. Default policy per Ben (2026-05-11): **quantum-feed and other in-NAS projects use global ONLY** — flag any session that tries to write to a project `.memo.db` as a likely misuse.

Print the target list, then fetch every reachable target's docs once (`/documents?limit=2000[&db_path=<path>]`) and cache to `/tmp/minder/<host>.docs.json`. Every later phase reuses this cache.

---

## Phase A — Ingest [DAILY]

Skip if `--skip ingest` (or skip sub-phases with `--skip gmail` / `--skip phony` / `--skip atc`).

### A.0 — Load checkpoint (idempotency)

Search office memo for `backfill-checkpoint` and `backfill-log` tagged memos to build dedup sets:

```bash
curl -sf "http://server4:8000/documents?limit=2000" | python3 -c "
import json, sys
docs = json.load(sys.stdin)
for d in docs:
    tags = d.get('tags') or []
    if 'backfill-log' in tags or 'backfill-checkpoint' in tags:
        print('---', d.get('title'))
        print(d.get('content','')[:6000])
"
```

Build sets:
- `already_processed_sessions`: from `Session UUIDs processed:` lines matching `<uuid>@<line_count>`. Bare-uuid lines (legacy) → treat as `<uuid>@0`.
- `already_processed_memory_files`: from `Memory files processed:` matching `<host>:<project>:<relpath>@<mtime>`.
- `already_processed_gmail`: `<thread_id>@<message_count>`.
- `already_processed_phony`: `<conversation_id>@<message_count>`.
- `already_processed_voicemails`: `<voicemail_id>`.
- `already_processed_atc`: `<status_id>@<updated_at_epoch>`.

If no logs exist, all sets empty.

### A.1 — Discover Claude session logs across all 3 hosts [DAILY]

Compute cutoff: today minus `--days N`.

```bash
CUTOFF="<YYYY-MM-DD>"
touch -d "$CUTOFF" /tmp/bf_cutoff
```

**A.1.0 — Enumerate agent services first (added 2026-06-01).**

The skill historically walked `/home/ben/.claude/projects` and used hardcoded priority tiers. That missed agent-service sessions that live in a workdir ≠ name (e.g. `quantum-assistant` runs in `/mnt/nas/data/code/quantum-feed`) and let ad-hoc `-home-ben` sessions crowd out durable agent-service ones. Fix: parse `~/scripts/agents` to build the authoritative service-name → workdir map per host, then resolve each to its `/home/ben/.claude/projects/` dir.

```bash
SCRIPT=/mnt/nas/data/code/scripts/agents

# Service names (canonical, from associative-array keys):
SERVICES=$(grep -oP '^\s*\[(\w[\w_-]+)\]=' "$SCRIPT" | grep -oP '\w[\w_-]+' | sort -u)

# Service → workdir mapping is encoded in three places:
#   1. OFFICE_SESSIONS lines: "<name>|<workdir>"
#   2. SERVER4_SESSIONS lines: "<name>|<workdir>"
#   3. SERVER5_SESSIONS lines: "<name>|<workdir>"
# Extract each:
python3 -c "
import re
text = open('$SCRIPT').read()
hosts = {}
for hostmatch in re.finditer(r'(OFFICE|SERVER4|SERVER5|SERVER3)_SESSIONS\s*=\s*\(([^)]+)\)', text, re.DOTALL):
    host = {'OFFICE':'office','SERVER4':'server4','SERVER5':'server5','SERVER3':'server3'}[hostmatch.group(1)]
    entries = re.findall(r'\"([\w_-]+)\|([^\"]+)\"', hostmatch.group(2))
    hosts[host] = entries
for h, entries in hosts.items():
    for name, workdir in entries:
        # convert workdir to projects dir name
        proj = workdir.replace('/', '-')
        print(f'{h}\t{name}\t{workdir}\t{proj}')
"
```

For each (host, service, workdir, project-dir), the corresponding session logs live at `/home/ben/.claude/projects/<project-dir>/` on **that host**. Note: `home-ben` workdir (used by static admin sessions like system/cluster/network/servers) maps to project dir `-home-ben`.

**A.1.1 — Find session log files.**

> ⛔ **`find -L` IS LOAD-BEARING — DO NOT DROP THE `-L`.** As of 2026-08-09 the
> per-project dirs on **server4** are SYMLINKS into `/fast4/claude/projects/`
> (the NVMe migration). `find` does not follow symlinks by default, so the bare
> form returns **0 files on server4** — and a zero here is indistinguishable from
> "no sessions were active." Measured 2026-08-10: bare `find` → **0**, `find -L`
> → **158**; the 2-day window went **0 → 31 sessions / 440 MB**. office and
> server5 are not symlinked, so they looked fine and the failure was
> **host-specific and silent on the busiest host**.
>
> ⭐ **Positive control, run it whenever this phase reports a suspiciously low
> count:** `/usr/bin/find -L /home/ben/.claude/projects -maxdepth 2 -name '*.jsonl' | wc -l`
> must be non-zero on every host. If bare `find` and `find -L` disagree, the
> layout moved again.
>
> ✅ **`glob` is NOT affected** — Python's `glob.glob("…/projects/*/*.jsonl")`
> descends through the symlinks (verified 158 = 158, 2026-08-10), so
> `capture_miss_scan.py` and `assistant_liveness_hours.py` need no change. The
> bug is specific to shell `find`. **Verified, not assumed** — the two tools
> genuinely differ here.

```bash
ALL_HOSTS=(office server4 server5 server3); THIS_HOST=$(hostname)
for HOST in "${ALL_HOSTS[@]}"; do
  if [ "$HOST" = "$THIS_HOST" ]; then
    /usr/bin/find -L /home/ben/.claude/projects -maxdepth 2 -name '*.jsonl' \
      -newer /tmp/bf_cutoff ! -path '*/subagents/*' \
      -printf '%T@ %s %p\n' 2>/dev/null | sort -n
  else
    ssh -o ConnectTimeout=5 -o BatchMode=yes -p 4999 ben@$HOST "
      touch -d '$CUTOFF' /tmp/bf_cutoff 2>/dev/null
      /usr/bin/find -L /home/ben/.claude/projects -maxdepth 2 -name '*.jsonl' \
        -newer /tmp/bf_cutoff ! -path '*/subagents/*' \
        -printf '%T@ %s %p\n' 2>/dev/null | sort -n" 2>&1 \
      || echo "WARN: ssh to $HOST failed"
  fi
done
```

(`/usr/bin/find` rather than bare `find` also dodges the ugrep shim, which
rewrites `find` in every Bash tool call — see the global CLAUDE.md note.)

For each line (`<mtime> <bytes> <path>`), extract host / project / session UUID / size_kb / mtime / line_count. Dedup key: `<uuid>@<line_count>`.

**Skip criteria:** key in `already_processed_sessions` (no growth) AND no Phase B re-verify pending; size < 20 KB AND no prior record; subagent log; outside `--projects` filter; **hook transcript (see below)**.

> ⛔ **FILTER OUT HOOK TRANSCRIPTS — THEY ARE 97% OF WHAT `find` RETURNS.** Every
> `claude -p` hook invocation (`memo-judge`, `memo-reconciler`, …) writes its **own**
> session file into the cwd's project dir. Measured 2026-08-11, 2-day window:
> **2,431 files discovered, 73 real** — server4 alone had 1,603 of which 29 were real,
> 889 from `memo-judge` in `-mnt-nas-data-code-memo`.
>
> ⛔ **The `size < 20 KB` rule does NOT catch them** — a sampled hook transcript is
> **119 KB with 14 lines** (`{user: 1, assistant: 1}`). Bytes were a proxy for substance
> and the proxy broke: these are byte-heavy and turn-poor.
>
> ✅ **Discriminate on LINE COUNT.** The distribution is near-perfectly bimodal (server4:
> median 14, p90 14, max 34,393), so anything in 25–100 separates cleanly:
> ```bash
> xargs -d'\n' wc -l < paths.txt | command grep -v ' total$' | awk '$1>25 {print $2}'
> ```
> ⛔ **Never mine one.** Its first user turn is the judge's own prompt — ingesting it
> fills the corpus with the instrument describing itself.
>
> ⭐⭐ **AND CHECK THE DENOMINATOR WHEN IT MOVES IN *EITHER* DIRECTION.** The 2026-08-10
> rule below says to verify when a count looks *low*. That is directionally half-blind:
> this defect made the same measurement **33× too HIGH** and the low-count rule could
> never have fired. ⚠️ **An inflated count looks like COVERAGE — like good news — which is
> exactly why nobody checks it.** The cheap check that caught it: 31 sessions yesterday
> vs 1,603 today, same host, same window. Full write-up: memo `bb3d4bc3`.

**Re-mine on growth:** if `<uuid>@<old_count>` is in dedup set but `<old_count> < <current_line_count>`, process only lines `<old_count>+1` through end. Update checkpoint with new `@<current_line_count>`.

**A.1.2 — Tier sessions using the agent-service map** (replaces the old hardcoded priority tiers):

1. **TIER 1 — agent services (from A.1.0 map)**: every session whose project dir matches an enumerated agent-service workdir. Always processed up to the per-cycle budget; never starved by tier-2/3 work.
2. **TIER 2 — known-substantive project sessions**: any session in a `/mnt/nas/data/code/<repo>/` project dir not in the agent-service map (e.g. ad-hoc work in a real code repo).
3. **TIER 3 — ad-hoc / home-ben sessions**: `-home-ben` project dirs that don't map to a known service workdir. Lowest priority.

Sort: tier asc, mtime asc within tier. Budget: cap tier 3 at 25% of total sessions processed per cycle (so a busy `-home-ben` day can't starve agent-service mining).

**A.1.3 — Overseer-tag agent-service sessions.** Mark each session record with an `is_overseer` boolean: True if the agent-service map's role description for that service contains "overseer", "advisor", "monitor", "watcher", or "supervisor" (heuristic LLM-judgment OK). Currently true for: `quantum-assistant`, `cluster` (when role brief mentions monitoring), and any future `*-watcher` agents. This flag controls extraction in A.3a.

**A.1.4 — Capture-miss detection [DAILY, added 2026-07-25]** — surfaces logistics/"remember this" data that never became a dedicated recall-surfaceable memo.

> ⚠️ **USE THE WORKING IMPLEMENTATION — do not hand-write these regexes.**
> `~/.claude/skills/memo-minder/bin/capture_miss_scan.py` (run per host,
> `CAPMISS_CUTOFF=YYYY-MM-DD python3 -` over ssh; emits JSON).
> The pattern list printed below is **retained for intent only and is known-broken as
> literal regex** — see "Why the naive version fails" after the block. Corrected
> 2026-07-30 after the phase had reported nothing for multiple cycles.
>
> **Standing positive control (run it, every time):** `CAPMISS_CUTOFF=2026-07-22` on
> **office** MUST return a hit whose context contains "this is where I parked at logan".
> A detector that has never been run against a known positive is not a detector — that is
> exactly how this phase sat marked "TODO" while silently matching nothing real.

Background (**CORRECTED 2026-07-30** — the original account below was wrong in every
particular; see memo `c84dc68a`): on 2026-07-22 at **07:10 EDT** Ben sent "this is where I
parked at logan" plus a photo, via the Slack bridge. Session `e7f89519` was **alive with 920
turns that afternoon** and actively tracking his flight JBU771. It downloaded the photo,
OCR'd it, correctly extracted **Logan Central Parking Garage → "Cape Cod" section → Level 6 →
Row R**, and replied to Ben on Slack. **It simply never wrote a memo.**

So this was **not** a session-liveness failure and there was no 26.5h gap — that figure was an
artifact of measuring mtime deltas (see A.1.5). The real failure mode is a category the
taxonomy below lacks: **captured to the wrong store** — durably saved to the filesystem and
answered in-channel, but never to the one store `/recall` reads. Treat "an agent extracted the
datum and answered in-channel" as a MISS, not a hit. Recovery memo `58aff069` (3 days later);
enforcement rule spec `8a2be6f8-c546-4b18-b562-bc617de5a2fd` (tag `capture-rule`).

For each session processed in A.3, scan its user turns (this cycle's window only — reuse the A.3 line-range) for these trigger phrases (case-insensitive):

```
# PARKING (car location) — highest-value miss category
parked at | where I parked | car is at | left the car
level ?[0-9]+ | \bP[0-9]\b | row ?[A-Z]\b
spot ?[0-9A-Z]+ | space ?[0-9A-Z]+ | section ?[A-Z]\b | aisle ?[0-9]+
\bgarage\b (nearby: level|row|spot|space|section — proximity match ±40 chars)
(BOS|JFK|LGA|MHT|EWR|SFO|LAX|ORD|DCA|IAD|DFW) (parking|garage)

# BOOKINGS / PNRs / CONFIRMATIONS — Ben's are usually 5-7 char alphanumeric WITHOUT a #
confirmation:?\s*[A-Z0-9]{5,7}\b | \bPNR\b | record locator
conf ?# | confirmation ?# | booking\s*[A-Z0-9]{5,8} | voucher ?#

# ACCESS CODES (contractors, lockboxes, house, wifi)
lockbox | lock ?box | gate code | door code | garage code
combo ?[0-9]{3,} | combination ?[0-9]{3,} | keypad | wifi password
locker ?[0-9]+ | key ?[0-9]{4,} | code ?[0-9]{4,}

# CASH / PAYMENT TO PERSON (contractors, gig)
paid \$?[0-9]+ (cash )?to \w+ | owe[sd]? \$?[0-9]+ to \w+
Venmo(d)? \w+ | Zelle(d)? \w+

# SEATS / GATES (recurring travel micro-facts)
seat ?[0-9]+[A-Z] | gate ?[A-Z][0-9]+

# GENERIC "REMEMBER THIS" markers (Ben's own escalation language)
where I left | remember this | hold onto this | don't lose | important
```

**Why the naive version fails (measured 2026-07-30: 293 matches, ~0 real).** Four defects in
the block above, and two more that any obvious fix introduces:

1. **`re.I` defeats every uppercase designator.** `row ?[A-Z]` matches "**row**s"; the whole
   parking family fires on ordinary prose about table rows. Designators must be
   **case-sensitive** with a trailing `(?![A-Za-z])` guard.
2. **`\bP[0-9]\b` matches P0/P1/P2 priority labels** — ubiquitous in agent chatter.
3. **`locker ?\d+` matches "b*locker 1*"**; **`section ?[A-Z]` matches "Sections"**.
4. **`parked (at|in) [A-Z\d]` matches `parked at 64df05d1`** and `POSTURE = PARKED` — technical
   and metaphorical uses dominate the corpus. Require a **parking-domain word within ±120
   chars** (car/garage/lot/valet/terminal/airport/logan/airport codes) for anything weaker than
   "where I parked" / "left the car".
5. **Do NOT filter the whole turn for engineering keywords.** Slack-relayed operator messages
   arrive wrapped in session-brief boilerplate that mentions ATC/zones/memo, so a whole-turn
   filter discards precisely the class carrying logistics data. Scope the filter to **±200 chars
   around the match**.
6. **Do NOT cap turn length.** Same reason — the relayed brief makes real operator turns long;
   a `len > 4000` guard silently drops them. This is what hid the 7/22 Logan datum from the
   first two corrected attempts.

Also dedup identical `(project, trigger, match, context-prefix)` rows: agent retry loops emit
the same turn dozens of times and will otherwise dominate the report.

For each matching user turn, extract:
- session UUID + host + timestamp (T0)
- the trigger phrase and ±100 char context (the datum candidate)
- what the assistant did in the following ≤5 turns: did it call `mcp__memo__memo_store` OR POST /documents? If yes, capture that memo's id + title.

Classify each match:

1. **DEDICATED_HIT** — an assistant `memo_store` fired within 5 turns AND the resulting memo's title contains a plain noun+location token (e.g. "parking" + "Logan" / "BOS"). No action.
2. **BURIED_HIT** — no dedicated memo, BUT a calendar event was created/updated in the same session with the datum in `description`. This is the fragile pattern (May SF trip). Flag as `⚠️ BURIED CAPTURE` in the digest with the memo/event id.
3. **CAPTURE_MISS** — no dedicated memo, no calendar event, no matching memo authored within T0 ± 24h. Flag as `🔴 CAPTURE MISS` in the digest with the session UUID + T0 + trigger phrase context, so I (or Ben) can author a dedicated memo before the datum ages out of session context. **T+24h detection instead of T+3d discovery.**
4. **CAPTURE_MISS_RECOVERED** — no dedicated memo within the tight T0 ± 24h window, BUT a dedicated memo covering the datum was authored later (T0 + 1d to T0 + 30d), typically by a different actor or after Ben asked for it. Do NOT reclassify to DEDICATED_HIT — that would launder a real miss into a clean hit and quietly defeat the T+24h detection goal. Instead:
   - Suppress the loud 🔴 alert (no point re-alerting on an already-fixed miss)
   - Record it as a miss with a `recovery_latency_days` field (e.g. `~3d`)
   - Note the recovery memo id + recovery actor (e.g. `recovered by 58aff069, authored by assistant, latency 3d`)
   - Include in the miss-rate/latency stats footer — a miss that only ever recovered late still counts as a miss for the purpose of measuring whether the prevention layer (agent respawn floor) is working over time.

Cross-reference: for each candidate hit, memo_search corpus with the trigger-context substring (`min_score: 0.75`) using TWO windows:
- **Tight window** `after: T0-3600, before: T0+86400` — a matching memo here with the right tags (`parking` / `<airport>` / `<trip>`, or `reference` + `logistics`) → DEDICATED_HIT (covers out-of-session subagent stores).
- **Late-recovery window** `after: T0+86400, before: T0+2592000` (T0+1d → T0+30d) — a matching memo here → CAPTURE_MISS_RECOVERED, not DEDICATED_HIT.

Surface findings in Phase I email under a top-level `## 🎯 Capture-miss audit` section:
- Table of `🔴 CAPTURE MISS` rows: `date | session | host | trigger | context (60 chars)` — loud, actionable
- Table of `⚠️ BURIED CAPTURE` rows: with the buried memo/event id (so Ben can promote it to a dedicated memo if the datum matters) — warning
- Table of `📋 CAPTURE_MISS_RECOVERED` rows: `date | session | trigger | recovery_latency | recovery_memo_id | recovery_actor` — quiet, but visible. This is the "we caught it late" bucket; feeds miss-rate/latency stats.
- Stats footer line: `capture health: N same-day / M buried / K missed / L late-recovered (mean recovery latency: Xd)` — the running scorecard for whether the prevention layer is working.
- If 0 hits across all buckets, print `no capture misses detected (0/0/0/0)`.

Budget: cap at 20 hits/cycle (if more, log full list to `/tmp/minder/capture-miss.log` and show only top 20 in email). Zero-token cost when no matches — regex is O(session-text), no LLM call.

**A.1.5 — Assistant-session liveness gap [DAILY, added 2026-07-25]** — flags dead-inbox windows during operator-travel days.

> ⚠️ **USE `~/.claude/skills/memo-minder/bin/assistant_liveness_hours.py`.** The mtime method
> described below is **wrong** and was corrected 2026-07-30 (memo `c84dc68a`).

**Background — CORRECTED 2026-07-30. There was no 26.5h gap.** The claim that the office
`-code-assistant` "died at 09:38 UTC 7/22 and didn't restart until 12:16 UTC 7/23" was a
measurement artifact. Session `e7f89519` had **920 turns** on 7/22 afternoon/evening, received
Ben's parking photo, and answered it. The real defect was capture discipline (A.1.4), not
liveness. Do not rebuild a supervision/respawn-floor control on this premise.

**Two wrong ways to measure, and the right one:**

- ❌ **mtime deltas** (what this section used to specify): manufactures phantom gaps. A session
  running 7/23→7/26 has ONE mtime, at 7/26 — so sorting mtimes shows a fabricated 77h "hole"
  where the session was continuously alive. This is where the 26.5h figure came from.
- ❌ **coverage intervals** (first→last timestamp per file): reports *no* gaps, ever. `--resume`
  copies prior history into the new file, so every session claims coverage back to its earliest
  ancestor (05-27 in the current set) and the union has no holes.
- ✅ **hours containing an actual turn**: union of `timestamp[:13]` across every assistant session
  file, both dir spellings, all hosts. Immune to both artifacts. Measured 2026-07-30: largest
  true gap since 07-18 is **12h** — so the historical baseline is "no >12h gaps", and anything
  larger is genuinely novel.

Session-liveness on travel days is an upstream supervision problem (agent-supervisor's
responsibility), but memo-minder can surface a real gap so the failure is visible within 24h.

Using the hour-granular measure above, flag any gap where:

- gap > **12 hours**, AND
- the gap window intersects a **Ben-travel-day** — determined by any of:
  - a `/documents?tags=travel&tags=operator-ben` memo with `trip_dates_start` or `trip_dates_end` inside the gap
  - a calendar event on Ben's cal (`ben@sack.io`) tagged `travel` or with title containing `flight | arrive | depart | trip | vacation | airport | BOS | JFK | LGA` inside the gap
  - a memo tagged `magill-reunion | mexico-trip | sf-trip | trip-<*>` referencing dates inside the gap
- OR (fallback if no travel signal): gap > **24 hours** unconditionally on any assistant host

> ⛔ **BEFORE FLAGGING ANY GAP, RULE OUT A HOST MOVE — THIS IS THE THIRD ARTIFACT THIS
> PHASE HAS PRODUCED.** A seat that migrates hosts leaves its old transcript frozen
> forever, and a frozen transcript is **indistinguishable from a dead seat** by any
> measure taken on the old host — including the hours-containing-a-turn method, which
> is immune to the *other* two artifacts but not to this one.
>
> **Measured 2026-08-10:** the script reported no `assistant` turn after `2026-08-04T14`
> — a ~6-day "outage" — on both server4 and office. The seat was **fine**: it had moved
> to **server3** (tmux `assistant`, created Aug 7) and was writing normally. It had also
> rotated its ATC pin at 08-09 22:53, which is what exposed the contradiction.
>
> ⭐ **Two cheap disproofs, either one settles it — run them BEFORE writing a gap into the
> digest:**
> 1. `ssh <each-host> "tmux ls | grep -i <seat>"` — a live tmux window anywhere means alive.
> 2. `atc_query` / `atc_list_sessions` — a board post inside the "gap" disproves it outright.
>
> **A liveness claim derived from ONE host is not a liveness claim.** Check every host in
> `ALL_HOSTS` (which now includes server3) before concluding a seat went dark.

For each flagged gap, emit in Phase I email under `## 🕐 Assistant-session liveness gaps` with:
- host, gap-start (last mtime before gap), gap-end (first mtime after gap)
- gap duration in hours
- travel-signal source (if any) — memo id or gcal event id
- **red flag** if any ATC DM was queued to that host's assistant during the gap (query `atc_search` for messages with `to=assistant` in the gap window — a queued message with no live processor is the acute failure mode)

Budget: cheap SSH + one memo_search. No LLM call.

If any flagged gap coincides with a CAPTURE_MISS from A.1.4 with the same session UUID → cross-link them in the email (`CAPTURE_MISS during LIVENESS_GAP — this is the acute pattern from 2026-07-22`).

### A.1b — Git pulse: scan ~/code commits as memo source [DAILY]

Active development inside `~/code` is high-signal but rarely surfaces from session-log audit (most decisions happen in commits, not chat). Cheap probe: for every repo in `~/code` with > 5 commits since the cutoff (today minus `--days N`), capture `git log --since=<cutoff> --stat --format='%h|%ai|%s'` and let LLM judgment pull out durable decisions.

```bash
CUTOFF="<YYYY-MM-DD>"
cd /home/ben/code
for proj in */; do
  proj=${proj%/}
  cd "/home/ben/code/$proj" 2>/dev/null || continue
  git rev-parse --git-dir >/dev/null 2>&1 || continue
  N=$(git log --since="$CUTOFF" --oneline 2>/dev/null | wc -l)
  [ "$N" -gt 5 ] || continue
  echo "===== $proj ($N commits since $CUTOFF) ====="
  git log --since="$CUTOFF" --format='%h|%ai|%s' 2>/dev/null
done
```

> ⛔⛔ **PER-COMMIT MEMOS ARE OFF. Ben, 2026-08-11, Slack #memo: "leave in git for now."**
> This supersedes the 2026-06-01 per-commit instruction below for as long as it stands.
>
> **What to do instead, each cycle:** write ONE index memo for the window — repo/commit
> counts plus verbatim subjects — and stop. Model: memo `8ffd0a3b`. Git remains the record
> for commit rationale; `git log`/`git show` answers "why did X change" precisely, and the
> only thing given up is that it isn't semantically searchable from `/recall`.
>
> **The numbers behind the call**, so a future cycle doesn't relitigate it: 469 substantive
> commits in a 2-day window, ~230/day, which would have doubled a 9,100-doc corpus in ~6
> weeks with exactly the low-value bulk that degrades retrieval. 73% of it is the
> fast-churning experiment repos (quantum-feed, dojo, agents); only 62 of 469 were in the
> infra repos where a commit body explains a lasting decision.
>
> ⛔ **Do NOT re-raise this every cycle.** It was deferred three times before being decided;
> the standing answer is index-only. If it ever needs revisiting, the middle option already
> costed out is: per-commit for the ~10–20% of commits whose body carries real design
> rationale, gated on body length plus a substance check, reporting the keep-rate each cycle
> so it can't silently drift back to everything.
>
> ⚠️ The instruction below is retained because it is the spec you would implement *if* Ben
> reverses this — not because it is current.

**Per-commit memoization (added 2026-06-01 per Ben — SUSPENDED 2026-08-11, see above):**

For each substantive commit, write ONE memo per commit (not one rollup per repo). Title: `<repo>: <subject> (<sha>)`. Content sections:

1. **Commit header**: sha, ISO timestamp, repo.
2. **Rationale (commit body)** — full `git log -1 --format=%b <sha>`, truncated at 4500 chars. Many projects (quantum-feed, quantum-predict) write rich design rationales in commit bodies; that's already memo-worthy as-is.
3. **Session context (WHY)** — only if commit body is < 80 chars (i.e. one-line commits). For these, grep matching session logs across all 3 hosts for an "anchor phrase" (the commit subject after the `<scope>:` prefix, 60 chars max). Capture ±80 surrounding lines per match, dedup overlapping windows, cap 3 blocks @ 6000 chars each. This recovers the rationale that quantum-style autonomous agents put in conversation, not commit body.

**Substantive filter (skip these):** subject starts with `merge `, `wip`, `fixup`, `fmt`, `lint`, `typo`, `comment`, `rename `; or contains `dependency`/`bump `.

**Anchor extraction:** if subject is `qf-realtime: Stage 4 eviction in event-time, not wall-time`, anchor = `Stage 4 eviction in event-time, not wall-time` (after the `: ` split, capped at 60 chars). Anchors of <20 chars are kept whole.

**Session search:** for each commit's anchor, try each session log identified in A.1 (local + ssh to other hosts). Use `grep -nF '<anchor>' <session-path>` for line numbers, then `sed -n '<start>,<end>p'` for context. Stop after first session that yields any hits — avoids redundant context across hosts working on the same NAS-mounted repo.

**Skip writing memo if** commit body is empty AND no session context found. (Pure code-only commits with no rationale anywhere aren't memo-worthy by themselves.)

Source tag (per A.6): `git-sourced`, `repo-<projname>`, plus heuristic content tags (`refactor`, `performance`, `bugfix`, `feature`, `security`, `testing`, `infra`) derived from subject+body regex.

**Working implementation:** `~/.claude/skills/memo-minder/bin/git_pulse_with_context.py` and `~/.claude/skills/memo-minder/bin/build_commit_memos.py` (built 2026-06-01). Pipeline:

```bash
cd <repo>
REPO_NAME=<repo> python3 ~/.claude/skills/memo-minder/bin/build_commit_memos.py < <(
  python3 ~/.claude/skills/memo-minder/bin/git_pulse_with_context.py \
    <repo-path> <cutoff-YYYY-MM-DD> \
    server5:<session-path-1> server5:<session-path-2> ...
) | while read line; do
  curl -sf -X POST http://server4:8000/documents -H 'Content-Type: application/json' \
    -d "$(echo "$line" | jq '{title, content, tags}')"
done
```

Validated on quantum-feed (49 commits in 4 days → 30 substantive memos) and quantum-predict (30 substantive memos), 2026-06-01. 40 memos written total.

**Rollup fallback:** if a repo has > 30 commits in the window, still write one consolidated `Git pulse: <repo> — <YYYY-MM-DD>` summary memo IN ADDITION to the per-commit memos — gives the high-level reading order.

**Update existing per-commit memo:** if `<sha>` already appears in a memo title, skip (commits are immutable; rationale doesn't change post-merge).

**Sample triggers from 2026-05-24 → 2026-05-31 audit (illustrative of yield):**
- quantum-feed: 255 commits → multiple substantive (qf-realtime Stage 4 event-time eviction, stream_router K=8→16, qf-feed-replay channel_cap 8192→1024)
- quantum-predict: 54 commits → iter34/35 strategy specs + 24h test results
- storybook: 30 commits → entire new project
- unibrowse: 7 commits → multi-server registry + /mcp LAN-only auth

**Budget**: cap at 10 repos per cycle, prefer highest commit-count first.

Remote hosts: skip git-pulse on office/server5 (NAS is shared; ~/code = /mnt/nas/data/code is the same tree on all three).

### A.2 — Auto-memory file ingest [WEEKLY]

Same dynamic-host discovery pattern as A.1:

```bash
# -L is load-bearing on server4 — see the A.1.1 note. Without it: 0 results, silently.
/usr/bin/find -L /home/ben/.claude/projects -maxdepth 3 -path '*/memory/*.md' \
  -printf '%T@ %s %p\n' 2>/dev/null | sort -n
```

Fingerprint = `<host>:<project>:<relpath>@<int(mtime)>`. Skip if in `already_processed_memory_files`, empty, or outside `--projects` filter. Process `MEMORY.md` first per project, then linked per-topic files.

### A.2b — Auto-memory file system audit [WEEKLY]

For each (host, project) pair from A.2:

**Orphans / broken pointers:**
- `index_links` = `*.md` files referenced via `[name](file.md)` in MEMORY.md
- `disk_files` = `*.md` files in `memory/` dir (excluding MEMORY.md)
- File on disk but not in MEMORY.md → **orphan**: auto-fix by appending one-line link entry using the file's frontmatter `description`
- Line in MEMORY.md without file on disk → **broken pointer**: auto-fix by removing the line
- MEMORY.md > 200 lines → surface for manual consolidation (CLAUDE.md says lines >200 are truncated)
- Monolithic MEMORY.md ≤ 100 lines is the legacy pattern — DO NOT FLAG. Only suggest split when > 100 lines.

**Frontmatter completeness** (per topic file): required `name`, `description`, `type` ∈ user/feedback/project/reference. Missing → flag with suggested value. Body < 30 chars → auto-delete (file + MEMORY.md entry).

**Staleness** by type: feedback 180d, user 365d, project 60d, reference 180d. If body has time-bound named entities → flag. Otherwise no action.

**Memo-redundancy:** vector-search memo body content (truncated to 800 chars) at `min_score: 0.80`. Score ≥ 0.92 + type ∈ reference/project → surface delete-candidate. Type ∈ feedback/user is intentionally per-host → never reap.

Apply low-risk auto-fixes immediately (or dry-run-collect). Surface medium/high-risk in email under `🗂️ Auto-memory audit`.

### A.2c — Memo server log audit [WEEKLY]

For each host: `ssh -p 4999 ben@<host> "docker logs memo --since 24h 2>&1" > /tmp/minder/<host>.memolog.txt` (skip SSH wrapper on local host).

Parse FastAPI access logs for: `GET /documents/<id>` (direct fetch, definitive signal), `POST /search` (count only, results not logged), `POST /context`, write ops, `POST /mcp/`, `POST /admin/*`. Per unique doc id seen in GET/PATCH/DELETE: increment per-host counter.

Surface to email: top-10 hot memos by direct fetches; "touched but never read" (PATCH/DELETE without GET); cold candidates (>30d, 0 fetches, 0 writes — *weak signal* because /search results aren't logged, never auto-delete on this).

**Open enhancement** (resurface monthly): memo server should log `/search` query text + returned doc IDs and parsed MCP tool names — without this, retrieval via semantic search is invisible.

### A.2d — Mine session logs for corrections [WEEKLY]

For each session in A.1's processed set, scan extracted conversation text for **correction events** using LLM judgment (no hardcoded phrases):

- **User correction**: ben says a stated fact is wrong/outdated/replaced
- **Self-correction**: Claude restates after seeing tool output
- **Tool-result contradiction**: assistant claim contradicted by subsequent tool result in same session

Extract per event: `subject, wrong_value, correct_value, kind, session_uuid, session_date, evidence_snippet`. If correct_value unclear, capture with `null`. Write all to `/tmp/minder/corrections.json`. Phase B.4 consumes.

### A.3 — Process each source [DAILY]

Memory files first (when running weekly), then sessions (oldest mtime first).

**A.3a session text extraction (local + remote) — noise-filtered, chunked:**

The naive `lines[:250]` cap covers only ~5% of long sessions (server-admin / ha / phony / etc. routinely exceed 20k messages); pre-stripping API/retry noise is what makes a larger cap usable. Pipeline:

1. Strip messages matching: `Credit balance is too low`, `No response requested`, `Waited \d+s`, `continue the prior task`, lines starting with `<channel`, `<system-reminder>`, `<local-command-`, `<command-name>`, or shorter than 30 chars.
2. Keep per-message truncation generous: USER 1500 chars, ASST 2000 chars.
3. Emit up to 1500 substantive lines. If session yields > 1500, process in chunks of 1500 — classify each chunk independently for memo-worthy content. (A 10k-line session becomes 7 chunks; each chunk gets its own A.3c+A.3d pass.)
4. Always append a `--- META: <N> substantive lines total ---` trailer so downstream can see how much was dropped.

```bash
# Defaults (overridable by A.1.3 is_overseer):
CHUNK_LINES=1500            # substantive-lines per chunk
MAX_CHUNKS_BASE=10          # hard cap for small sessions
# Size-scaled (added 2026-06-01): for sessions > 50 MB, scale up.
# size_mb / 5 gives 20 chunks for 100MB, 70 chunks for 354MB. Sample every Nth chunk
# to stay bounded:
#   max_chunks = min(MAX_CHUNKS_BASE + ceil(size_mb/5), 60)  # hard ceiling 60
#   if total_substantive_lines > max_chunks*CHUNK_LINES: skip every floor(N/max_chunks)-th chunk
# Overseer mode (is_overseer=True): use SCORE_AND_KEEP_TOP_N instead of sequential chunking — see below.

python3 << 'PY'
import json, re, sys, os, math
PATH = '<path>'
IS_OVERSEER = False  # set by A.1.3
NOISE = re.compile(r'(Credit balance is too low|No response requested|Waited \d+s|continue the prior task)')
def is_noise(s):
    if not s or len(s) < 30: return True
    if s.startswith(('<channel', '<system-reminder', '<local-command-', '<command-name>')): return True
    if NOISE.search(s): return True
    return False
def is_tool_dump(s):
    """True if line looks like raw tool output an overseer shouldn't be scored on."""
    lines = s.split('\n')
    if len(lines) < 5: return False
    # code-fence density
    fences = sum(1 for ln in lines if ln.strip().startswith('```'))
    if fences >= 2 and len(s) < 1500: return True
    # line-break density (log dumps, file listings)
    short_lines = sum(1 for ln in lines if 0 < len(ln) < 60)
    if short_lines / len(lines) > 0.7: return True
    return False
def overseer_score(s):
    """Synthesis-marker score; require >=60 to keep."""
    score = 0
    for marker in ('## ', '### ', 'finding', 'halt-trigger', 'campaign', 'verified',
                   'skeptical', 'p0 ', 'p1 ', 'p2 ', 'blocker', 'regression',
                   'stop.', 'stop here', 'summary', 'survey'):
        if marker.lower() in s.lower(): score += 10
    if 'Stop.' in s[:100] or '**Stop' in s[:100]: score += 25  # halt-trigger header
    if s.count('|') >= 6: score += 15  # markdown tables
    if 800 <= len(s) <= 4000: score += 15
    if len(s) > 4000: score += 10
    return score

lines = []  # (ts, role, text)
for line in open(PATH):
    try: e = json.loads(line.strip())
    except: continue
    if e.get('isSidechain') or e.get('agentId'): continue
    msg = e.get('message', {})
    role = msg.get('role')
    content = msg.get('content', '')
    ts = e.get('timestamp', '')[:10]
    if role == 'user' and isinstance(content, str):
        s = content.strip()
        if is_noise(s): continue
        lines.append((ts, 'USER', s[:1500]))
    elif role == 'assistant' and isinstance(content, list):
        for b in content:
            if b.get('type') == 'text':
                s = b.get('text','').strip()
                if is_noise(s): continue
                if IS_OVERSEER and is_tool_dump(s): continue
                lines.append((ts, 'ASST', s[:2000]))

size_mb = os.path.getsize(PATH) / (1024*1024)
max_chunks = min(MAX_CHUNKS_BASE + math.ceil(size_mb/5), 60)

if IS_OVERSEER:
    # Score-and-keep-top-N: score every asst line, keep top max_chunks*5 by score
    scored = [(overseer_score(t), ts, role, t) for (ts, role, t) in lines if role == 'ASST']
    scored = [x for x in scored if x[0] >= 60]
    scored.sort(reverse=True)
    keep = scored[:max_chunks * 5]
    keep.sort(key=lambda x: x[1])  # restore chronological
    print(f'--- OVERSEER MODE: {len(lines)} substantive lines → {len(keep)} top-scored kept ---')
    for sc, ts, role, t in keep:
        print(f'[{role} {ts}] (score={sc}) {t}')
else:
    # Sequential-chunk mode (default)
    total = len(lines)
    skip_n = max(1, math.ceil(total / (max_chunks * CHUNK_LINES)))
    out_idx = 0
    for i in range(0, total, CHUNK_LINES):
        if (i // CHUNK_LINES) % skip_n != 0: continue
        out_idx += 1
        if out_idx > max_chunks: break
        chunk = lines[i:i+CHUNK_LINES]
        print(f'--- CHUNK {out_idx} (lines {i+1}-{min(i+CHUNK_LINES, total)}, skip_every={skip_n}) ---')
        for (ts, role, t) in chunk:
            print(f'[{role} {ts}] {t}')
print(f'\n--- META: {len(lines)} substantive lines total ({size_mb:.0f} MB, max_chunks={max_chunks}, mode={"overseer" if IS_OVERSEER else "sequential"}) ---')
PY
```

If extracted output has < 5 non-empty lines, skip. **For chunked sessions, run A.3c/A.3d once per chunk and aggregate before A.6.**

**Overseer-mode extraction** (added 2026-06-01) handles agent-service sessions tagged `is_overseer=True` in A.1.3. Instead of sequential chunks (which favor whatever's first in the file), it scores every substantive ASSISTANT text block on synthesis markers (markdown headers, `Stop.`/`Stopping autonomous` halt-trigger openers, "finding"/"P0"/"campaign"/"survey" keywords, markdown table density, paragraph length sweet-spot 800-4000 chars) and keeps the top `max_chunks*5` by score. Empirically validated on quantum-assistant 2026-06-01 mining run: 5 sessions (12-354 MB, 6k-115k lines) → 60 high-quality memos with 0 dedup-skips against the commit-rationale corpus. Tool-output dumps (code-fence heavy, line-break density >70%) are auto-skipped before scoring.

**Size-scaled cap** (added 2026-06-01): `max_chunks = min(10 + ceil(size_MB / 5), 60)`. For a 100 MB session that's 30 chunks (vs the old hard cap of 10); for a 354 MB monster it's 60 chunks (the ceiling) with sample-every-Nth fallback so we still cover the whole file rather than the first ~15%.

**A.3c memo-worthy content:**
- **Definite extract**: config values, ports, file paths, credentials format, API endpoints, env var names, architectural decisions with rationale, working commands/scripts, system state changes, project context, contractor/vendor info, infra topology changes
- **Consider**: tool design rationale, debugging outcomes (root cause + fix), financial data with specific numbers, scheduling commitments
- **Skip**: failed attempts (resolution captured already), ephemeral status, info obvious from code/README, conversational chatter

**A.3d audit before write:** vector-search at `min_score: 0.75` — classify as NEW / UPDATE / ALREADY-COVERED / SKIP.

### A.4 — Gmail audit [DAILY]

Skip if `--skip gmail`. Lookback = `--days N`.

**Three-layer filters:**

```
STANDARD_EXCLUSIONS = (
  "-category:promotions -category:social "
  "-from:noreply -from:no-reply -from:donotreply -from:notifications@ "
  "-subject:\"unsubscribe\" -subject:\"% off\" -subject:\"flash sale\" "
  "-subject:\"limited time\" -subject:\"last chance\" -subject:\"deal of\" "
  "-subject:\"webinar\" -subject:\"free trial\" -subject:\"introducing\""
)
NOISE_FILTER = "-from:homeserver@pushbuild.com -subject:\"SENSOR NOT RESPONDING\""
KNOWN_GOOD_SENDERS = []   # grow when ben says "always show me X"
BASE_EXCLUDE = "-in:spam {STANDARD_EXCLUSIONS} {NOISE_FILTER}"
```

Grow lists only when ben explicitly asks. Both also recorded in `feedback_memo_minder_filters.md`.

**Search axes** (each uses `BASE_EXCLUDE` unless noted):
- Catch-all: `newer_than:<N>d category:primary` (40)
- Orders/deliveries: subject:order/receipt/invoice/tracking/shipped/dispatched/delivered/"out for delivery"/"on the way"/arriving OR from:amazon/ups/fedex/usps/dhl/shop.app/shopify (30)
- Events: subject:appointment/scheduled/confirmation/booking/reservation/"see you"/invite/"calendar invitation" OR filename:ics (25)
- Contractor/property: subject:contract/quote/estimate/proposal/bid/renovation/contractor/groton/reedy (15)
- **Self/family @sack.io** (high-signal — ben confirmed 2026-05-31): `(to:ben@sack.io OR from:ben@sack.io OR from:nas@sack.io OR from:laura@sack.io OR from:family@sack.io OR to:laura@sack.io OR to:nas@sack.io) -from:noreply -from:no-reply -from:notifications@ {NOISE_FILTER}` (30). Self-mail = explicit durable-archive intent. **Auto-promote to Definite memo** any thread from `ben@sack.io` with attachment ≥ 50KB, or any subject containing `[*]` bracket tags (alerts/reports), or any nas@sack.io/calendar-notification message.
- Family: from/to:family@sack.io (15, NOISE_FILTER only) — subset of the above; keep for parity but Self/family axis is now primary.
- Reminders: subject:reminder/expir/renew/overdue/"action required"/"action needed" (10)
- Security: subject:security/alert/warning/suspicious/"unusual sign-in"/"verify your" (10)
- Allowlist (no exclusions, only if `KNOWN_GOOD_SENDERS` non-empty)

**Engagement signal:** `gmail_search(query="newer_than:<N>d in:sent", number=30)`. Capture `thread_id`s into `replied_threads`. For matched threads: promote one tier in classifier; always surface in email under "🗣 Threads you're actively engaged with"; extract commitments from ben's outgoing text.

**Build candidate set** keyed by `thread_id`. Skip threads where `thread_id@message_count` is in `already_processed_gmail`. Mark each: `replied`, `allowlisted`.

**Post-filter** with LLM judgment (NOT regex) — drop cold sales pitches, marketing-as-transactional, newsletters, 2FA codes, CI noise, transit pings. If `replied=True` or `allowlisted=True`, **never drop**.

**Classify and extract** into 4 buckets:

**Definite memo** (always create/update; per-event categories BYPASS dedup `/search`):
- **Order placed** — `vendor=, item=, $=, order#=, ETA=` — tag `order email-sourced <vendor>`
- **Delivery scheduled** — `vendor=, item=, carrier=, tracking=, ETA=` — tag `delivery email-sourced`
- **Appointment** — `what=, who=, when=, where=, action_needed=` — tag `appointment email-sourced`
- **Contractor commitment** — tag `contractor email-sourced <name>` (DO `/search` dedupe — continuing relationship)
- **Invoice/bill** — `vendor=, $=, due=, account=` — tag `invoice email-sourced`
- **Account change** — tag `account email-sourced`
- **Real security alert** — tag `security email-sourced`
- **Ben's outgoing commitment** (from `replied_threads`)

**Consider**: family decisions, recurring vendors, subscriptions, replied-but-small-talk threads. DO `/search` dedupe.

**Surface to digest only**: allowlisted senders w/ no actionable content; replied threads w/ no extractable fact.

**Per-event Definite categories must skip the `/search` dedup** — each occurrence is its own memo (Cucamelon order bug 2026-04-27: vector-similarity to a prior order misclassified a real new order as ALREADY-COVERED, then dedup permanently buried it). Dedup that matters is `<thread_id>@<message_count>`.

**Dedup-recording invariant**: only add `<thread_id>@<message_count>` to checkpoint when EITHER (a) memo created/updated, OR (b) explicitly classified Skip with reason logged under `## Gmail threads explicitly skipped (this cycle)`. "Looked at it but did nothing" must NOT silently dedup.

### A.5 — Phony SMS + voicemail audit [DAILY]

Skip if `--skip phony`.

```
phony_list_messages(limit=50)
phony_list_voicemails(limit=20)
phony_list_calls(limit=20)
```

Dedup SMS by `conversation_id@message_count` against `already_processed_phony`; voicemails by `voicemail_id`. For new/changed: `phony_get_conversation`, `phony_get_voicemail`. Calls only if duration > 30s and unfamiliar number.

**Voicemail floor (always-memo rule):** voicemail with (a) identifiable named sender (caller-ID, recognized number, or transcript opens "Hi Ben, this is X") AND (b) ≥1 sentence beyond a callback request → **always create memo** `Voicemail: <name> — <date>` with full transcript verbatim + one-line "What this is about:". Tag `voicemail-sourced contact-<normalized-name>` plus topic tags. Don't gate on actionability — voicemails from named senders are durable artifacts.

**SMS floor:** new substantive SMS thread (≥2 messages, not 2FA/delivery) → memo `SMS thread: <name or number>` with rolling summary + latest exchange.

**Call floor:** any call >30s with named contact → one-liner memo `Call with <name> — <date>, <duration>`.

**Surface to email:** sender + first 80 chars of any voicemail transcript; any SMS thread unanswered on Ben's side.

### A.5b — ATC board audit [DAILY]

Skip if `--skip atc`.

```python
mcp__atc__query_atc_statuses(zone=None, include_revoked=False)
mcp__atc__list_sessions(active_only=False, limit=50)
for zone in ["sessions", "database", "deploy", "files", "network", "k8s"]:
    mcp__atc__query_atc_statuses(zone=zone, include_revoked=True, limit=30)
```

Dedup `<status_id>@<updated_at_epoch>`. Classify (LLM judgment, NOT keyword match):
- **Decision** ("we're doing X", "moved to Y") → memo `atc-sourced decision <topic>`
- **Lock/coordination** ("running migration on DB X") → ephemeral; surface to email; memoize only if duration > 4h or repeated across cycles
- **Warning/incident** → memo `atc-sourced incident <component>` if persists ≥ 2 cycles or has named root cause
- **Coordination handoff** ("X agent now owns Y") → memo or update `atc-sourced ownership <role>`
- **Routine heartbeat** (`/cluster-heal cycle 412`) → skip

For ATC inbox/`atc_reply` history: same Phase A.4 thread treatment (dedup by message-count, extract decisions/commitments).

Surface to email under `🛰 ATC activity`: zone, agent, status, durable?; cross-cycle persistent warnings as a "watch list".

### A.5c — ATC DM thread audit [DAILY, added 2026-06-01]

Skip if `--skip atc`.

Phase A.5b covers ATC **statuses** (the queryable board). It does NOT cover the dense **DM / inter-agent message** stream — where commitments, decisions, and halt-triggers actually get negotiated. The 2026-05-31 backfill day had Ben sending ~30 Slack-bridge DMs to the `memo` agent with substantive decisions (skill patches, scope changes, reconcile approval) that the prior cycles ingested only as session-log artifacts, not as a first-class DM-sourced source.

Mine ATC DM history for named-operator threads (Ben primarily; future operators as named). Per-host (the ATC bridge runs on server4 but messages may be cached differently):

```python
# Inbox + outbox for this cycle's lookback
for zone in ['<own-session-id>', '<atc-team-channels-this-session-is-on>']:
    mcp__atc__atc_query(zones=[zone], lookback_minutes=<--days N>*1440, min_priority='info')
# Also pull DM message-history for named operators:
mcp__atc__slack_thread_history(channel='<ben-DM-channel>', thread_ts='<thread>', limit=100)
```

Dedup `<thread_id>@<message_count>` like Gmail (A.4 invariant). For each thread:

**Definite memo** (always create/update):
- **Operator decision** ("yes to all", "a", "drop X", "go with B") in response to a proposed approach → memo `Operator decision: <topic> (<YYYY-MM-DD>)` with full thread context. Tag `dm-sourced operator-<name>` plus topic tags.
- **Operator commitment** (Ben says "I'll do X by Y") → memo `Operator commitment: <X> by <Y>` — similar pattern to Gmail "Ben's outgoing commitment" in A.4.
- **Halt-trigger / scope-change directive** → memo immediately, escalate to email "Needs human review" if cross-cutting.

**Consider**: long deliberation threads where the conclusion is implicit; cross-agent debates that ended with a winning approach.

**Skip**: routine acknowledgments ("ok", "got it", "noted"), broker bounces, registry pings, automated stall-watcher reminders.

**Provenance tags** (per A.6 mandate): `dm-sourced`, `operator-<name>` (e.g. `operator-ben`), `atc-thread-<thread8>`, plus topic tags.

Validated by 2026-05-31 case: had A.5c existed, the in-band decisions ("all four patches", "drop Phase H", "do all agent services", "c then a") would have been memoed as `Operator decision: memo-minder skill upgrade roadmap (2026-05-31)` rather than buried inside the memo agent's session log.

### A.6 — Apply ingest changes [DAILY]

Auto-apply NEW and UPDATE except high-risk (UPDATE that replaces > 50% of existing).

**Mandatory provenance tagging.** Every memo created/updated by ingest MUST carry the source tag matching where it came from, so Phase I can produce per-source counters and Ben can filter retrospectively:

| Source | Required tags |
|---|---|
| Session log | `session-sourced`, `session-<uuid8>` (first 8 chars of UUID), `host-<host>` |
| Gmail thread | `email-sourced` (already specified in A.4) |
| Phony SMS | `sms-sourced` (A.5) |
| Phony voicemail | `voicemail-sourced` (A.5) |
| ATC board (statuses) | `atc-sourced` (A.5b) |
| ATC DM thread | `dm-sourced`, `operator-<name>`, `atc-thread-<thread8>` (A.5c) |
| Auto-memory file | `automemory-sourced`, `project-<projname>` |
| Git pulse (A.1b) | `git-sourced`, `repo-<projname>` |

If an update merges signal from multiple sources, append all applicable source tags (don't replace existing ones).

```bash
# NEW
curl -sf -X POST http://server4:8000/documents \
  -H 'Content-Type: application/json' \
  -d '{"title": "...", "content": "...", "tags": ["session-sourced","session-abc12345","host-server4",...]}'
# UPDATE — merge cleanly; DO NOT replace, DO NOT append; rewrite as a clean current document
curl -sf -X PATCH http://server4:8000/documents/<id> \
  -H 'Content-Type: application/json' \
  -d '{"content": "...", "tags": [...existing..., "session-sourced", "session-abc12345"]}'
```

Track all created/updated IDs + titles + source for email + checkpoint.

### A.6.5 — Reconcile-lite on this cycle's new memos [DAILY, added 2026-06-01]

Skip if `--skip reconcile` is passed.

The full Phase D (and B.3 supplant) runs WEEKLY across the whole corpus. But burst cycles (e.g. the 2026-05-31 manual backfill that added 162 memos in 12h) create immediate duplicate/supersede risk that shouldn't wait until Sunday. This DAILY mini-reconcile is tightly scoped:

1. Build the "recent set" = every memo created/updated in THIS cycle (captured by A.6's `created_updated_ids` tracker).
2. For each recent memo, vector-search corpus at `min_score: 0.80`. Track candidate pairs (recent_id, other_id, score). Skip both-recent (dedup the pair); skip pairs where either is tagged `backfill-log`/`backfill-checkpoint`/`sync-log`/`maintenance`.
3. For each candidate pair, sorted by score desc, classify (LLM judgment, NOT regex):
   - **TRUE_DUPLICATE**: same fact, no new info in newer → DELETE newer (preserves the canonical ID)
   - **SUPERSEDE**: newer is current state, older is history → prepend `> Superseded by <newer-id> (<date>). See current state there.` to older's content
   - **REFINE**: newer adds detail, older is summary → MERGE newer into older's id, DELETE newer
   - **DISTINCT**: same general topic, different facts (e.g. multiple halt-trigger findings on different days) → leave both, append `<!-- See also: <other-id> -->` to the older
   - **FALSE_POSITIVE**: skip

4. Hard caps per cycle: DELETE 8, REFINE 6, SUPERSEDE 12, DISTINCT 25, total 50. Priority: TRUE_DUPLICATE > SUPERSEDE > REFINE > DISTINCT.

5. Validated 2026-06-01 (167 recent memos): 51 candidate pairs at ≥0.80 → 0 true dupes, 3 supersedes, 5 refines, 25 distincts, 1 supplant. Net 1178 → 1173. Conservative is correct — most ≥0.80 hits are different timestamps of the same overseer in different sessions, not actual duplicates.

The WEEKLY full Phase D still runs (Sunday) over the entire corpus to catch cross-week drift the DAILY pass misses.

### A.7 — Write checkpoint memo per host AND reap old logs [DAILY]

For each host:

1. Identify reap candidates: tagged `backfill-log` (or both `backfill-log` + `backfill-checkpoint`) AND (a) "no-op" (`0 new memos`, `empty delta`, `N=0 new`) regardless of age, OR (b) older than 14 days. **Always keep 3 most recent** as continuity baseline.

2. Build the consolidated checkpoint: union of every dedup key in this cycle's processed set + every key from each to-be-reaped log on this host (so reaping preserves dedup).

3. POST checkpoint:
   - Title: `Backfill checkpoint — <host> — <YYYY-MM-DD HH:MM>`
   - Tags: `["backfill-log", "backfill-checkpoint", "maintenance", "<host>"]` — `backfill-log` MUST be present so future-cycle dedup finds it.
   - Content sections: `## Session UUIDs already processed`, `## Memory files already processed`, `## Gmail threads already processed`, `## Gmail threads explicitly skipped (this cycle)`, `## Phony conversations already processed`, `## ATC statuses already processed`, `## Phony voicemails already processed`, `## Reaped logs (consolidated here)`.

4. Verify POST returned an ID.

5. DELETE each to-be-reaped log on that host.

If `--dry-run`, print the plan.

---

## Phase C1 — Per-project daily reconciler (INLINE — YOU do this) [DAILY, IMPLEMENTED 2026-07-28]

Skip if `--skip perproject` is passed.

**IMPORTANT — architecture rule (Ben's directive 2026-07-28):**

C1 is done BY YOU (the memo session) INLINE during the daily cycle. Only permitted mechanism for the judgment is **this interactive Claude Code session** doing it directly. Explicitly BANNED:
- headless / `-p` subprocess invocations (banned outright by Ben 2026-07-28 — do not spawn under any flag combination, no exceptions)
- OpenRouter API or any other metered-key HTTP LLM call
- workflow/subagent fan-out that spawns fresh sessions
- any pattern that isn't "the memo session reads memos with its own tools and applies mutations"

YOU read the memos with `mcp__memo__memo_list`/`memo_search`, YOU judge them, YOU apply mutations via HTTP PATCH/DELETE. Same interactive Claude Code pattern the rest of the fleet uses.

### C1 execution (during each daily cycle)

**Discover active projects.** From the session-log enumeration in A.1, extract distinct project dirs (last 72h activity). Cap at 15 projects/cycle.

**For each active project (in order):**

1. **Fetch project memos** — `mcp__memo__memo_list(tags=[<slug>], limit=200)` OR HTTP `GET /documents?limit=15000` client-side filter by tag intersection with `{f"project-{slug}", f"repo-{slug}", slug}`.
2. **Skip if already-reconciled today** — if any project memo carries today's `reconciled-YYYY-MM-DD` tag, L1 SessionStop handled it. Move on.
3. **Skip if < 3 memos** — nothing to reconcile.
4. **Select the 15 most-recently-updated project memos.** Sort by `updated_at` DESC. This is the fresh slice where new-vs-old contradictions actually appear. For huge tag pools (e.g. `quantum-feed` has 3k+ memos), don't try to judge the whole set — the freshest slice is where invalidations bite.
5. **YOU judge each memo** — look for cases where a newer memo makes an older one stale. Classify each candidate:
   - `KEEP` — still current, no action
   - `SUPERSEDE_BANNER` — older memo describes state that a newer memo replaced. Prepend `> Superseded YYYY-MM-DD by <newer-id>: <reason>` banner to the older's content. Tag `reconciled-<date>` + `superseded`.
   - `PATCH` — older memo has a stale specific detail (IP, version, config, price) corrected by newer. Rewrite the specific field.
   - `DELETE` — older memo is fully redundant with a newer authoritative memo AND has no historical value. Rare — bias toward `SUPERSEDE_BANNER`.
6. **Apply mutations via HTTP PATCH/DELETE.** Cap: 6 mutations per project per cycle.
7. **Log the outcome** — count of proposed/applied per project into a running per-cycle tracker for the digest.

### Skill patterns (paste-able)

```python
# Fetch + filter
import urllib.request, json
r = urllib.request.urlopen("http://server4:8000/documents?limit=15000", timeout=60)
docs = json.loads(r.read())
project_tags = {f"project-{slug}", f"repo-{slug}", slug}
project_memos = [d for d in docs if set(d.get("tags") or []) & project_tags]
project_memos.sort(key=lambda m: m.get("updated_at", 0), reverse=True)
recent = project_memos[:15]

# Skip guards
today = datetime.now().strftime("%Y-%m-%d")
if any(f"reconciled-{today}" in (m.get("tags") or []) for m in project_memos): continue  # L1 handled
if len(project_memos) < 3: continue

# YOU (memo session) read `recent` and decide mutations. Then:
# PATCH for SUPERSEDE_BANNER
body = json.dumps({"content": f"> Superseded {today} by {newer_id[:8]}: {reason}\n\n{original}",
                   "tags": sorted(set(existing_tags) | {f"reconciled-{today}", "superseded"})}).encode()
req = urllib.request.Request(f"http://server4:8000/documents/{older_id}", data=body,
    headers={"Content-Type": "application/json"}, method="PATCH")
urllib.request.urlopen(req, timeout=15)
```

### Ordering

C1 fires AFTER A.6.5 reconcile-lite (this-cycle new memos) and AFTER A.7 checkpoints (idempotency artifact preserved). Sequence: A.1 → A.3 → A.6 → A.6.5 → A.7 → **C1** → G → Step 11 → Step 11.5.

### Complementarity

- **L1 SessionStop hook** — reconciles the SESSION that just finished (fires post-session-end, only sessions with >50 substantive turns; needs `MEMO_RECONCILER_ENABLED=1` in env — verified wired 2026-07-28).
- **C1 (this)** — reconciles projects the memo session sees active in A.1, in case L1 missed them or synthesis across sessions is needed.
- **Weekly Phase D** — full-corpus vector-similar-cluster reconcile, Sundays only.

Budget guidance: 15 projects × ~2k tokens judgment each = ~30k tokens/day. This is bounded by the daily cycle's total budget.

---

## Phase B — Investigate (verify claims live) [WEEKLY]

Skip if `--skip investigate`.

### B.1 — Classify each memo by verifiable claim type

| Claim | Signature | Probe |
|---|---|---|
| Server IP | `\d+\.\d+\.\d+\.\d+` near hostname | `ping -c 1 -W 2 <ip>` |
| LAN service | `http://(host):port` | `curl -sf --max-time 3 <url>/health` (try `/health`, `/`, `/api/health`) |
| SSH host | `ssh -p 4999 ben@<host>` | `ssh -p 4999 -o ConnectTimeout=3 -o BatchMode=yes ben@<host> true` |
| File path | `/mnt/...`, `/home/...`, `~/code/...` | `ls -d <path>` (locally if office, via SSH otherwise) |
| Cron entry | quoted cron line | SSH host: `crontab -l \| grep -F <distinctive>` |
| Container | `docker compose ... <service>` | SSH host: `docker ps --filter name=<service>` |
| K8s resource | `kubectl ... <ns>/<name>` | `k-barn get <kind> <name> -n <ns> -o jsonpath='{.status.phase}'` |
| GitHub | `github.com/<o>/<r>/(pull\|issues)/<n>` | `gh pr view <url> --json state,title` |
| DNS | `<name>.<tld>` | `dig +short +time=2 +tries=1 <name>` |
| Service port | `port (\d+)` near hostname | `nc -z -w 2 <host> <port>` |

**Budgets per cycle**: B.1 live probes 200; B.3 supplant scans 50; B.4 corrections 50; B.5 provenance 30.

**Prioritize**: (1) memos > 30 days, (2) tagged `infrastructure|network|k8s|barn-cluster|contact|contractor|family`, (3) shortest content first.

**Probe parallelism**: batches of 10–20 per Bash call.

**Shared-MAC ARP entries** (gateway proxying many IPs to one MAC) → treat as UNREACHABLE, not VERIFIED.

### B.2 — Print live-probe summary

```
[INVESTIGATE] Probed N memos across M targets
  VERIFIED:     N    CONTRADICTED: N    UNREACHABLE: N    N/A: N
```

### B.3 — Supplant detection (built fresh per cycle)

Scan memos for replacement language using LLM judgment (seed phrases, NOT regex): "X replaced Y", "Y was replaced by X", "superseded by", "migrated from X to Y", "X is now Y", "decommissioned X", "X retired", "X is the new Y", "X took over from Y", "live as of <date>".

Extract: `old_entity, new_entity, scope_qualifier, as_of_date, source_memo_id, confidence`. **Respect scope qualifiers** — "VyOS replaced EdgeRouter as primary LAN router" applies to that role only; Router B may still legitimately be EdgeRouter. Dedup map by `(old_entity, new_entity, scope_qualifier)`.

For each tuple, scan every other memo for old_entity in matching context. Classify:
- **Stale assertion** within scope → propose UPDATE replacing references with new_entity (preserve explicit historical mentions like "before VyOS, Router A was EdgeRouter")
- **Historical/context-only** → leave alone
- **Out-of-scope** → leave alone

Skip memos tagged `historical`, with `[HISTORICAL]` in title, or with explicit "superseded by" line at top.

Auto-apply high-confidence + clean find/replace; flag medium-confidence in email.

### B.4 — Correction-driven re-verification

Read `/tmp/minder/corrections.json`. For each tuple:

1. Vector-search memos for `<subject> <wrong_value>` at `min_score: 0.75`.
2. Read each hit; assess if it actually claims `subject = wrong_value`.
3. If yes:
   - Known correct_value + recent (within `--days N` or last 60d, whichever larger): UPDATE with provenance `"corrected per session <uuid> on <date>"`.
   - Unknown correct_value: mark **suspect — needs human verify**. Surface to digest. No auto-edit.
4. **Live probe overrides session correction** if Phase B.1 already probed this fact.

Auto-apply: user-correction events + tool-contradiction events. Self-corrections from Claude are medium-confidence — flag unless live probe corroborates.

### B.5 — Provenance audit (catch hallucinations)

Identify high-stakes unverifiable claims (LLM judgment): phone numbers, mailing addresses, contractor commitments, dollar amounts, deadlines, names with roles. Costly if wrong, no probe possible.

For each, search corroboration in: other memos (vector at 0.7), Phony, Gmail (90d), auto-memory files, the memo's own ingest tag (`email-sourced`/`sms-sourced`/`voicemail-sourced` IS corroboration).

| Corroboration | Action |
|---|---|
| ≥2 independent sources agree | Corroborated — no action |
| 1 source different from origin | Lightly corroborated — no action |
| Only the memo, but ingest-tagged | Sourced — the tag IS the source |
| Only the memo, no source/verification | **Uncorroborated** — flag in digest as possibly hallucinated. NEVER auto-delete. |
| Other sources actively disagree | Contradicted — propose update from most-corroborated value |

**Never auto-delete** on absence of corroboration: source might just not have been ingested.

---

## Phase C — Conflict detection [WEEKLY]

Skip if `--skip investigate`.

### C.1 — Extract structured claims with **explicit binding patterns**

Loose word-adjacency generates false positives. Match only:

| Pattern | Example |
|---|---|
| `<host>: <value>` | `server4: 192.168.1.168` |
| `<host> = <value>` | `nas = 192.168.1.207` |
| `<host> (<value>)` | `kibo (192.168.1.55)` |
| `<host> @ <value>` / `<host> at <value>` | `gpu1 @ 192.168.1.57` |
| `**<host>**: <value>` | bolded |
| Markdown table — subject in **first cell only** | `\| server4 \| 192.168.1.168 \|` |

**False-positive guards:**
1. Markdown tables: subject MUST be the first cell (`^\|\s*<host>\s*\|`). Don't match `<host>` in later cells (those are usually parent references).
2. Hyphenated derivatives are distinct: `gpu1` vs `gpu1-tesla`, `server4` vs `server4-iDRAC` are separate subjects.
3. Multi-IP rows are legitimate: `| nas | eth0=.207, eth4=.11 |` is dual-NIC, record as `subject=<host>, ip=set` not "conflict per pair".

Restrict `<host>` to canonical list: server4, server5, office, kibo, vyos, omada, router-a, router-b, gateway, starlink, octoprint, homeserver, nas, barn, greenhouse, worker0..workerN, gpu0..gpuN, plex, nas-x520. Adding requires manual decision.

**Skip claim extraction from memos tagged**: `memo-minder`, `design-decision`, `design-rationale`, `lessons-learned`, `open-enhancement`, `backfill-log`, `backfill-checkpoint`, `sync-log`, `maintenance`, `audit`, `audit-report`, `cycle-report`. These describe the system and re-trigger false-positive loops if treated as factual claims.

Focus on: IPs, ports, phone numbers, email addresses, prices, commitment dates, hostname-to-purpose mappings.

### C.2 — Group + report

Group by `(subject, predicate)`. Groups with >1 distinct value = conflict candidate. Sort by source memo `updated_at` desc — newest presumed authoritative unless Phase B probe overrode.

Phase-B-verified conflicts → high-confidence UPDATE. Both-unprobed → flag for human.

---

## Phase D — Reconcile near-duplicates [WEEKLY]

Skip if `--skip reconcile`.

For each target, build similarity graph from cached docs (content truncated to 800 chars) at `min_score: 0.78`. Track `{min(id_a, id_b), max(id_a, id_b)}` to dedupe.

Build connected clusters. For each multi-doc cluster, sort by `updated_at` desc. Read all members:
- **Versioned duplicate** (newer is superset) → `supersede`: patch newer if older has unique info, then delete older
- **Overlapping topic** → `merge`: synthesize anchored to newest member's framing
- **False positive** → skip

Draft merged content before reporting.

---

## Phase E — Curate quality + tags + staleness [WEEKLY]

Skip if `--skip curate`.

**Auto-fix zero-token docs first** (always, no approval): `curl -sf -X POST <base_url>/admin/recount-tokens[?db_path=<path>]`.

**Re-use cached doc list.** **Global tag vocabulary pass**: collect unique tags, identify synonyms (`k8s` vs `kubernetes`, `docker` vs `containers`), choose canonical forms.

| Check | Flag when |
|---|---|
| No title | `title` null/empty |
| Vague title | "Notes", "Info", "Misc", "Update", "Reminder" |
| No tags | `tags: []` |
| Non-canonical tag | uses synonym |
| Missing obvious tag | content clearly belongs to topic |
| Too terse | < 40 tokens |
| Oversized | > 2000 tokens |
| Stale operational | > 30 days: prices, contacts, active status |
| Stale project state | > 60 days: current work, pending tasks |
| Stale how-to | > 120 days: procedures |
| Stale reference | > 180 days: configs, credentials format |

Compute age from `max(created_at, updated_at)`. **Phase B verification overrides age** — VERIFIED memo is not stale regardless of age.

### E.1 — Synthesize purchasing profile

Roll up per-order memos created in A.4 into a single durable profile.

1. List candidate orders on office: `curl -sf http://server4:8000/documents | jq '[.[] | select(.tags | index("order") or index("delivery"))]'`. Skip if < 5 candidates.
2. Search existing profile (tag `purchasing-profile`); fetch current content if present.
3. **LLM-synthesize** (NOT regex): group by category (coffee, electronics, household, outdoor, kitchen, books — let categories emerge); per category identify preferred vendors, brand/model preferences, recurring items + cadence, typical price band; note vendor patterns; surface notable changes.
4. Write/update: title `Ben's purchasing profile (rolling)`, tags `purchasing-profile preferences global-scope`, footer `Synthesized from N order memos through <date>`. PATCH if exists, POST if not.
5. **Do NOT delete the underlying order memos** — they're the source of truth for next synthesis.
6. `global-scope` tag → Phase G replicates to server4/server5.

---

## Phase F — Reap (non-log) [WEEKLY]

Skip if `--skip reap`. Backfill-log reaping happens in Phase A.7 (daily).

- **F.1 Phase B contradicted-and-replaced**: patch-overwrite (preserves ID).
- **F.2 Superseded versioned duplicates**: from Phase D, delete older.
- **F.3 Sync-log reaping**: `sync-log` tagged > 14 days → delete; keep 3 most recent.
- **F.4 Ephemeral stubs**: tokens < 20 AND content matches `TODO:`/`Reminder:`/`Check on`/`Need to`/"in progress"/"WIP". OR tokens < 40 AND no title AND no tags AND created > 14d ago.
- **F.5 Empty/zero-content**: empty/whitespace; URL-only with no surrounding context.
- **F.6 Fingerprint memos** (title contains `-fingerprint` OR tagged `ephemeral`): per-host ephemeral worker state. Reap when `max(created_at, updated_at)` is > 14 days ago — the originating `/loop` cron is effectively dead and a restart would just refresh the fingerprint. Don't reap fresh fingerprint memos (< 14d) — they're working as intended.

---

## Phase G — Cross-server sync [DAILY]

Skip if `--skip sync` or `--host` is single server.

**Global scope** (in-scope when ANY apply):
- `global-scope` tag (authoritative)
- Tags: `purchasing-profile`, `preferences`, `contact-*`, `voicemail-sourced`, `sms-sourced`, `email-sourced`, `atc-sourced`
- LLM judgment: network infra, shared hardware (barn cluster, iDRAC, switches), credentials/access, contacts/contractors/family, house/property, cross-server tools, decisions w/ cross-cutting impact

**Server-specific** (do NOT sync): `quantum-feed`/`quantum-reactor`/`quantum-dojo` ops, per-server proxy configs, `backfill-log`/`backfill-checkpoint`/`sync-log` tagged, anything tagged `host-<name>` or `local-only`, **any memo whose title contains `-fingerprint`** (per-host ephemeral state from `/loop` audit workers — see `memo-maintain`), and **anything tagged `ephemeral`**.

For each global memo:
1. **Vector-search match (PRIMARY)** at `min_score: 0.80` using content (800-char truncated). Result ≥0.80 = equivalent on target, even if titles differ.
2. **Title fuzzy match (SECONDARY, fallback only)**: normalized exact equality only — punctuation differences should NOT match. Use vector for semantic equivalence.
3. **Missing**: POST to target preserving content as-is.
4. **Diverged**: most-recently-updated source = authoritative; PATCH target. **Do NOT POST a new memo** — that creates duplicates.

**Critical**: title-only matching is a duplicate-factory — when titles get edited, title-match fails and POST creates a copy. Always vector-search first.

Auto-apply copies. Pause on diverged PATCHES if proposed content replaces > 40% of existing.

**Sort**: by `updated_at` desc — newest wins when there are multiple recent edits.

### G.full-sweep [WEEKLY only]

Reactive sync misses memos that never changed. Periodic sweep:
1. Pull every memo from each host: `GET <host>/documents`.
2. Classify each per global-scope criteria above.
3. Build 3-host coverage matrix keyed by canonical content-hash (or vector-match ≥0.80):
   - Present on all 3 → no action
   - Present on 1 or 2 → enqueue copy (PATCH if vector-match w/ different content; POST if missing)
   - Diverged → most-recently-updated authoritative; PATCH the others; log divergence to email
4. Apply queue subject to >40% PATCH-pause.
5. **Rate-limit**: cap at ~50 memos/cycle. Resume next cycle.

### G.email — Per-host syndication delta

| Host | Total | Global-scope | Out-of-sync | Copies sent | Patches sent |

Drift over multiple cycles is itself a signal.

---

## Phase H — REMOVED (2026-05-31)

Project-DB promotion was a no-op for the entire skill lifetime — `find ~/code /mnt/nas/data/code -name '*.memo.db'` returned empty at audit. Globals-only is the working model. If project DBs are ever introduced, restore from git history of this file.

---

## Step 9 — Consolidated proposal review

```
══════════════════════════════════════════
FULL CYCLE PROPOSALS
  Ingest:      N sessions, N memfiles, N gmail, N phony, N atc, N git-repos → N new, N updates
  Investigate: N verified, N contradicted, N unreachable
  Conflicts:   N detected (N high-conf updates, N flag-for-human)
  Reconcile:   N merges across M targets
  Curate:      N proposals (X low, Y med, Z high)
  Reap:        N deletes (non-log), N patch-overwrites
  Sync:        N copies, N patches
══════════════════════════════════════════
```

If `--dry-run`, print `DRY RUN — no changes applied.` and skip to Phase I.

**Auto-apply low+medium risk; pause only for high-risk** (deletes that aren't logs/stubs/superseded, splits, > 40% rewrites, conflicts where neither value was probed). Cron mode (no TTY) → default `skip-high` after 30s.

---

## Step 10 — Apply approved changes

```bash
curl -sf -X PATCH <base>/documents/<id> -H 'Content-Type: application/json' -d '{...}'
curl -sf -X DELETE "<base>/documents/<id>[?db_path=<path>]"
curl -sf -X POST <base>/documents -H 'Content-Type: application/json' -d '{...}'
```

Track every successful op for the email report.

---

## Phase I — Email report [DAILY]

Skip ONLY if `--no-email` or `--skip email` is explicitly passed. Send via `gmail_send` to `ben@sack.io` from `ben@sack.io`.

**The email is the daily heartbeat.** Send it on EVERY daily cycle, including no-op cycles where nothing changed. A "+0 edits=0 -0" digest is still valuable — it confirms the cycle ran cleanly and the dedup state is current. Do NOT decide on your own to skip the email because "activity was minimal" or "prior checkpoint sufficient." If the user wanted no-email-on-no-op, they would have said so.

**NEVER ask the user for confirmation to send.** This skill always runs from cron (no TTY, no human at the keyboard). Asking "Should I send this?" or "Want me to skip the email?" is the same as not sending — the process exits with the question unanswered and the digest is lost. Just call `gmail_send` directly. The `--permission-mode bypassPermissions` flag in the cron line means tool calls don't need approval; this directive means **conversational** asking is also forbidden.

**Subject**: `Memo Minder Daily Digest -- <YYYY-MM-DD> -- +<Nadded> edits=<Nedited> -<Nremoved> [<src-breakdown>]` where `<src-breakdown>` is a compact per-source counter like `s=3 g=5 sms=1 vm=0 atc=0 dm=2 git=1` (s=session, g=gmail, sms=phony-sms, vm=voicemail, atc=atc-board, dm=atc-dm-thread, git=git-pulse). Only includes non-zero sources. Prefix `[DRY RUN]` / `[ERRORS]` / `[GMAIL: N items needing attention]` as applicable.

**Use ASCII-only characters in the subject.** The gmail MCP does not MIME-encode non-ASCII subject headers — em-dashes (`—`), pencil emoji (`✏`), and other Unicode chars cause the email to display with subject "No Subject" in Gmail. Use `--` instead of `—`, `edits=N` instead of `✏N`. The body/html_body can use full Unicode (those are properly encoded).

**Body**: the THREE big lists (added / edited / removed) are the point. Include EVERY memo affected, no truncation. Empty list → `<p>None.</p>`.

Sections (in order):
1. `<h2>Memo Minder Daily Digest</h2>` + small header (date, run window, sources scanned)
2. **Per-source breakdown table** (NEW — header table immediately after the digest title):

   | Source | Added | Edited | Removed | Notes |
   |---|---|---|---|---|
   | session-log | N | N | N | N sessions scanned, N substantive after noise filter, N overseer-mode |
   | gmail | N | N | N | N threads processed, N skipped as noise, N from @sack.io self-mail axis |
   | phony-sms | N | N | N | N conversations |
   | phony-voicemail | N | N | N | N transcripts |
   | atc-board | N | N | N | N statuses |
   | atc-dm | N | N | N | N operator-threads |
   | git-pulse | N | N | N | N repos, N commits with body-only memos, N with session-WHY context |
   | auto-memory | N | N | N | N files |

   This is the most important addition: without it, the daily digest can hide a regression in a single source (e.g. session-log audit produced 0 for weeks — happened May 2026).

3. `<h3>📥 Memos added (<b>N</b>)</h3>` table: id / title / tags / host / **source** (now derived from the `*-sourced` tag) / 1-line snippet
4. `<h3>✏️ Memos edited (<b>N</b>)</h3>` table: id / title / changed / old → new / phase reason / source
5. `<h3>🗑 Memos removed (<b>N</b>)</h3>` table: id / title / tags / reason (e.g. F.1 / F.3 / A.7 etc.)
6. `<h3>📬 Personal items worth your eyes (no memo created)</h3>` — voicemail callbacks, unanswered SMS, gmail items
7. `<h3>🎯 Capture-miss audit</h3>` — from A.1.4. Tables of `🔴 CAPTURE MISS` (logistics/"remember this" data with no dedicated memo) and `⚠️ BURIED CAPTURE` (datum lives only in a calendar-event description). Empty section → `<p>No capture misses detected.</p>` (still print — silent success is important negative signal).
8. `<h3>🕐 Assistant-session liveness gaps</h3>` — from A.1.5. Any `-code-assistant` gap >12h during a Ben-travel-day (or >24h unconditionally). Include host / gap-start / gap-end / duration / travel-signal source / red flag if any ATC DM was queued during the gap. Empty section → omit heading entirely (this is expected on most days).
9. `<h3>🚨 Needs human review</h3>` — Phase B/C contradictions and possibly-hallucinated claims
10. `<details><summary>📊 Stats footer</summary>` — phase counts, per-host doc/token table, checkpoint IDs, errors

Send:
```
atc_subscribe(zones=["memo-minder"])  # idempotent, run once per cycle
gmail_send(to="ben@sack.io", from_addr="ben@sack.io",
           subject="...", body=<plain-text fallback>, html_body=<html>,
           custom_headers={"Reply-To": "ben+atc-memo-minder@sack.io"})
```

**Reply-To hook is mandatory.** Use the **literal string** `memo-minder` in Reply-To and subscribe to the `memo-minder` zone. When Ben replies to the digest ("forget memo X", "merge these two", "what did you mean by Y?"), the reply routes to the `memo-minder` zone → next cycle's correction-mining phase picks it up. See `gmail-send` skill §5.

**`body` is REQUIRED and MUST NOT be empty** (gmail MCP rejects empty plain-text body silently). Render a plain-text version of the digest — the same 3 sections (added/edited/removed) as a flat list with `id  title  reason`, each per line. If empty, render `(none)` rather than empty string.

After the call, **verify the result is non-empty** — the MCP returns the new gmail message ID on success and empty string on failure. If empty, retry once with a simpler body; if still failing, log the error and continue.

---

## Step 11 — Cycle log + ATC revoke

Print final summary:
```
════════════════════════════════════════════
CYCLE COMPLETE — <date>  (duration: M minutes)
  Targets:     N processed, M unreachable
  Mode:        <DAILY | FULL>
  Ingest:      N sessions, N memfiles, N gmail, N phony → N new, N updated
  Investigate: <skipped weekly | N verified, N contradicted, N unreachable>
  Conflicts:   <skipped | N resolved, N flagged>
  Reconcile:   <skipped | N merges>
  Curate:      <skipped | N changes>
  Reap:        N backfill-log → checkpoint, <N other deletes | skipped>
  Sync:        N copies, N patches
  Email:       sent | skipped | failed
  Next cycle:  tomorrow at 06:17 EDT via cron
════════════════════════════════════════════
```

Append to `~/.memo/minder.log`:
```bash
mkdir -p ~/.memo
echo "<ISO> | mode: <DAILY|FULL> | duration: Nm | ingest: ... | investigate: ... | sync: ... | email: <status>" >> ~/.memo/minder.log
```

Revoke ATC status from Step 0.

### Step 11.5 — Daily digest DM (RETIRED 2026-07-29; the host-cron that replaced it RETIRED 2026-08-21)

⛔⛔ **THERE IS NO DAILY DIGEST ANY MORE. DO NOT REINSTATE ONE HERE.** Ben retired the host-cron
on 2026-08-21 12:10 EDT (*"retire it"*) after asking what memo-minder was doing, because the
digest printed zeros every morning while 100+ memos/day were being written: its per-project table
keys on a `repo-`/`project-` tag that **0 of the last 103 memos carried**, the convention having
been turned off on 08-11. Cron line is commented (not deleted); scripts still work; full reasoning
in memo `f1303326`.
⚠️ **Ben now receives NOTHING daily from memo, by design.** The `11,41 * * * *` watch loop still
messages him on a real problem, and this cycle still runs at 06:17 and writes memos.
⛔ Do NOT "fix" that by adding a digest back at Step 11.5 — the reason below (one DM, not two) is
now moot, but the newer reason stands: **a report nobody can act on is worse than no report**, and
the attribution it wanted is not recoverable after the fact (70 of 103 memos carry no `*-sourced`
tag). That is a write-time problem, not a reporting one.

---

**Historical, kept for the rationale — DO NOT run a digest DM from Step 11.5.** Ben clarified 2026-07-29 11:21 EDT that he wants ONE daily digest DM to the #memo Slack channel — not two. The single-source-of-truth is the **host-cron script** `/mnt/nas/data/code/memo/scripts/memo-daily-digest-dm` at 06:30 EDT (system crontab entry, survives session death).

Rationale for choosing host-cron over Step 11.5:
- Durability: system crontab fires even if the memo Claude session dies (the whole point of the 2026-07-05 redesign lesson learned via the 2026-07-23 cron-gap incident).
- Dedup: Step 11.5 was firing a second DM at ~06:48 that duplicated the host-cron 06:30 fire.

⛔ SUPERSEDED 2026-08-21 (the script is no longer scheduled) — if the digest script needs enhancement (mutation counts from this cycle, additional flags, richer TL;DR), edit `/mnt/nas/data/code/memo/scripts/memo-daily-digest-dm` (bash wrapper) or `/mnt/nas/data/code/memo/scripts/memo-daily-digest` (Python analyzer). Do NOT re-add the Step 11.5 code path here.

---

## Guidelines

- **No hardcoded entity/pattern lists.** Supplant relationships, correction phrases, noise patterns derived per-cycle from current state via LLM judgment. Seed examples are illustrative, not regex. Exceptions: `NOISE_FILTER` and `KNOWN_GOOD_SENDERS` (Gmail can't run LLM judgment server-side) — grow ONLY when ben asks, never auto-populated.
- **Investigation is cheap-probes only.** Never call/SMS contractors, never hit external APIs that cost money or rate-limit aggressively.
- **Probe budget caps at 200 per cycle.** Future cycles pick up the rest.
- **Phase B verification overrides Phase E age-based staleness.**
- **Auto-apply low + medium risk; pause only for high risk.** Cron (no TTY) → `skip-high` after 30s.
- **Never degrade content.** Merges and trims preserve all unique information.
- **Never fabricate facts** — flag for human verify when probes can't confirm.
- **Email is a report, not a request.** Cycle proceeds regardless of send failure.
- **Backfill-log reaping is in A.7** (with checkpoint). Phase F handles non-log reaping only.
- **Idempotency keys live in the checkpoint.** Never delete a backfill-log without writing the checkpoint that absorbs its keys.
- **SSH failures are non-fatal.** Log unreachable, continue, surface in email.
- **Shared-MAC ARP entries = UNREACHABLE**, not VERIFIED.
- **Cron mode (no TTY)** detected via `[ -t 0 ] || echo cron`. Auto-default high-risk to skip.
- **Day-of-week scheduling**: `IS_SUNDAY=$(date +%u)` (returns 7 on Sundays). Skip `[WEEKLY]` phases unless `IS_SUNDAY=7` or `--full`. `--daily-only` forces daily-only regardless.
