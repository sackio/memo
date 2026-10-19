#!/usr/bin/env python3
"""Drive headless memo-audit workers across rotating API keys.

⭐ WHY HEADLESS AND NOT IN-SESSION SUBAGENTS. The `agents` seat established (DM
2026-10-19 11:27 EDT) that in-session subagents spend MAX SUBSCRIPTION QUOTA,
while the thing Ben wants consumed is the per-account Console API grant. Only a
`claude -p` invocation carrying ANTHROPIC_API_KEY touches the grant. A fleet of
in-session writers looks like the same work and bills the wrong pool entirely.

⚠️ MEASURED COST FLOOR: ~$0.22 per invocation before any work happens -- 42,686
cache-creation tokens of session boot, measured 2026-10-19 on a three-word
prompt. Batch accordingly: many tiny calls spend the grant on boot, not audit.

⛔ ONE MEMO BELONGS TO EXACTLY ONE BATCH. Two workers writing one document id
concurrently is how an append-retry duplicates text and how a compare-and-set
starts failing for reasons nobody can reproduce.
"""
from __future__ import annotations

import argparse, json, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import keyring

HERE = Path(__file__).resolve().parent
PROMPT = HERE / "worker-prompt.md"
CLAUDE = "/home/ben/.local/bin/claude"


def parse_result(raw: str) -> dict:
    """Extract the result JSON from `claude -p --output-format json` output.

    ⛔ A stderr warning line precedes the JSON ("claude.ai connectors are disabled
    because ANTHROPIC_API_KEY ... is set"), so `json.loads(raw)` fails on real
    output. Measured 2026-10-19. Skip to the first '{'.
    """
    i = raw.find("{")
    if i < 0:
        return {"is_error": True, "result": raw[:2000], "_parse": "no json found"}
    try:
        return json.loads(raw[i:])
    except ValueError as e:
        return {"is_error": True, "result": raw[i:i + 2000], "_parse": str(e)}


def _assignment_line(m: dict) -> str:
    """One assignment, for either shape of candidate.

    A single memo has `id`; a consolidation SET has `ids` + `chars` as a list.
    The set form must print every full uuid, because `/supersede-link` does not
    resolve an 8-char prefix the way `GET /documents/<prefix>` does, and a
    worker handed a prefix will fabricate the suffix and get a 404.
    """
    if m.get("ids"):
        bodies = ", ".join(f"{c} chars" for c in m.get("chars", []))
        ids = "\n".join(f"    - `{i}`" for i in m["ids"])
        return f"- SET, shared title: {m['title'][:110]}  ({bodies})\n{ids}"
    return (f"- `{m['id']}` — {m['title'][:110]} ({m['chars']} chars, last updated "
            f"{datetime.fromtimestamp(m['updated_at'], timezone.utc).date()})")


def run_batch(batch: list[dict], idx: int, outdir: Path, timeout: int,
              prompt_path: Path = PROMPT) -> dict:
    acct = keyring.next_acct()
    assignments = "\n".join(_assignment_line(m) for m in batch)
    prompt = prompt_path.read_text().replace("{{ASSIGNMENTS}}", assignments)

    t0 = time.time()
    proc = subprocess.run(
        [CLAUDE, "-p", prompt, "--output-format", "json",
         "--permission-mode", "bypassPermissions"],
        env=keyring.key_env(acct),            # ⛔ key lives only in this env dict
        capture_output=True, text=True, timeout=timeout, cwd=str(HERE))
    d = parse_result(proc.stdout or proc.stderr or "")
    cost = float(d.get("total_cost_usd") or 0.0)

    # Attribute spend to the account that actually paid, per model where known.
    mu = d.get("modelUsage") or {}
    if mu:
        for model, v in mu.items():
            keyring.log_spend(acct, model, float(v.get("costUSD") or 0.0))
    elif cost:
        keyring.log_spend(acct, "unknown", cost)

    rec = {"batch": idx, "acct": acct, "n": len(batch),
           "ids": [m["id"][:8] for m in batch],
           "cost_usd": cost, "wall_s": round(time.time() - t0, 1),
           "is_error": bool(d.get("is_error")), "num_turns": d.get("num_turns"),
           "result": d.get("result") or "", "parse_error": d.get("_parse")}
    (outdir / f"batch-{idx:04d}.json").write_text(json.dumps(rec, indent=1))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default="candidates.json")
    ap.add_argument("--batch-size", type=int, default=6)
    ap.add_argument("--batches", type=int, default=1, help="how many to run (pilot: 1)")
    ap.add_argument("--offset", type=int, default=0, help="skip this many memos first")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--outdir", default="runs")
    ap.add_argument("--prompt", default=str(PROMPT),
                    help="worker brief; defaults to the general truth audit")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cands = json.loads(Path(a.candidates).read_text())
    sel = cands[a.offset:a.offset + a.batch_size * a.batches]
    batches = [sel[i:i + a.batch_size] for i in range(0, len(sel), a.batch_size)]
    outdir = Path(a.outdir) / datetime.now().strftime("%Y%m%dT%H%M%S")

    print(f"candidates {len(cands)} · selected {len(sel)} · batches {len(batches)} "
          f"· batch-size {a.batch_size} · concurrency {a.concurrency}")
    print(f"brief: {a.prompt}")
    print(f"next account: {keyring.next_acct()} "
          f"(remaining ~${keyring.remaining(keyring.next_acct()):.2f}, local ledger)")
    if a.dry_run:
        for i, b in enumerate(batches):
            print(f"  batch {i}: " + ", ".join(
                "+".join(x[:8] for x in m["ids"]) if m.get("ids") else m["id"][:8]
                for m in b))
        return 0

    outdir.mkdir(parents=True, exist_ok=True)
    print(f"writing to {outdir}")
    done, spend = 0, 0.0
    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        futs = {ex.submit(run_batch, b, i, outdir, a.timeout, Path(a.prompt)): i
                for i, b in enumerate(batches)}
        for f in as_completed(futs):
            i = futs[f]
            try:
                r = f.result()
            except subprocess.TimeoutExpired:
                print(f"  batch {i}: ⛔ TIMEOUT after {a.timeout}s "
                      f"— INCONCLUSIVE, not a clean audit")
                continue
            except Exception as e:                      # noqa: BLE001
                print(f"  batch {i}: ⛔ FAILED {type(e).__name__}: {e}")
                continue
            done += 1
            spend += r["cost_usd"]
            flag = "⛔ ERROR" if r["is_error"] else "ok"
            print(f"  batch {i}: {flag} acct={r['acct']} ${r['cost_usd']:.4f} "
                  f"{r['wall_s']}s turns={r['num_turns']}")
    print(f"\n{done}/{len(batches)} batches completed · ${spend:.4f} this run")
    print(f"⚠️ completed is not the same as correct — read the batch JSON in {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
