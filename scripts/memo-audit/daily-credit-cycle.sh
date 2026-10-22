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

phase () {   # phase <name> <command...>
  local name="$1"; shift
  echo "════ ${name} $(date -Is)"
  "$@"; local rc=$?
  echo "──── ${name} rc=${rc}"
  rcs+=("${name}=${rc}")
  return 0
}

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
BATCHES=$(python3 - <<'PY'
import sys; sys.path.insert(0, ".")
import keyring
try:
    budget = sum(keyring.remaining(a) for a in keyring.load_grants())
except Exception:
    budget = 0.0
per_batch = 2.60                      # measured mean, 2026-10-22 runs
print(max(1, min(400, int(budget / per_batch))))
PY
)
echo "budget buys ~${BATCHES} batches"
phase audit /usr/bin/timeout 36000 python3 run.py --candidates candidates.json \
    --batch-size 6 --batches "$BATCHES" --concurrency 2 --wait-for-lock 7200

echo "════ cycle done $(date -Is)"
printf '%s\n' "${rcs[@]}"
printf '%s\n' "${rcs[@]}" | grep -v '=0$' && echo "⛔ phases above did not return 0" || echo "all phases rc=0"
