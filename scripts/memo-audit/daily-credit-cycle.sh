#!/usr/bin/env bash
# The daily cycle that spends the grant on keeping memo true.
#
# Ben, Slack #memo 2026-10-22: "I basically want you to use all these credits.
# No limit usage no whatever just use them up to the amount that we have so that
# we maximize them" -- and "we're going to keep getting these credits each
# month". A grant expires unused, so UNDERSPEND IS WASTE, and the only way that
# holds without someone remembering to start it is a cron.
#
# Order is deliberate and each phase feeds the next:
#   1. slack digests    free     what Ben said since yesterday
#   2. slack mining     $        his rulings become memos
#   3. refute recheck   free     probes find memos the world moved under
#   4. candidate rebuild free    refuted first, then by read traffic
#   5. truth audit      $$$      whatever grant is left, until it is gone
#
# ⛔ PHASES 2 AND 5 EACH TAKE THE SPENDER LOCK, so they cannot overlap -- two
# spenders are blind to each other's in-flight cost. They run SEQUENTIALLY here
# for that reason, not for tidiness.
#
# ⚠️ A PHASE THAT FAILS DOES NOT STOP THE CYCLE. A Slack archive that did not
# run must not cost the corpus its audit. Each phase reports its own rc and the
# script carries on; the summary at the end names every failure.
# ⛔ CONCURRENCY 2, AND 03:30 IS INSIDE THE PERMITTED WINDOW ON PURPOSE.
# `system`, relaying Ben from #system 2026-10-22 14:55: memo's batch work runs
# on server4 only at concurrency 2, idle priority, between 22:00 and 06:00.
# server4 is a single point of failure and its ROOT disk (sde) holds every
# session transcript, so a worker's own logging is the I/O that hurts -- this
# seat drove the host to load 170 at ~195 MB/s during a RAID rebuild.
# ⚠ Do not raise either number without `system` agreeing. Workers are wrapped
# in ionice/nice at the exec by run.py; the cap here is the second half of it.
set -u
cd "$(dirname "$0")" || exit 1
# \u26d4 ABSOLUTE, AND EXPORTED, because the python heredocs below used sys.path.insert(0, ".")
#    and "." is the PROCESS cwd, not this file's directory. It happened to be right only
#    because of the cd above. See the wrong-module guard each heredoc now carries.
export MEMO_AUDIT_DIR="$PWD"
SLACK=../slack-archive
INSTR=../instruction-audit
LOG_PREFIX="$(date -Is)"
rcs=()

# ⛔⛔ THE WINDOW HAD A START AND NO END, AND NOTHING ENFORCED EITHER.
# `system` relaying Ben, #system 2026-10-22 14:55: memo's batch work runs on server4
# only, concurrency 2, idle priority, BETWEEN 22:00 AND 06:00. The comment above records
# 03:30 as a deliberate start inside that window. Nothing recorded an end, and the phase
# timeouts below sum to 19 HOURS -- from 03:30 that is 22:30 the next day, capped only by
# the cron's own 12h outer timeout at 15:30. Either way the expensive phases would be
# running at 10:00 against interactive seats, which is the thing the window exists to stop.
# ⇒ Every phase is now clamped to the next 06:00, and a phase that cannot get a usable
#   slice is SKIPPED AND NAMED rather than started and killed mid-worker.
WINDOW_END_H=6
_now=$(date +%s)
DEADLINE=$(date -d "today ${WINDOW_END_H}:00" +%s)
[ "$DEADLINE" -le "$_now" ] && DEADLINE=$(date -d "tomorrow ${WINDOW_END_H}:00" +%s)
echo "batch window closes $(date -d "@$DEADLINE" -Is) -- $(( (DEADLINE - _now) / 60 )) min from now"

# ⚠️ A FLOOR, NOT A TRIM. A `claude -p` worker killed at the deadline has already been
# billed for the tokens it read; starting one with four minutes left buys nothing and
# spends real money. 600s is the smallest slice worth starting.
PHASE_FLOOR_S=600

# ⛔⛔ A CYCLE THAT RAN AND SPENT NOTHING EXITS 0 AND LOOKS EXACTLY LIKE A GOOD NIGHT.
#    That is the failure that costs money here: a grant expires on a date, so the loss is
#    silent, gradual and never produces an error. The cronguard wrapper only writes a line
#    on a NON-ZERO exit, and nobody reads that file. ⇒ Snapshot the budget now, diff it at
#    the end, and DM the seat when the night fell short of what the soonest expiry needs.
BUDGET_BEFORE=/tmp/memo-credit-cycle.before.json
python3 - > "$BUDGET_BEFORE" <<'PYSNAP' || echo "⚠ budget snapshot FAILED -- the end-of-run shortfall check will say so, not assume zero" >&2
import sys, json, os; sys.path.insert(0, os.environ["MEMO_AUDIT_DIR"])
import keyring
if not hasattr(keyring, "load_grants"):
    # \u26d4 rc 3, NOT a bare sys.exit(<str>) which returns 1 -- 1 is SHORTFALL, and an
    #    import fault announcing itself as a shortfall is a wrong diagnosis wearing a
    #    real one's clothes.
    print("\u26d4 IMPORTED THE WRONG keyring: %s\n"
             "   The PyPI credential library of the same name shadows ours whenever our\n"
             "   directory is not first on sys.path, and the import SUCCEEDS -- the fault\n"
             "   only surfaces later as AttributeError, which this script used to report as\n"
             "   'the spend ledger could not be read'. That diagnosis sends someone to the\n"
             "   broker for an import fault. Ours is scripts/memo-audit/keyring.py."
             % getattr(keyring, "__file__", "?"), file=sys.stderr)
    sys.exit(3)
print(json.dumps({a: keyring.remaining(a) for a in keyring.load_grants()}))
PYSNAP

# ⛔⛔ THE AUDIT GETS A RESERVED SLICE, OR THE CLAMP STARVES THE ONE PHASE THAT MATTERS.
#    Phase order puts the cheap, additive work first and the expensive audit last, and the
#    earlier timeouts sum to over 9 hours on their own -- probe-coverage alone is 4. Clamping
#    everything to 06:00 without a reserve means a slow night spends the whole window on
#    coverage and SKIPS the audit, which is the phase that drains the grant about to expire.
#    ⇒ A phase may declare PHASE_RESERVE=<seconds>: it is clamped to leave that much behind.
#    ⚠️ A reserve is not a guarantee the audit will fill it -- it only stops an earlier phase
#      taking it. If the audit still skips, the window is genuinely too short, which is a
#      different finding and the end-of-run accounting will say so.
AUDIT_RESERVE_S=$(( 5 * 3600 ))

phase () {   # phase <name> <command...>   (PHASE_RESERVE=<s> to hold time back)
  local name="$1"; shift
  local left=$(( DEADLINE - $(date +%s) ))
  local reserve="${PHASE_RESERVE:-0}"
  if [ "$reserve" -gt 0 ]; then
    local usable=$(( left - reserve ))
    if [ "$usable" -lt "$PHASE_FLOOR_S" ]; then
      echo "════ ${name} SKIPPED $(date -Is): ${left}s left, ${reserve}s reserved for the audit"
      rcs+=("${name}=SKIPPED-reserve"); return 0
    fi
    left="$usable"
  fi
  if [ "$left" -lt "$PHASE_FLOOR_S" ]; then
    echo "════ ${name} SKIPPED $(date -Is): ${left}s left in the 22:00-${WINDOW_END_H}:00 window, floor ${PHASE_FLOOR_S}s"
    rcs+=("${name}=SKIPPED-window")
    return 0
  fi
  # ⛔ Clamp this phase's own timeout to the deadline. The arg list is
  #    `/usr/bin/timeout <secs> <cmd...>`, so rewrite element 2 when it is that shape.
  local clamped=0
  if [ "${1:-}" = "/usr/bin/timeout" ] && [ "${2:-}" -eq "${2:-}" ] 2>/dev/null; then
    local want="$2"
    if [ "$want" -gt "$left" ]; then
      echo "──── ${name}: timeout ${want}s clamped to ${left}s (window)"
      set -- "$1" "$left" "${@:3}"
      clamped=1
    fi
  fi
  echo "════ ${name} $(date -Is)"
  "$@"; local rc=$?
  echo "──── ${name} rc=${rc}"
  # ⭐⭐ A CLAMPED PHASE THAT EXITS 124 DID EXACTLY WHAT IT WAS TOLD.
  #    The clamp SETS the timeout to the window edge, so hitting it is the
  #    design, not a fault -- the audit is meant to spend until 06:00 and stop.
  #    Recording that as a failure made the cycle print "⛔ phases above did not
  #    return 0" on every single successful night. An alarm that fires on
  #    correct behaviour is worse than no alarm: it trains the reader to skip
  #    the one line that would carry a real failure.
  # ⛔ Only a CLAMPED phase gets this. An unclamped 124 is a genuine timeout
  #    against its own budget and must still read as a failure.
  if [ "$rc" -eq 124 ] && [ "$clamped" -eq 1 ]; then
    echo "──── ${name}: 124 after a window clamp = ran to the 06:00 edge as designed, not a fault"
    rcs+=("${name}=0-window-edge")
  else
    rcs+=("${name}=${rc}")
  fi
  return 0
}

# ⛔ AND REFUSE TO SPEND AT ALL OUTSIDE THE WINDOW, whatever started this script. A cron
#    time is not an enforcement: a hand-run at 14:00 violates the same constraint, and the
#    constraint is Ben's, not this file's. Override only with his authorization, per run.
_h=$(date +%-H)
if [ "$_h" -lt "$WINDOW_END_H" ] || [ "$_h" -ge 22 ]; then :; else
  if [ -z "${MEMO_BATCH_OUTSIDE_WINDOW:-}" ]; then
    echo "⛔ REFUSING: it is $(date +%H:%M) and memo's batch work is authorized 22:00-0${WINDOW_END_H}:00 only"
    echo "   (system relaying Ben, #system 2026-10-22 14:55). The free phases are not run"
    echo "   either, because the expensive ones are what this cycle exists for and a"
    echo "   half-cycle reads as a completed one."
    echo "   Deliberate out-of-window run: MEMO_BATCH_OUTSIDE_WINDOW=<why> $0"
    exit 3
  fi
  echo "⚠️ RUNNING OUTSIDE THE 22:00-0${WINDOW_END_H}:00 WINDOW by explicit override: ${MEMO_BATCH_OUTSIDE_WINDOW}"
fi

phase slack-archive  /usr/bin/timeout 1800 python3 "$SLACK/archive.py" --days 2
phase slack-digests  /usr/bin/timeout  900 python3 "$SLACK/mine.py" --days 1.2 --checkpoint

# ⭐ INSTRUCTION SURFACE -- the files and pins that are LOADED into sessions.
#    Ben, #memo 2026-10-23: "maintain any files that anyone is using with claims
#    that a. should reference memo which we can better control, b. need to be
#    reverified and maintained."
#    These run EARLY and cost no credits: they are pure script, no LLM, per the
#    standing token embargo. Placed before the paid phases so the window clamp
#    can never squeeze them out -- a cheap check that only runs on quiet nights
#    is a check that is absent exactly when the fleet is busiest.
# ⛔ scan.py defaults touch no NFS. Do NOT add --paths while /mnt/nas is degraded:
#    a GETATTR sweep across 194 files on a mount doing 60s writes is an outage.
# route.py drives FOUR detectors across THREE push surfaces, then DMs each owning
# seat ONLY its NEW defects and sends memo the full digest. ⛔ Printing alone is
# not maintenance: in cron nobody reads stdout, and the phase still shows green.
#   sweep.py             -- memo ids in instruction files that no longer resolve
#   sweep.py --unanchored-- dated quantitative claims carrying NO memo id (drips
#                           3 seats/night, oldest claim first; 175 found 10-23)
#   pins.py              -- ATC standing pins + the session-start budget cliff
#   statuses.py          -- the ATC status board (NEW 2026-10-23, Ben #memo 09:06)
# ⚠️ TIMEOUT RAISED 1800 -> 3000 THE SAME DAY, and the reason matters more than the
#    number: 1800 was sized when route.py ran scan.py and pins.py on ONE host. It
#    now runs sweep.py TWICE over four hosts each, plus statuses.py. A timeout that
#    was generous for half the work is a silent truncation for all of it -- the
#    phase records rc=124, but the findings it never reached are indistinguishable
#    from findings that do not exist.
# ⛔ SHARED LOCK WITH THE MORNING SWEEP (crontab 07:10, added 2026-10-23 on
#    Ben's "multiple sweeps" directive, #memo 10:06). Both runs send DMs and
#    both write ~/.memo-instruction-audit/*.json; two at once would race the
#    seen-ledger and double-send a first-contact notice. They cannot overlap
#    on the clock today, but a cycle that runs long into the morning would.
phase instr-audit /usr/bin/flock /tmp/instr-audit-route.lock /usr/bin/timeout 3000 python3 "$INSTR/route.py"
PHASE_RESERVE="$AUDIT_RESERVE_S" \
phase slack-mine     /usr/bin/timeout 10800 python3 "$SLACK/mine-run.py" --per-worker 4 --concurrency 2 --wait-for-lock 7200
# ⛔⛔ GATE THE RECHECK ON THE RUNNER'S OWN CONTROLS, BECAUSE A BROKEN RUNNER
#    DOES NOT FAIL -- IT PUBLISHES. refute-recheck writes refute-queue.json,
#    pins.py reads it, and route.py DMs the holder "a memo your pin cites has
#    been DISPROVED, so the pin is handing you a known-wrong fact every
#    session". A wrong verdict therefore leaves this host and asks a seat to
#    edit a correct pin. MEASURED 2026-10-25: `_run_grep` matched without re.M,
#    11 of 48 refutations were false, and three of them reached `atc`, `nginx`
#    and `servers` as exactly that DM. Two disputed it; one had not yet.
# ⭐ refute.py HAS CARRIED 19 CONTROLS THE WHOLE TIME AND THE CYCLE NEVER RAN
#    THEM. A control nothing invokes is indistinguishable from one that was
#    never written -- the same finding as snapshot-targets.py (2a838c2), one
#    phase apart in this very file.
phase refute-selftest /usr/bin/timeout 120 python3 refute.py --self-test
if [[ " ${rcs[*]} " == *" refute-selftest=0 "* ]]; then
  phase refute-recheck /usr/bin/timeout 1800 python3 refute-recheck.py --queue
else
  echo "⛔ refute-recheck SKIPPED: the runner's own controls failed. The previous"
  echo "   queue stands; a stale queue repeats yesterday's verdicts, a queue"
  echo "   written by a broken runner mails new false ones to pin holders."
  rcs+=("refute-recheck=SKIPPED-selftest")
fi
phase candidates-all /usr/bin/timeout 1800 python3 select-all.py --out candidates-all.json
phase candidates     /usr/bin/timeout  900 python3 select-read-weighted.py \
                         --candidates candidates-all.json --out candidates.json --top 0

# ── probe coverage, before the deep audit ────────────────────────────────────
# ⭐ CHEAP AND STRICTLY ADDITIVE. Measured 2026-10-22: a probe-only pass is
# $0.034/memo against $0.41 for a full audit. Every memo that gains a probe is
# one the FREE nightly recheck can watch from then on, so this phase buys
# permanent coverage where the audit buys one memo's correctness.
# ⚠️ Bounded on purpose. It runs ahead of the audit and takes a fixed slice
# (~$20, ~600 memos), not the whole grant -- the split between coverage and
# depth is Ben's call, not a default this script should quietly make.
PHASE_RESERVE="$AUDIT_RESERVE_S" \
# ── TONIGHT'S PACED BUDGET, split across the two spending phases ───────
# ⭐ AUTHORIZED BY BEN, Slack #memo 2026-10-23 09:10 ("yes" to hoisted ask a14):
#    pace each grant to finish at ITS OWN expiry instead of bursting.
# ⛔⛔ THE PROBE PHASE USED TO BE A HARDCODED `--batches 60` THAT CONSULTED NO
#    BUDGET AT ALL. Measured 10-22: it spent $17.68 unconditionally. Under pacing
#    the whole night is ~$11 today, so the hardcoded phase would have spent 160%
#    of the night's budget BEFORE the audit phase computed its share -- and the
#    audit phase, reading `remaining()`, would have seen the money already gone
#    and quietly run tiny. A cap on one of two spenders is not a cap.
# ⚠️ THE SPLIT IS A JUDGEMENT, NOT A MEASUREMENT, and it is the knob to turn.
#    Probes cost $0.072/memo and buy a FALSIFIER; audits cost $0.433/memo and buy
#    a CORRECTION. Ben's 10-23 09:06 direction is about "correcting issues and
#    preventing pitfalls", and under scarcity a correction lands today while a
#    falsifier pays off whenever that memo is next read -- so audit gets the
#    larger share. Full probe coverage of the remaining 11,409 memos is ~$821
#    against an $800/month pool: it is a multi-month program either way, and
#    starving it by a few dollars a night changes its finish date very little.
export PROBE_SHARE=30                  # percent of the night to the probe lane
# ⛔ EXPORTED, not a plain shell var: the audit phase reads it via os.environ in a
#    python subprocess, and an unexported var there silently defaults to 30 -- the
#    two phases would disagree about the split the moment anyone tuned it.
NIGHT=$(python3 - <<'PY'
import sys, os; sys.path.insert(0, os.environ["MEMO_AUDIT_DIR"])
import keyring
if not hasattr(keyring, "nightly_budget"):
    print("\u26d4 IMPORTED THE WRONG keyring (no nightly_budget): %s"
          % getattr(keyring, "__file__", "?"), file=sys.stderr)
    sys.exit(3)
try:
    print("%.4f" % keyring.nightly_budget())
except keyring.LedgerUnreadable as e:
    print(f"spend ledger unreadable: {e}", file=sys.stderr); sys.exit(9)
except Exception as e:
    print(f"paced budget not computable: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(9)
PY
) || NIGHT=""
if [ -z "$NIGHT" ]; then
  # ⛔ Same rule as the audit phase below: an unreadable ledger is NOT a zero
  #    budget. Skip loudly rather than run a token batch that reads as a success.
  echo "⛔ PACED BUDGET UNKNOWN — probe and audit phases BOTH SKIPPED."
  echo "   Check: curl -sS http://192.168.1.168:3030/api-spend/totals | head -c 200"
  echo "FAILED probe-coverage (budget unknown)"
  echo "FAILED audit (budget unknown)"
  NIGHT=0
else
  echo "paced budget tonight: \$${NIGHT} (each grant finishes at its own expiry)"
  PROBE_BATCHES=$(python3 -c "print(max(1,min(400,int($NIGHT*$PROBE_SHARE/100/0.295))))")
  echo "  probe lane: ${PROBE_SHARE}% -> ~${PROBE_BATCHES} batches at \$0.295/batch measured"
  phase probe-coverage /usr/bin/timeout 14400 python3 run.py \
      --candidates candidates-all.json --prompt probe-prompt.md --model sonnet \
      --batch-size 10 --batches "$PROBE_BATCHES" --concurrency 2 --wait-for-lock 7200 --skip-probed
fi

# How many batches the remaining grant actually pays for. ⚠️ Derived, never a
# constant: the cost per memo is ordering-dependent (measured $0.291 stratified
# vs $0.697 read-weighted) so a hardcoded batch count either strands credit or
# fills the log with refusals.
# ⛔⛔ AN UNREADABLE LEDGER IS NOT A ZERO BUDGET, AND THE OLD `except Exception:
#    budget = 0.0` TURNED IT INTO ONE. That line was near-dead while `remaining()`
#    read a local file and could not fail; since the reads moved to the broker
#    (2026-10-22) it is live, and it fails in the WASTEFUL direction: a zero budget
#    floors to ONE batch, the cycle logs a normal-looking small night, and the grant
#    expires with the money unspent. Ben, #memo 2026-10-22: "I basically want you to
#    use all these credits." Underspend is the loss here, not overspend.
# ⇒ An unknown budget SKIPS the audit phase loudly rather than running a token
#   batch that reads as success. Earlier phases have already run and are unaffected.
BATCHES=$(python3 - <<'PY'
import sys, os; sys.path.insert(0, os.environ["MEMO_AUDIT_DIR"])
import keyring
if not hasattr(keyring, "load_grants"):
    # \u26d4 rc 3, NOT a bare sys.exit(<str>) which returns 1 -- 1 is SHORTFALL, and an
    #    import fault announcing itself as a shortfall is a wrong diagnosis wearing a
    #    real one's clothes.
    print("\u26d4 IMPORTED THE WRONG keyring: %s\n"
             "   The PyPI credential library of the same name shadows ours whenever our\n"
             "   directory is not first on sys.path, and the import SUCCEEDS -- the fault\n"
             "   only surfaces later as AttributeError, which this script used to report as\n"
             "   'the spend ledger could not be read'. That diagnosis sends someone to the\n"
             "   broker for an import fault. Ours is scripts/memo-audit/keyring.py."
             % getattr(keyring, "__file__", "?"), file=sys.stderr)
    sys.exit(3)
try:
    # ⛔ WAS `sum(remaining(a) for a in load_grants())` -- the WHOLE reachable
    #    pool. That is the burst that spent $171.04 on 10-22 and left 1.7 nights
    #    of credit. Ben authorized pacing 10-23 09:10 (ask a14).
    budget = keyring.nightly_budget() * (100 - int(os.environ.get("PROBE_SHARE", "30"))) / 100
except keyring.LedgerUnreadable as e:
    print(f"spend ledger unreadable: {e}", file=sys.stderr); sys.exit(9)
except Exception as e:
    print(f"budget not computable: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(9)
per_batch = 2.60                      # measured mean, 2026-10-22 runs
print(max(1, min(400, int(budget / per_batch))))
PY
) || BATCHES=""
if [ -z "$BATCHES" ]; then
  echo "⛔ AUDIT PHASE SKIPPED: the spend ledger could not be read, so the budget is UNKNOWN."
  echo "   Not run as one batch -- that would read as a quiet night while the grant expires."
  echo "   Check: curl -sS http://192.168.1.168:3030/api-spend/totals | head -c 200"
  echo "FAILED audit (budget unknown)"
else
  echo "budget buys ~${BATCHES} batches"
  # ── WITNESS. Freeze the targets BEFORE the run or the night is unverifiable ──
  # \u26d4\u26d4 THIS WAS MISSING FROM 2026-10-22 UNTIL 2026-10-25 AND FOUR AUDIT RUNS
  #    WENT UNVERIFIED -- 10-23 x2 and 10-24 x2, roughly $40 of grant whose effect
  #    on the corpus nobody can diff, because `verify-run.py` needs a snapshot taken
  #    first and one taken afterwards shows every target untouched. The standing
  #    rule is DIFF THE CORPUS, NEVER THE WORKER'S REPORT (memos 69f9b27f, e6482116),
  #    `snapshot-targets.py` was written and committed for exactly this, and the
  #    pipeline that needed it simply never called it. A tool that exists and is
  #    not wired in is indistinguishable, in the log, from one that was never built.
  # \u26a0\ufe0f run.py takes cands[offset : offset + batch_size*batches] -- the HEAD of the
  #    same file with no --skip-probed here -- so head-N is exactly the target set.
  #    If that invocation ever gains a filter, this snapshot stops matching it.
  PRESNAP="runs/pre-snapshot-$(date +%Y%m%dT%H%M%S).json"
  phase audit-snapshot /usr/bin/timeout 600 python3 snapshot-targets.py \
      --candidates candidates.json --n "$(( BATCHES * 6 ))" --out "$PRESNAP"
  RUNDIR_BEFORE=$(ls -dt runs/*/ 2>/dev/null | head -1)

  phase audit /usr/bin/timeout 36000 python3 run.py --candidates candidates.json \
      --batch-size 6 --batches "$BATCHES" --concurrency 2 --wait-for-lock 7200

  # \u26a0\ufe0f ONLY IF THE AUDIT ACTUALLY RAN. A skipped or locked-out audit leaves no new
  #    run dir, and verifying against the previous night's would report every target
  #    untouched -- a clean-looking diff that means "no audit happened", which is the
  #    one message it must not be able to produce by accident.
  RUNDIR_AFTER=$(ls -dt runs/*/ 2>/dev/null | head -1)
  if [ -s "$PRESNAP" ] && [ -n "$RUNDIR_AFTER" ] && [ "$RUNDIR_AFTER" != "$RUNDIR_BEFORE" ]; then
    phase audit-verify /usr/bin/timeout 900 python3 verify-run.py \
        --snapshot "$PRESNAP" --rundir "${RUNDIR_AFTER%/}"
  else
    echo "\u26d4 audit-verify SKIPPED: no new run dir (audit did not run) or snapshot missing."
    echo "   \u26a0\ufe0f This night's audit, if any, is UNVERIFIED -- do not read its batch JSON as a corpus change."
    rcs+=("audit-verify=SKIPPED-no-run")
  fi
fi

# ── what the night actually bought ─────────────────────────────────────
# ⚠ Reports per ACCOUNT, never a pooled total. $200 expiring in 33 days and $125 expiring
#   in 4 are not interchangeable, and a pooled figure is the one number that hides the only
#   question worth asking: is the money that disappears FIRST being spent?
BROKER=http://192.168.1.168:3030
SHORTFALL_REPORT=/tmp/memo-credit-cycle.shortfall.txt
python3 - "$BUDGET_BEFORE" > "$SHORTFALL_REPORT" 2>&1 <<'PYACCT'
import sys, json, datetime, os
sys.path.insert(0, os.environ["MEMO_AUDIT_DIR"])
import keyring
if not hasattr(keyring, "load_grants"):
    # \u26d4 rc 3, NOT a bare sys.exit(<str>) which returns 1 -- 1 is SHORTFALL, and an
    #    import fault announcing itself as a shortfall is a wrong diagnosis wearing a
    #    real one's clothes.
    print("\u26d4 IMPORTED THE WRONG keyring: %s\n"
             "   The PyPI credential library of the same name shadows ours whenever our\n"
             "   directory is not first on sys.path, and the import SUCCEEDS -- the fault\n"
             "   only surfaces later as AttributeError, which this script used to report as\n"
             "   'the spend ledger could not be read'. That diagnosis sends someone to the\n"
             "   broker for an import fault. Ours is scripts/memo-audit/keyring.py."
             % getattr(keyring, "__file__", "?"), file=sys.stderr)
    sys.exit(3)
try:
    before = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"⛔ NO BASELINE: the start-of-run budget snapshot is unreadable ({type(e).__name__}: {e}).")
    print("   This run's spend CANNOT be computed. That is not the same as zero spend.")
    sys.exit(2)
g = keyring.load_grants()
today = datetime.date.today()
rows, worst = [], None
for a in g:
    try:
        now = keyring.remaining(a)
    except Exception as e:
        print(f"⛔ {a}: remaining() failed ({type(e).__name__}) -- not counted as zero spend."); continue
    spent = before.get(a)
    spent = None if spent is None else spent - now
    exp = g[a].get("expires")
    days = (datetime.date.fromisoformat(exp) - today).days if exp else None
    need = (now / max(1, days)) if (days is not None and days >= 0 and now > 0) else 0.0
    rows.append((a, now, exp, days, spent, need))
    if now > 1.0 and days is not None and (worst is None or days < worst[3]):
        worst = (a, now, exp, days, spent, need)
print("acct        remaining   expires      days   spent tonight   needed/night")
for a, now, exp, days, spent, need in sorted(rows, key=lambda r: (r[3] if r[3] is not None else 9999)):
    sp = "   unknown" if spent is None else f"{spent:10.2f}"
    print(f"{a:<10} {now:10.2f}   {exp or '-':<11} {days if days is not None else '-':>5}   {sp}      {need:10.2f}")
if worst is None:
    print("\nNo account holds a spendable balance with a known expiry.")
    sys.exit(0)
a, now, exp, days, spent, need = worst
print(f"\nSOONEST EXPIRY: {a}, ${now:.2f} left, {days} day(s) to {exp} -> ${need:.2f}/night to not waste it.")
if spent is None:
    print("⛔ Tonight's spend on it is UNKNOWN (no baseline). Treat as unverified, not as fine."); sys.exit(2)
if spent < need * 0.5:
    print(f"⛔ SHORTFALL: spent ${spent:.2f} tonight against ${need:.2f} needed. At this rate the grant expires with money on it.")
    sys.exit(1)
print(f"✅ On pace: spent ${spent:.2f} tonight against ${need:.2f} needed.")
PYACCT
ACCT_RC=$?
echo "──── spend accounting rc=${ACCT_RC}"
cat "$SHORTFALL_REPORT"
# DM only when there is something to act on (rc 1 shortfall, rc 2 unverifiable). rc 0 is silence.
if [ "$ACCT_RC" -ne 0 ]; then
  python3 - "$SHORTFALL_REPORT" "$ACCT_RC" > /tmp/memo-credit-cycle.dm.json <<'PYDM'
import sys, json
body = open(sys.argv[1]).read()
rc = sys.argv[2]
subj = {"1": "credit cycle: SHORTFALL against the soonest grant expiry",
        "2": "credit cycle: tonight's spend could not be verified",
       }.get(rc, f"credit cycle: the spend accounting itself FAILED (rc={rc})")
print(json.dumps({"to": "memo", "from": "memo", "subject": subj,
                  "content": body + "\n\nSource: daily-credit-cycle.sh, server4. "
                             "Per-account figures come from keyring.remaining() against the "
                             "broker ledger; a pooled total would hide this."}))
PYDM
  DM_CODE=$(curl -sS --max-time 15 -X POST "$BROKER/messages" -H 'Content-Type: application/json' \
                 --data-binary @/tmp/memo-credit-cycle.dm.json -o /tmp/memo-credit-cycle.dm-resp.json \
                 -w '%{http_code}' 2>/dev/null)
  # ⛔ DECIDE ON THE HTTP CODE, NEVER curl's EXIT STATUS: a 404 on a dead route exits 0.
  case "${DM_CODE:-}" in
    2*) if grep -q '"sent":0' /tmp/memo-credit-cycle.dm-resp.json 2>/dev/null; then
          echo "⛔ DM ACCEPTED BUT NOT ROUTED (sent:0) -- the shortfall stands and reached nobody."
        else echo "✅ shortfall DM delivered"; fi ;;
    *)  echo "⛔ DM SEND FAILED (HTTP ${DM_CODE:-<none>}) -- the shortfall stands; it reached nobody."
        head -c 300 /tmp/memo-credit-cycle.dm-resp.json 2>/dev/null ;;
  esac
fi

echo "════ cycle done $(date -Is)"
printf '%s\n' "${rcs[@]}"
printf '%s\n' "${rcs[@]}" | grep -vE '=0$|=0-window-edge$' && echo "⛔ phases above did not return 0" || echo "all phases OK" 
