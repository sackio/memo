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
SLACK=../slack-archive
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

phase () {   # phase <name> <command...>
  local name="$1"; shift
  local left=$(( DEADLINE - $(date +%s) ))
  if [ "$left" -lt "$PHASE_FLOOR_S" ]; then
    echo "════ ${name} SKIPPED $(date -Is): ${left}s left in the 22:00-${WINDOW_END_H}:00 window, floor ${PHASE_FLOOR_S}s"
    rcs+=("${name}=SKIPPED-window")
    return 0
  fi
  # ⛔ Clamp this phase's own timeout to the deadline. The arg list is
  #    `/usr/bin/timeout <secs> <cmd...>`, so rewrite element 2 when it is that shape.
  if [ "${1:-}" = "/usr/bin/timeout" ] && [ "${2:-}" -eq "${2:-}" ] 2>/dev/null; then
    local want="$2"
    if [ "$want" -gt "$left" ]; then
      echo "──── ${name}: timeout ${want}s clamped to ${left}s (window)"
      set -- "$1" "$left" "${@:3}"
    fi
  fi
  echo "════ ${name} $(date -Is)"
  "$@"; local rc=$?
  echo "──── ${name} rc=${rc}"
  rcs+=("${name}=${rc}")
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
phase slack-mine     /usr/bin/timeout 10800 python3 "$SLACK/mine-run.py" --per-worker 4 --concurrency 2 --wait-for-lock 7200
phase refute-recheck /usr/bin/timeout 1800 python3 refute-recheck.py --queue
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
phase probe-coverage /usr/bin/timeout 14400 python3 run.py \
    --candidates candidates-all.json --prompt probe-prompt.md --model sonnet \
    --batch-size 10 --batches 60 --concurrency 2 --wait-for-lock 7200 --skip-probed

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
import sys; sys.path.insert(0, ".")
import keyring
try:
    budget = sum(keyring.remaining(a) for a in keyring.load_grants())
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
  phase audit /usr/bin/timeout 36000 python3 run.py --candidates candidates.json \
      --batch-size 6 --batches "$BATCHES" --concurrency 2 --wait-for-lock 7200
fi

echo "════ cycle done $(date -Is)"
printf '%s\n' "${rcs[@]}"
printf '%s\n' "${rcs[@]}" | grep -v '=0$' && echo "⛔ phases above did not return 0" || echo "all phases rc=0"
