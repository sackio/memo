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

# ⛔⛔ A CYCLE THAT RAN AND SPENT NOTHING EXITS 0 AND LOOKS EXACTLY LIKE A GOOD NIGHT.
#    That is the failure that costs money here: a grant expires on a date, so the loss is
#    silent, gradual and never produces an error. The cronguard wrapper only writes a line
#    on a NON-ZERO exit, and nobody reads that file. ⇒ Snapshot the budget now, diff it at
#    the end, and DM the seat when the night fell short of what the soonest expiry needs.
BUDGET_BEFORE=/tmp/memo-credit-cycle.before.json
python3 - > "$BUDGET_BEFORE" <<'PYSNAP' || echo "⚠ budget snapshot FAILED -- the end-of-run shortfall check will say so, not assume zero" >&2
import sys, json; sys.path.insert(0, ".")
import keyring
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
PHASE_RESERVE="$AUDIT_RESERVE_S" \
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
PHASE_RESERVE="$AUDIT_RESERVE_S" \
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

# ── what the night actually bought ─────────────────────────────────────
# ⚠ Reports per ACCOUNT, never a pooled total. $200 expiring in 33 days and $125 expiring
#   in 4 are not interchangeable, and a pooled figure is the one number that hides the only
#   question worth asking: is the money that disappears FIRST being spent?
BROKER=http://192.168.1.168:3030
SHORTFALL_REPORT=/tmp/memo-credit-cycle.shortfall.txt
python3 - "$BUDGET_BEFORE" > "$SHORTFALL_REPORT" 2>&1 <<'PYACCT'
import sys, json, datetime
sys.path.insert(0, ".")
import keyring
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
subj = "credit cycle: SHORTFALL against the soonest grant expiry" if rc == "1" \
       else "credit cycle: tonight's spend could not be verified"
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
printf '%s\n' "${rcs[@]}" | grep -v '=0$' && echo "⛔ phases above did not return 0" || echo "all phases rc=0"
