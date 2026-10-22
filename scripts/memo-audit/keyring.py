#!/usr/bin/env python3
"""Rotate Anthropic API keys so the monthly Console grants get used, not wasted.

Ben, Slack #memo 2026-10-19 11:31 EDT: "we have multiple credits from different
keys so you'll have to handle rotating keys and stuff you should work on it with
agents but we want to use all our credits which I think are like $1,000 a month."

⛔ THE KEY VALUE NEVER ENTERS THIS PROCESS'S OUTPUT, ARGV, OR A TRANSCRIPT.
`key_env()` returns a dict for `subprocess(env=...)` and nothing else. There is
deliberately no function that returns the key as a value a caller might print,
and no `--show`. A credential in a transcript is unrecoverable: transcripts are
archived hourly to /mnt/backup/claude-logs with no --delete, on all four hosts.

⛔ NEVER admin.key. Per the `agents` seat (DM 2026-10-19 11:29 EDT, relayed from
memo 34fa4ebc): no credit link is recorded for the admin org, so the $7.44
already logged against it may be REAL BILLING rather than grant spend. The whole
point of this script is to spend money that is already paid for.

⚠️ RENEWAL DATES AND GRANT SIZES ARE RELAYED, NOT MEASURED. They come from
`agents`' 2026-10-12 Console reading. Console is the only authoritative balance
and neither seat can read it from here. So this module tracks what IT has spent
and treats the grant as a soft ceiling -- it will not stop you spending real
money if a grant turns out to be absent or exhausted. Re-confirm with `agents`
before trusting `remaining()` for anything that matters.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

KEY_DIR = Path("/home/ben/.claude/state/api-keys")
SPEND_LOG = KEY_DIR / "spend.jsonl"          # schema shared with the `agents` seat

# ⭐ SINGLE SOURCE OF TRUTH, shared with the `agents` seat (its 8990793, this
# file 2026-10-19): /home/ben/.claude/state/api-keys/grants.json, shape
#   {"<acct>": {"monthly": 200, "renews": "YYYY-MM-DD" | null}}
# `renews` null means the renewal date is genuinely UNKNOWN -- treated as a
# rolling 30-day window, never as "no limit". admin is absent from the file and
# is additionally refused below; see the module docstring.
#
# ⭐ CONFIRMED BY `agents` 2026-10-19: the "Max API credit" ($200/mo on a 20x
# account) and the per-account Console grant are THE SAME POOL, not two budgets.
# Its earlier $1,200/mo figure (6 accounts) was wrong -- agents3 has no offer and
# admin has no recorded link -- so the real pool is $800/mo ~ $26/day. The
# fleet-wide $40/day cap in memo `ee0c98c5` was sized off that wrong $1,200.
#
# ⛔ A HARDCODED TABLE LIVED HERE UNTIL 2026-10-19 AND IT CARRIED A WRONG DATE
# (pushbuild "22", commented as a placeholder) THAT WAS ABOUT TO BE SPENT
# AGAINST. Two seats keeping private copies of the same budget is how one of
# them acts on a number the other has already corrected.
GRANTS_PATH = KEY_DIR / "grants.json"
ROLLING_DAYS = 30
NEVER = {"admin"}            # no linked grant => real billing. See docstring.


def _add_months(d: date, k: int) -> date:
    m = d.month + k
    y = d.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    # clamp for short months: a grant that renews on the 31st renews on the 30th
    # in a 30-day month, and date() raises rather than clamping on its own.
    for day in range(d.day, 0, -1):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError("unreachable")


def load_grants() -> dict[str, dict]:
    """Read the shared grants file. ⛔ Never falls back to a baked-in table.

    A missing or corrupt file is an ERROR, not an empty budget: defaulting here
    would spend real money against an imagined grant, which is the exact failure
    `admin.key` already caused once (memo `34fa4ebc`).
    """
    try:
        g = json.loads(GRANTS_PATH.read_text())
    except FileNotFoundError:
        raise FileNotFoundError(
            f"{GRANTS_PATH} is missing -- it is the shared source of truth for "
            f"grant sizes and renewal dates (owned with the `agents` seat). "
            f"Refusing to guess a budget.") from None
    return {a: v for a, v in g.items() if a not in NEVER}


def _period_start(renews: str | None, now: datetime) -> float:
    """Epoch of the most recent renewal, so spend is counted per grant PERIOD."""
    if not renews:
        return now.timestamp() - ROLLING_DAYS * 86400     # unknown => rolling
    nxt = date.fromisoformat(renews)
    today = now.date()
    while nxt <= today:                   # a stale file must not freeze the window
        nxt = _add_months(nxt, 1)
    prev = _add_months(nxt, -1)
    return datetime(prev.year, prev.month, prev.day, tzinfo=timezone.utc).timestamp()


def spent(acct: str, now: datetime | None = None) -> float:
    """USD this module and the `agents` wrapper have logged in the CURRENT period.

    ⚠️ A floor, never the truth. Console is authoritative; anything spent outside
    the wrapper is invisible here, which is the same class of error as reading one
    host's /tmp and calling it the fleet.
    """
    now = now or datetime.now(timezone.utc)
    grants = load_grants()
    if acct not in grants:
        raise KeyError(f"unknown account {acct!r} (not in {GRANTS_PATH})")
    since = _period_start(grants[acct].get("renews"), now)
    return _sum_spend(now, acct=acct, since=since)


def _sum_spend(now: datetime, acct: str | None = None, since: float = 0.0,
               actor: str | None = None, day: str | None = None) -> float:
    total = 0.0
    if not SPEND_LOG.exists():
        return total
    for line in SPEND_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue                      # a torn line is not a zero; skip it
        if acct is not None and r.get("acct") != acct:
            continue
        if day is not None and r.get("day") != day:
            continue
        if actor is not None and r.get("actor") != actor:
            continue
        if (r.get("ts") or 0) < since:
            continue
        total += float(r.get("usd") or 0.0)
    return total


def remaining(acct: str) -> float:
    """What is actually left on the account, Console-first.

    ⛔⛔ `monthly - spent()` IS NOT THE BALANCE, AND IT ERRS UPWARD — the one
    direction that overspends. The local ledger is a FLOOR: it records the cost
    the Claude Code CLI reports, so anything spent outside the wrapper, and any
    premium the CLI omits, is invisible. Measured 2026-10-22 on `ben`: ledger
    said $38.99 left, Console said $29.30. A percentage margin on the ledger
    figure does not save you — 8% off $38.99 is $35.87, still $6.57 more than
    exists. ⇒ WHEN A CONSOLE READ EXISTS, IT IS THE BALANCE, and the ledger's
    job is only to age it forward.

    ⭐ A BALANCE IS A FACT ABOUT AN INSTANT. `console_left` is meaningless
    without `console_at`, so this subtracts only spend logged AFTER that
    instant; spend before it is already inside the Console figure and
    subtracting it again would double-count.
    ⭐ THE MARGIN GOES ON THE LEDGER TERM, NOT ON THE BALANCE. A Console figure
    is exact at its instant; only the spend aged onto it is uncertain. Marking
    the whole balance down 8% would strand $16 of a $200 grant for no reason —
    which is the stranding this rewrite exists to stop — while marking up the
    ledger term costs pennies an hour after a fresh read and grows only as the
    read goes stale. Same protection, applied where the error actually is.
    """
    g = load_grants()[acct]
    left = g.get("console_left")
    at = g.get("console_at")
    if left is not None and at:
        since = datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
        aged = _sum_spend(datetime.now(timezone.utc), acct=acct, since=since)
        return max(0.0, float(left) - aged * (1.0 + GRANT_MARGIN))
    # No Console read: the whole figure is ledger-derived and erring upward, so
    # the markdown applies to all of it.
    return max(0.0, (float(g.get("monthly") or 0.0) - spent(acct)) * (1.0 - GRANT_MARGIN))


# ── the binding constraint is the GRANT BALANCE, not a daily number ─────────
# Ben, Slack #memo 2026-10-22 11:42 EDT, VERBATIM: "I basically want you to use
# all these credits. No limit usage no whatever just use them up to the amount
# that we have so that we maximize them if we start to have other things
# competing for these credits then we can read discuss but my thought is that I
# want this to be used to just improve and maintain the memo for everyone."
#
# ⛔⛔ A FLAT DAILY CAP CANNOT SPEND A GRANT THAT EXPIRES SOON, AND IT FAILS
# SILENTLY — it refuses work while money dies, and every refusal looks like the
# guard working. Measured 2026-10-22: `agents@sack.io` held $199.68 expiring
# 10/26, four days out. At $40/day that stranded ~$38 with nothing to signal it.
# ⇒ The cap is now each ACCOUNT'S REMAINING BALANCE. Unused grant is destroyed
#   at expiry, so the only real budget is "what is left on the account I am
#   about to bill", and the only real deadline is that account's expiry.
#
# ⚠️ WHAT THE OLD $40 WAS ACTUALLY PROTECTING IS GONE. It was agreed with
# `agents` 2026-10-19 after this seat spent $69.47 in a day and darkened fleet
# haiku. That blackout was the OLD FLEET-WIDE daily cap letting one actor's
# spend refuse every other script's calls; it is per-actor now (`spent_today`
# filters on `actor`), so this seat can no longer starve another. Confirmed by
# `agents` 2026-10-22 and verified here. ⇒ The runaway risk that remains is a
# LOOP, not a budget, and RUNAWAY_BATCH_USD is the guard for that.
RUNAWAY_BATCH_USD = 25.0
# One batch has never cost more than $4.18 (the 2026-10-20 head). A single
# batch asking for 6x that is a bug, not a big job, and it is the only failure
# mode a balance check cannot catch — a balance stops you at $200 of garbage,
# which is $200 too late.

# ⚠️ THE LEDGER IS A FLOOR, SO SPENDING TO EXACTLY `remaining()` OVERSHOOTS.
# Measured under-report against Console: 1.2% on pushbuild (2026-10-19, $2.44)
# and ~6% on ben (2026-10-22, ~$9.70 — the CLI's own `total_cost_usd` is what
# this ledger records, so the shortfall is in what the CLI REPORTS, not in any
# arithmetic here). Hold back the larger of the two, rounded up.
# ⭐ This is not a hedge against Ben's instruction, it is how you obey it: he
#   said use them "up to the amount that we have", and the amount we have is
#   known only to Console. The held-back tail is RELEASABLE — have `agents` read
#   Console and reconcile the ledger, and it spends.
GRANT_MARGIN = 0.08


def daily_cap(now: datetime | None = None) -> float:
    """⛔ RETIRED 2026-10-22 — kept only so an old caller fails loudly.

    There is no daily cap any more. Call `guard_ok`, which binds on the
    account's remaining balance.
    """
    raise RuntimeError(
        "DAILY_CAP was retired 2026-10-22 on Ben's instruction (#memo 11:42). "
        "The budget is now each account's remaining grant -- use guard_ok(acct=...)."
    )


def this_actor() -> str:
    """The seat this process bills as. ⚠️ Read from the PARENT env on purpose --
    `key_env()` strips ATC_* from CHILDREN so workers cannot steal the broker
    registration. Same variable, opposite requirements."""
    return (os.environ.get("MEMO_AUDIT_ACTOR")
            or os.environ.get("ATC_SUBSCRIBER_ID") or "memo")


def spent_today(actor: str | None = None, now: datetime | None = None) -> tuple[float, float]:
    """(mine, unattributed) USD logged today.

    ⛔ THE SECOND NUMBER EXISTS BECAUSE `actor` WAS ONLY ADDED 2026-10-19. Every
    row written before that has no actor at all, so a guard that counted only
    `actor == me` read $0.00 on the very day this seat spent $69.47 -- it would
    have cheerfully authorised another full day's budget on top of a breach.
    ⇒ An attribution field makes history MORE ambiguous, not less, until the
    backlog ages out: rows that predate it are not "someone else's", they are
    unknown. Count unknown against yourself while any remain in the window; it
    is the only direction that cannot under-report.
    """
    now = now or datetime.now(timezone.utc)
    actor = actor or this_actor()
    today = date.today().isoformat()
    mine = _sum_spend(now, actor=actor, day=today)
    total = _sum_spend(now, day=today)
    attributed = 0.0
    if SPEND_LOG.exists():
        for line in SPEND_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("day") == today and r.get("actor"):
                attributed += float(r.get("usd") or 0.0)
    return mine, max(0.0, total - attributed)


def guard_ok(now: datetime | None = None, reserve: float = 0.0,
              acct: str | None = None, batch_est: float | None = None) -> tuple[bool, str]:
    """(may_spend, human reason). Checked BEFORE each batch, not at kickoff.

    ⭐ The 2026-10-19 breach happened because the cap was checked at the start of
    a 6-memo pilot and never again as the work scaled to 85 sets. A guard that is
    only consulted once is a guard on the smallest thing you ever did.

    ⛔⛔ `reserve` IS MONEY ALREADY COMMITTED AND NOT YET BILLED, AND OMITTING IT
    BREACHED THE CAP A SECOND TIME. Measured 2026-10-20: 12 batches at
    concurrency 4 spent $50.21 against a $40 cap. Nothing was wrong with the
    per-batch check -- every batch that started was under the cap WHEN IT ASKED.
    But `log_spend` only runs when a subprocess RETURNS, so four batches in
    flight are four unbilled charges the ledger cannot see, and each one reads
    the same stale total. ⇒ A GUARD THAT READS ONLY SETTLED SPEND IS BLIND BY
    EXACTLY ITS OWN CONCURRENCY. Callers running work in parallel must pass the
    estimated cost of everything in flight, including the batch being asked
    about. See run.py's `_INFLIGHT`.
    """
    now = now or datetime.now(timezone.utc)
    acct = acct or next_acct(now)
    budget = remaining(acct)   # already margined, at the term that is uncertain
    mine, unattributed = spent_today(now=now)
    basis = "Console" if load_grants()[acct].get("console_left") is not None else "ledger only"
    detail = (f"{acct}: ${budget:.2f} spendable ({basis}, {GRANT_MARGIN:.0%} margin "
              f"on the ledger term)"
              + (f"; ${reserve:.2f} in flight" if reserve else "")
              + f" (today so far ${mine + unattributed:.2f})")
    # ⛔ COMPARE THE SINGLE BATCH, NOT `reserve`. `reserve` is CUMULATIVE work in
    # flight, so at concurrency 4 it legitimately reaches ~$16-20 and a per-batch
    # ceiling checked against it would refuse healthy runs. The first version of
    # this guard did exactly that -- the constant said "a single batch" and the
    # comparison used the total, which is the same name-vs-quantity conflation
    # that cost three wrong findings on 2026-10-22.
    if batch_est is not None and batch_est >= RUNAWAY_BATCH_USD:
        return False, (f"RUNAWAY GUARD TRIPPED: one batch estimated ${batch_est:.2f}, over "
                       f"${RUNAWAY_BATCH_USD:.2f}. No batch has ever cost more than $4.18. "
                       f"This is a loop or a bad estimate, not a big job -- fix the caller, "
                       f"do not raise the constant to get past it.")
    if reserve >= budget:
        return False, (f"grant exhausted: {detail} -- stopping. ⭐ The tail is RELEASABLE, "
                       f"not gone: have `agents` read Console and reconcile the ledger, then "
                       f"the margin spends. Switching accounts is automatic via next_acct().")
    return True, f"grant guard: {detail}"


def next_acct(now: datetime | None = None) -> str:
    """The account to spend next: soonest EXPIRY first, among those with budget.

    ⭐ SOONEST-EXPIRY-FIRST IS THE WHOLE POINT, not round-robin. Unused grant is
    DESTROYED at expiry, so the grant closest to dying is the one whose balance
    is about to be lost. Round-robin spreads spend evenly and lets the nearest
    deadline expire with money on it.

    ⚠️ An account with NEITHER date sorts LAST, not first: an unknown date
    cannot be argued to be urgent, and acting on a placeholder is how the old
    hardcoded table nearly doubled a burst on a guessed pushbuild date.

    ⭐ SORTS ON `expires` AS OF 2026-10-22, WHICH IS WHAT THE DEADLINE ACTUALLY IS.
    It used to sort on `renews` under a standing note to switch once the per-grant
    expiries landed. They landed: `agents` had unibrowse read them from Console on
    2026-10-22 and they are in grants.json now.

    ⛔ THE TWO ARE DIFFERENT EVENTS AND ONLY ONE DESTROYS MONEY. Each grant is its
    own line expiring 30 days after it was GRANTED, so `renews` is when the NEXT
    grant ARRIVES and `expires` is when the current BALANCE dies. A $60/day burst
    once lived in this file for 40 minutes built on exactly that conflation, and
    on 2026-10-22 this seat put a $90/day ask to Ben built on an inverted Console
    reading. Confirm what a date is OF before using it as a deadline.
    """
    now = now or datetime.now(timezone.utc)
    grants = load_grants()
    usable = [(a, remaining(a)) for a in grants if remaining(a) > 1.0]
    if not usable:
        raise RuntimeError(
            "every tracked grant looks exhausted by the local ledger. That ledger "
            "is a floor, not the truth -- confirm against Console with `agents` "
            "before concluding there is no credit left."
        )

    def sort_key(ar: tuple[str, float]):
        g = grants[ar[0]]
        # `expires` is the real deadline. `renews` is only a fallback, because
        # an account with no Console read still has to sort somewhere sane.
        deadline = g.get("expires") or g.get("renews")
        if not deadline:
            return (1, 10**6, -ar[1])              # unknown date => last
        return (0, (date.fromisoformat(deadline) - now.date()).days, -ar[1])

    usable.sort(key=sort_key)
    return usable[0][0]


def log_unmeasured(acct: str, reason: str, est_usd: float) -> None:
    """Record that spend HAPPENED but its cost was never reported back.

    ⛔ WHY THIS EXISTS. `log_spend` runs only after `subprocess.run` RETURNS, so a
    worker killed by `--timeout` or by a signal burns real tokens and writes NO
    ledger row at all. Measured 2026-10-19: an aborted run left a complete, good
    memo in the corpus and $0.00 in the ledger. Every timed-out batch in this
    tool's history is therefore unaccounted, and a daily guard that sums `usd`
    cannot see any of it.

    ⇒ The row is written with `estimated: true` so a reader can tell a measured
    cost from a stand-in. ⚠️ An estimate in the ledger is a compromise: it is
    wrong by construction. It is still strictly better than a zero, because a
    zero is also wrong AND claims to be measured.
    """
    rec = {"day": date.today().isoformat(), "ts": int(time.time()),
           "acct": acct, "model": "unknown", "usd": round(float(est_usd), 6),
           "actor": this_actor(), "estimated": True, "reason": reason}
    with SPEND_LOG.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def key_env(acct: str, base: dict | None = None) -> dict:
    """An env dict carrying the key, for subprocess(env=...) ONLY.

    ⛔ Do not log, echo, or interpolate the result. Read with the file's own 0600
    permissions and keep it in this process's memory for the length of one call.
    """
    p = KEY_DIR / f"{acct}.key"
    if not p.is_file():
        raise FileNotFoundError(f"no key file for {acct!r} at {p}")
    val = p.read_text().strip()
    if not val:
        raise ValueError(f"key file for {acct!r} is empty -- an empty key reads "
                         f"to the API as absent auth, not as an error")
    env = dict(base if base is not None else os.environ)
    env["ANTHROPIC_API_KEY"] = val

    # ⛔⛔ STRIP ATC_* OR EVERY WORKER STEALS THIS SEAT'S BROKER IDENTITY.
    # `atc-session-start.sh` registers with the broker using $ATC_SUBSCRIBER_ID
    # and exits 0 without registering when ATC_SUBSCRIBER_ID, ATC_ZONES and
    # ATC_TEAM are all unset (hook lines 14-16). Inheriting os.environ handed
    # every `claude -p` worker ATC_SUBSCRIBER_ID=memo, so each one re-registered
    # as `memo`, pointed the registration at itself, and then EXITED.
    #
    # MEASURED 2026-10-19: the live `memo` row read
    #   description: "Claude Code session in .../scripts/memo-audit"
    # -- a worker's cwd, not the seat's -- and DMs to `memo` stopped arriving at
    # the real pane from ~12:50. Ben's Slack replies (13:33, 14:24, 15:11) were
    # never delivered; they were found by reading the channel directly. The seat
    # looked registered and healthy the whole time, which is why nothing alarmed.
    # Reported by the `agents` seat.
    #
    # ⇒ A child process that inherits an identity variable does not merely read
    # it, it BECOMES that identity to anything keyed on it. Strip identity from
    # a child env; pass only capability.
    for k in [k for k in env if k.startswith("ATC_")]:
        del env[k]
    return env


def log_spend(acct: str, model: str, usd: float) -> None:
    """Append one call's cost in the schema the `agents` seat already consumes.

    ⭐ `actor` NAMES THE SEAT, AND WITHOUT IT THE FLEET CAP IS UNENFORCEABLE.
    Added 2026-10-19 at the `agents` seat's request, matching what its `haiku`
    wrapper writes as of c605d90 (HAIKU_ACTOR or ATC_SUBSCRIBER_ID). Until both
    sides wrote it the ledger held only acct/model/usd/ts/day, so spend could not
    be attributed to a seat: on 2026-10-19 this seat could identify its own
    $69.47 only by matching Opus-1m amounts against its own batch records, and
    the $40/day cap could be breached without anyone being able to show who did
    it. A cap nobody can attribute is advisory.

    ⚠️ Reads ATC_SUBSCRIBER_ID from THIS process, which is correct: `log_spend`
    runs in the PARENT. `key_env()` strips ATC_* from the CHILD so workers cannot
    steal the broker registration -- a different problem, same variable, and the
    two fixes must not be confused into stripping it here too.
    """
    actor = (os.environ.get("MEMO_AUDIT_ACTOR")
             or os.environ.get("ATC_SUBSCRIBER_ID") or "memo")
    rec = {"day": date.today().isoformat(), "ts": int(time.time()),
           "acct": acct, "model": model, "usd": round(float(usd), 6),
           "actor": actor}
    with SPEND_LOG.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    # ⛔ THIS BLOCK READ A MODULE-LEVEL `ACCOUNTS` TABLE UNTIL 2026-10-19 AND
    # DIED WITH A NameError THE MOMENT e72e5bf MADE grants.json THE SOURCE OF
    # TRUTH. The table was deleted; the only thing that REPORTED it was not.
    # So `python3 keyring.py` -- the status view this seat and `agents` share
    # for a budget we both spend against -- was dead for hours, and nobody saw
    # it because the one invocation of it piped stderr to /dev/null and read
    # the surviving header row as "no accounts".
    # ⇒ WHEN YOU RETIRE A DATA SOURCE, GREP FOR ITS READERS. A migration that
    # updates every writer and no reporter fails only where someone is looking
    # for reassurance, which is the worst place to put a silent failure.
    # (Same shape as memo-minder's `office:8000`, which survived 11 days in the
    # commands beside the table documenting that office was dead.)
    now = datetime.now(timezone.utc)
    grants = load_grants()

    print(f"{'acct':<10} {'grant':>8} {'spent':>9} {'remaining':>10}  renews")
    for a in sorted(grants):
        print(f"{a:<10} {float(grants[a].get('monthly') or 0.0):>8.2f} "
              f"{spent(a, now):>9.4f} {remaining(a):>10.2f}  "
              f"{grants[a].get('renews') or '(unknown -- sorts LAST)'}")
        if grants[a].get("_note"):
            print(f"{'':<10} ↳ {grants[a]['_note']}")

    # ⚠️ A KEY FILE WITH NO GRANT ENTRY BILLS REAL MONEY. admin.key is Laura's
    # and has no linked grant; surface it here rather than letting it be found
    # by a charge. `NEVER` is the refusal list, not documentation.
    untracked = sorted(k.stem for k in KEY_DIR.glob("*.key") if k.stem not in grants)
    if untracked:
        print(f"\nkeys with NO grant (REAL BILLING; {'refused' if NEVER else 'allowed'}"
              f" for {sorted(NEVER)}): {', '.join(untracked)}")

    print(f"\nnext account to spend: {next_acct(now)}")
    ok, why = guard_ok(now)
    print(("⛔ " if not ok else "✅ ") + why)
    print("⚠️ 'spent' counts only wrapper-logged calls. Console is authoritative: "
          "measured 2026-10-19, this ledger under-reported pushbuild's Console "
          "balance by $2.44 (1.2%), so any cap enforced here runs ~1% permissive.")
