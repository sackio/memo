#!/usr/bin/env python3
"""Dispatch the Slack digest miners on grant credit.

Ben approved the phase (ask `a0t`, Slack #memo 2026-10-22) and the budget
direction the same day: *"I basically want you to use all these credits. No
limit usage ... so that we maximize them"*. So this bills a grant key, rolls to
the next account when one empties, and stops only when they are all gone.

⛔ IT TAKES THE FLEET-WIDE SPENDER LOCK. Two spenders cannot see each other's
in-flight cost -- the budget guard is blind by exactly the other process's
concurrency -- so the audit and this job run one at a time, never together.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "memo-audit"))
import keyring                                              # noqa: E402

CLAUDE = "/home/ben/.local/bin/claude"
PROMPT = HERE / "mine-prompt.md"
DIGESTS = HERE / "digests"

_INFLIGHT = 0.0
_LOCK = threading.Lock()


def run_group(group: list[Path], idx: int, outdir: Path, timeout: int, est: float) -> dict:
    """One worker over a handful of channels."""
    global _INFLIGHT
    with _LOCK:
        ok, why = keyring.guard_ok(reserve=_INFLIGHT + est, batch_est=est)
        if not ok:
            rec = {"group": idx, "channels": [p.stem for p in group], "skipped": why}
            (outdir / f"group-{idx:03d}.json").write_text(json.dumps(rec, indent=1))
            print(f"  group {idx}: ⛔ SKIPPED — {why}")
            return rec
        acct = keyring.next_acct(min_balance=_INFLIGHT + est)
        _INFLIGHT += est

    body = "\n\n".join(p.read_text() for p in group)
    prompt = PROMPT.read_text().replace("%%DIGESTS%%", body)
    t0 = datetime.now()
    try:
        proc = subprocess.run(
            [CLAUDE, "-p", prompt, "--output-format", "json",
             "--permission-mode", "bypassPermissions"],
            capture_output=True, text=True, timeout=timeout,
            env=keyring.key_env(acct), cwd=str(HERE))
        raw = proc.stdout
    except subprocess.TimeoutExpired:
        raw = ""
        timed_out = True
    else:
        timed_out = False
    finally:
        with _LOCK:
            _INFLIGHT -= est

    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = {"result": raw[:4000], "is_error": True}
    cost = float(parsed.get("total_cost_usd") or 0.0)

    # ⛔ A KILLED WORKER STILL SPENT THE MONEY. The cost only comes back in the
    # subprocess's own JSON, so a timeout reports nothing and the ledger would
    # show a free run. Log the estimate instead -- wrong by a little beats
    # absent, which is wrong by the whole amount and in the dangerous direction.
    mu = parsed.get("modelUsage") or {}
    if mu:
        for model, v in mu.items():
            keyring.log_spend(acct, model, float(v.get("costUSD") or 0.0))
    elif cost:
        keyring.log_spend(acct, "unknown", cost)
    elif timed_out:
        keyring.log_spend(acct, "unknown-timeout-estimate", est)
    rec = {"group": idx, "channels": [p.stem for p in group], "acct": acct,
           "cost_usd": cost, "wall_s": (datetime.now() - t0).total_seconds(),
           "is_error": bool(parsed.get("is_error")) or timed_out,
           "timed_out": timed_out, "result": parsed.get("result", "")}
    (outdir / f"group-{idx:03d}.json").write_text(json.dumps(rec, indent=1))
    print(f"  group {idx}: {'ERR' if rec['is_error'] else 'ok'} acct={acct} "
          f"${cost:.4f} {rec['wall_s']:.0f}s [{', '.join(rec['channels'])}]")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-worker", type=int, default=3, help="channels per worker")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--est", type=float, default=1.20, help="$ estimate per worker")
    ap.add_argument("--max-workers-total", type=int, default=0, help="0 = every digest")
    ap.add_argument("--outdir", default=str(HERE / "mine-runs"))
    ap.add_argument("--digests", default=str(DIGESTS),
                    help="digest directory; a subset is how you test one channel")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    digests = sorted(Path(a.digests).glob("*.md"))
    if not digests:
        print("⛔ no digests — run mine.py first. NOT SEARCHED, not a quiet day.")
        return 2
    groups = [digests[i:i + a.per_worker] for i in range(0, len(digests), a.per_worker)]
    if a.max_workers_total:
        groups = groups[:a.max_workers_total]

    print(f"digests {len(digests)} · workers {len(groups)} · "
          f"concurrency {a.concurrency} · est ${a.est:.2f}/worker")
    if a.dry_run:
        for i, g in enumerate(groups):
            print(f"  group {i}: {', '.join(p.stem for p in g)}")
        return 0

    try:
        _lock = keyring.acquire_spender_lock("slack-mine")
    except RuntimeError as e:
        print(f"⛔ {e}")
        return 3

    outdir = Path(a.outdir) / datetime.now().strftime("%Y%m%dT%H%M%S")
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"writing to {outdir}")
    with ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        recs = list(pool.map(lambda ig: run_group(ig[1], ig[0], outdir,
                                                  a.timeout, a.est),
                             enumerate(groups)))
    spent = sum(float(r.get("cost_usd") or 0) for r in recs)
    ran = sum(1 for r in recs if not r.get("skipped"))
    print(f"workers ran {ran}/{len(groups)} · spent ${spent:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
