# memo-watch — the memo v2 post-cutover watch, as host cron

Six checks against the live store every 30 minutes. **Silent when clean.** DMs the `memo`
seat only when something is wrong, or when a check could not read its input.

| file | runs on | cadence | what it is |
|---|---|---|---|
| `run` | server4 | `11,41 * * * *` | the six checks + the verdict + the DM |
| `classify.py` | server4 | — | access-log classifier `run` calls (status codes, per-IP 4xx recovery, benign classes) |
| `heartbeat-check` | **server3** | `*/20 * * * *` | the missed-run alarm — a different host's crond reading `run`'s heartbeat |

Both cron lines are wrapped in `/mnt/nas/data/code/scripts/cronguard` and their exit codes
are declared in `cronguard-digest.conf`.

## Authorisation

Ben, Slack #agents 2026-09-30 22:14 EDT, verbatim **"yes definitely"**, answering `agents`'
ask to move this watch off its 30-minute ATC broker prompt-fire — which cost the seat ~48 LLM
turns a day to read a result that was green 47 times out of 48 — onto host cron that messages
the seat only on a finding, with a missed-run alarm so it cannot go blind silently. Confirmed
by reading #agents (`C0B83Q85FPW`) at source rather than trusting the relay.

## Exit contract

| rc | meaning | effect |
|---|---|---|
| 0 | ran, verified quiet | silent. Nothing sent, nothing logged as a miss |
| 1 | ran, **has a finding** | DMs `memo` with the verdict lines and the full run. `rc1 findings` |
| 2 | ran, **could not read** an input | DMs `memo`. `rc2 unknown` — NOT clean, NOT failed |

⛔ **A skipped check is never a pass.** That is the entire reason rc2 is distinct from rc0: a
check that cannot read its input fails in the reassuring direction, and this loop exists to
catch exactly that.

⛔ `agents` specified `0 = finding / 3 = quiet / 2 = unreadable`. Not used here, and the reason
is local: `cronguard` logs every non-zero exit as `DID-NOT-COMPLETE`, and `cronguard-digest`
treats an undeclared non-zero as a failure — so `3`-on-quiet writes 48 fake failures a day, and
`0`-on-finding makes a real finding invisible to the digest. The fleet's own convention is
`0 = clean, non-zero carries the verdict`, declared per job in the conf. (Their numbering does
work when the cron line translates it, which is what their own `[ "$rc" = 3 ] && exit 0`
wrapper does — this just skips the translation.) ⛔ Do not "fix" this by exiting 0 on a finding.

### What the misses log CANNOT tell you (measured 2026-10-06)

The consequence above is documented; the **limit** of it was not. Because cronguard records both as
`rc=1`, `/tmp/cronguard-misses.log` cannot distinguish a normal findings run from this script
**genuinely crashing**. The blind spot is a missed failure, not a false alarm.

⚠️ It already cost a peer real time: on 2026-10-06 the `cluster` seat reported "memo-watch has failed
rc=1 on every run today and 69 times in the log" — measured correctly from that file and reasoned
correctly from cronguard's own legend, which simply does not apply to this script. Breakdown of those
69 entries: **34 DEFERRED + 26 DEFER-CEILING-then-RAN** (cronguard's IO gate, by design) **+ 8 rc=1
+ 1 rc=2** — nine non-deferral entries, every one a designed exit.

**The discriminator** is the DM, which a findings run always sends and always reports:

| misses log | `/home/ben/.memo-watch/cron.log` (server4, HOST-LOCAL) | means |
|---|---|---|
| `rc=1` | a verdict line **and** a DM line (`✅ DM accepted`) | normal: found something, delivered it |
| `rc=1` | no verdict line, or no DM line | **real failure** — died before finishing |
| `rc=1` | `⛔ DM ACCEPTED BUT NOT ROUTED (sent:0)` | finding stands and reached NOBODY |
| `rc=2` | `UNKNOWN:` line(s) | unreadable input; not a pass |
| `rc=99` / `rc=124` | — | cronguard: lock held / timeout, not this script |

⛔⛔ **CORRECTED the same day — THE RAW LOG IS NOT THE WHOLE MECHANISM.** `cronguard-digest` plus
`cronguard-digest.conf` is the reading layer and it already carries this job's contract:
`cronguard-digest.conf:60-61` declare `memo-watch rc1 findings` / `rc2 unknown` (and `:65-66` the
heartbeat half), the digest's header states `"NON-ZERO MEANS FAILED" IS FALSE FOR ANY JOB WHOSE EXIT
CODE CARRIES A VERDICT`, and a live run lists memo-watch under **FINDINGS**, not failures. A real crash
exits an UNDECLARED code and is classed separately. ⇒ The table above is how to read the RAW log by
hand; it is not a workaround for a missing capability, and nothing here is waiting on a feature.

⚠️ Date, measured by `git blame`: the mechanism shipped 2026-08-22 (`0e18c53`) but the **memo-watch
declaration was added 2026-10-06 08:21 EDT** (`7a26871`). The 8 historical rc=1 entries read correctly
only because the digest re-reads the log against the current conf.

⛔ Do not change `cronguard` to accommodate this — it is shared fleet tooling at
`/mnt/nas/data/code/scripts/cronguard`, nonzero-means-failure is correct for every other job, and the
per-job contract belongs in the digest's conf, where the owner can state it and the wrapper cannot
guess it.

## Where the knowledge lives

⛔ **The SPEC is the ATC broker schedule `7f9571bd-c2f1-43c9-8e9b-7a7c1a421d86` (`memo/memo-watch`),
PAUSED and NOT DELETED.** Every check in `run` exists because something failed in a specific way
on a specific date, and the schedule body is the only written record of which. **Rebuild the impl
from the SPEC, never from memory** — the 2026-08-25 rebuild-from-memory introduced four bugs that
all alarmed on healthy input.

⚠️ This file lived in `/tmp/memo-watch/` until 2026-09-30 and was lost to every reboot (rebuilt
2026-09-22 after one). A cron job cannot have its implementation in `/tmp`.

## The missed-run alarm, and why it is two halves

- `run` step 0 compares **its own heartbeat age** against the 30-minute cadence, so a gap that
  has **ENDED** is named on the next run that happens. This replaces the old broker `fire_count`
  delta, which answered the same question (did a window go unwatched) through the counter the
  prompt-fire incremented.
- `heartbeat-check` on **server3** sees a gap that is **STILL OPEN**, because a job that is not
  running cannot report that it is not running. On 2026-09-08 the broker fired 798–810 and not
  one reached the seat: 6.5 hours of no monitoring produced output byte-identical to 13 clean
  windows, while memo itself was perfectly healthy. ⛔ Both halves are required; neither
  substitutes for the other.

⚠️ **Residual, stated rather than hidden:** `heartbeat-check` shares the NFS mount and the ATC
broker with the thing it watches, so NAS-down and broker-down make it alarm (right direction),
but **server3 down means nothing watches the watch and nothing says so.**

## State

| path | host | what |
|---|---|---|
| `/home/ben/.memo-watch/last-run`, `last-doc-count` | server4, host-local | the deltas. ⛔ There is **no absolute document floor** — the only count criterion is the previous reading |
| `/home/ben/.memo-watch/run.out`, `cron.log` | server4, host-local | last run, and the append-only history |
| `/mnt/nas/data/memo-watch/heartbeat` | **NFS, shared** | written LAST by `run`, so a killed run leaves a stale heartbeat rather than a fresh lie |

## A shrink names its own actor (added 2026-10-05)

A document-count shrink used to emit the finding *"Name the client IP and check whether it was
authorised"*, which cost an LLM turn every time. On 2026-10-05 it cost two, for one authorised
purge — a step with exactly one correct output given its input, which makes it a script.

`run` now reads `deletion_log` itself over the last 60 minutes and branches on what it finds:

| what the log says | outcome | why |
|---|---|---|
| any row with `actor=unknown` **or** `reason=unspecified` | **FINDING** | that is the MCP delete path, which records neither; a human has to name the client IP from the access log |
| rows ≥ the shortfall, none anonymous | **not a finding** — prints the actors and their reasons | the deletions attribute themselves; nothing is left to look up |
| rows < the shortfall | **FINDING**, naming how much is unaccounted | ⛔ a longer window is an assumption, not an explanation |
| `deletion_log` unreadable (empty output) | **UNKNOWN** (rc2), never a pass | a check that cannot read its input is not a check |

⛔ **`docker exec` writes 0 bytes to a REDIRECT — the `| cat` on that query is load-bearing.**
Omitting it is not a crash: the summary comes back empty, the id set is empty, and the comparison
passes vacuously. That exact mistake produced a false "0 outside-scope citers" while auditing the
2026-10-05 purge (memo `8b6bc8d5`, instance 17); the denominator printed beside the verdict is what
caught it, which is why this check prints the row count next to its conclusion.

⚠️ **Attribution is not authorisation.** A clean pass here means the deletions say who did them and
why — the reason is the actor's own words. A reason citing a Slack channel still has to be read
there if anything looks off; see memo `0c20c7dd` for the scope check a tag-scoped purge needs.

## Two instrumentation fixes from the 2026-10-06 embedder outage

Both came out of one incident (barn GPU nodes rebooted, `:31541`/`:31536` down 13:02–13:45Z) and
both are about the watch measuring itself rather than the service.

**1. Check 1b now marks its own probe `?wp=1`.**
`classify.py:104` already skips any path containing `wp=1`, for *every* classifier — the marker had
simply never been put on 1b's `POST /search`. Consequence, measured at fire 13:11: both 5xx in the
window were probes (one mine, one 1b's) and the verdict read `2 5xx in the window`, which reads as
caller impact when no caller had failed yet.

⚠️ **The distortion is bounded, and the bound matters.** At fire 13:41 there were 26 5xx and only
one was 1b's — the rest were real seat traffic (`POST /documents` ×8, `/search-documents`,
`/search-passages`, `/recall`, `GET /documents?query=`). So this only skews a window where real
traffic is ~zero, i.e. the first minutes of an outage. ⛔ Do not read the 5xx finding as generally
self-inflicted; at volume it was 1-in-26 and it independently corroborated the `ha` seat's report.

⚠️ **All 26 came from `192.168.0.109`, which is NOT evidence of self-infliction** — that address *is*
server4 (memo `b8c1ed64`), and server4 hosts many seats. An IP is a host, not a seat (`647773da`).

**2. The `--tail` ceiling went 4000 → 12000.**
At fire 13:41 the parser declared `PARTIAL COVERAGE: this read only goes back 29.9 min` against its
32-minute window — elevated traffic had pushed older lines out. Measured the same day at normal
traffic, 4000 spans 36.8 min: it was *always* marginal and only failed under the load an incident
produces. 8000/12000 span many hours and are still accepted as current.

⭐ Larger candidates are safe by construction: the probe walks DOWNWARD and the age check rejects any
tail whose newest line is >300 s old, which is exactly how a read that crossed into the rotated file
is detected. ⚠️ Keep the PARTIAL COVERAGE declaration either way — it is the net that caught this.

### How both branches were verified
A fixture, because during the outage every `/search` 500s and traffic can only be generated from the
watch's own host, so the branch that matters could not be tested live:

| assertion | result |
|---|---|
| fixture parses 2 events, not 0 (a 0 would pass both branches vacuously) | PASS |
| a `wp=1` 500 is EXCLUDED from the 5xx list | PASS |
| a real 500 from another IP STILL fires | PASS |
| `POST /search?wp=1` returns 200 with hits (marker does not break the probe) | PASS |
| full control run: `--tail 12000`, `coverage OK ... 2204.8 min`, 1b `code=200 hits=2` | PASS |

### Still open, NOT fixed here
`GET /ready` reports on the store and never touches the embedding provider, so it returned
`status: ready, vector_index_readable: true, passage_index_gap: 0` throughout an outage in which
every embedding-dependent path 500'd. ⇒ Nothing monitoring `/ready` can fire on this, and ~50 seats
plus any external monitor read it. The `ha` seat reached the same conclusion independently. Fixing it
means a provider probe on `/ready` (or a sibling endpoint), which is a change to `:8000` — live infra
needing a rebuild carrying `MEMO_V2_PORT=8000` + `OPENROUTER_API_KEY`.
⛔ The old post-rebuild step *"and `:8091` verified closed after"* is **INVALID** and is removed:
`:8091` closed proves nothing about this rebuild. What to do instead: **tag the live image and
save `docker logs -t` BEFORE the rebuild** (memo `358dc28e`), and afterwards read the healthcheck
**probe count**, not the status — the interval is 300s, so `health: starting` for five minutes is
normal and is not evidence of anything. **Queued for the operator, deliberately not done mid-incident.** Detail in memo `279e5509`.

## Verified before shipping, both directions

Positive controls run 2026-09-30, because a detector never run against a known positive is not
a detector: heartbeat **fresh** → rc0 silent · **stale** (forced) → rc1 + DM delivered ·
**unreadable path** → rc2 + DM delivered · `run` with a 3-hour-old heartbeat → "MISSED ~5
WINDOW(S)" finding + DM carrying the full run + rc1, and rc0 silent on the next run.
