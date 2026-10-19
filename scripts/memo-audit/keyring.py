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

# acct -> (grant_usd, renewal day-of-month).  Relayed from `agents` 2026-10-12.
# ⛔ admin deliberately absent: see the module docstring.
ACCOUNTS: dict[str, tuple[float, int]] = {
    "ben":       (200.0, 22),
    "agents":    (200.0, 24),
    "pushbuild": (200.0, 22),   # ⚠️ renewal day UNCONFIRMED; 22 is a placeholder
    "agents2":   (200.0, 18),
}


def _period_start(renewal_day: int, now: datetime) -> float:
    """Epoch of the most recent renewal, so spend is counted per grant period."""
    y, m = now.year, now.month
    if now.day < renewal_day:
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return datetime(y, m, renewal_day, tzinfo=timezone.utc).timestamp()


def spent(acct: str, now: datetime | None = None) -> float:
    """USD this module and the `agents` wrapper have logged in the CURRENT period.

    ⚠️ A floor, never the truth. Console is authoritative; anything spent outside
    the wrapper is invisible here, which is the same class of error as reading one
    host's /tmp and calling it the fleet.
    """
    now = now or datetime.now(timezone.utc)
    if acct not in ACCOUNTS:
        raise KeyError(f"unknown account {acct!r}")
    since = _period_start(ACCOUNTS[acct][1], now)
    total = 0.0
    if SPEND_LOG.exists():
        for line in SPEND_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue                      # a torn line is not a zero; skip it
            if r.get("acct") == acct and (r.get("ts") or 0) >= since:
                total += float(r.get("usd") or 0.0)
    return total


def remaining(acct: str) -> float:
    return max(0.0, ACCOUNTS[acct][0] - spent(acct))


def next_acct(now: datetime | None = None) -> str:
    """The account to spend next: soonest renewal first, among those with budget.

    ⭐ SOONEST-RENEWAL-FIRST IS THE WHOLE POINT, not round-robin. `agents` says to
    assume unused grant does NOT roll over, so the grant closest to resetting is
    the one whose balance is about to be destroyed. Round-robin would spread spend
    evenly and let the nearest deadline expire with money on it.
    """
    now = now or datetime.now(timezone.utc)
    live = [(a, remaining(a)) for a in ACCOUNTS]
    usable = [(a, r) for a, r in live if r > 1.0]
    if not usable:
        raise RuntimeError(
            "every tracked grant looks exhausted by the local ledger. That ledger "
            "is a floor, not the truth -- confirm against Console with `agents` "
            "before concluding there is no credit left."
        )

    def days_to_renewal(acct: str) -> int:
        day = ACCOUNTS[acct][1]
        d = now.day
        return (day - d) if d < day else (day - d + 30)

    usable.sort(key=lambda ar: (days_to_renewal(ar[0]), -ar[1]))
    return usable[0][0]


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
    return env


def log_spend(acct: str, model: str, usd: float) -> None:
    """Append one call's cost in the schema the `agents` seat already consumes."""
    rec = {"day": date.today().isoformat(), "ts": int(time.time()),
           "acct": acct, "model": model, "usd": round(float(usd), 6)}
    with SPEND_LOG.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    print(f"{'acct':<10} {'grant':>8} {'spent':>9} {'remaining':>10}  renews")
    for a in ACCOUNTS:
        print(f"{a:<10} {ACCOUNTS[a][0]:>8.2f} {spent(a, now):>9.4f} "
              f"{remaining(a):>10.2f}  day {ACCOUNTS[a][1]}")
    print(f"\nnext account to spend: {next_acct(now)}")
    print("⚠️ 'spent' counts only wrapper-logged calls. Console is authoritative.")
