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

import argparse, json, re, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import keyring

HERE = Path(__file__).resolve().parent
PROMPT = HERE / "worker-prompt.md"
CLAUDE = "/home/ben/.local/bin/claude"
API_BASE = "http://server4:8000"

# ⛔ WRAP EVERY WORKER, DO NOT RENICE THE PARENT. A batch worker is background
# work competing with interactive seats and, on server4, with the root disk that
# holds every session transcript. Renicing the parent looks like it covers the
# children and does not: measured 2026-10-22 by `system`, workers spawned after
# the parent was set to nice 19 / ionice idle came up at nice 0 and ionice none,
# because the CLI re-execs. The priority has to be on the exec itself.
NICE = ["/usr/bin/ionice", "-c3", "/usr/bin/nice", "-n19"]


# Top-level keys that identify a worker report, one per brief this runner drives.
REPORT_KEYS = {"audited", "probed", "stored"}


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
    # Size in qwen3 tokens when the selector measured it. ⛔ `chars` cannot
    # stand in: the 16,384-token ceiling is counted by the embedder's
    # tokenizer, and chars-per-token varies enough across these memos that a
    # char count says nothing about how close one is to a refused write.
    size = ""
    if m.get("tokens_qwen3"):
        size = f", {m['tokens_qwen3']} qwen3 tok, {m.get('headroom')} to the cap"
    return (f"- `{m['id']}` — {m['title'][:110]} ({m['chars']} chars{size}, "
            f"last updated "
            f"{datetime.fromtimestamp(m['updated_at'], timezone.utc).date()})")


# batch index -> account, filled in BEFORE the subprocess runs. The timeout
# handler in main() needs it, and by then run_batch has raised and returned
# nothing -- so the account cannot be read from its result.
ACCT_FOR_BATCH: dict[int, str] = {}

# ⛔⛔ MONEY COMMITTED BUT NOT YET BILLED. `log_spend` runs only when a subprocess
# RETURNS, so with --concurrency N there are up to N charges the ledger cannot
# see, and every in-flight batch reads the same stale total. Measured 2026-10-20:
# 12 batches at concurrency 4 spent $50.21 against the $40 cap -- each one was
# legitimately under the cap at the moment it asked. ⇒ Reserve the estimate
# BEFORE spawning and release it after, so the guard sees the committed total.
_INFLIGHT = 0.0
_INFLIGHT_LOCK = threading.Lock()


def mean_batch_cost(default: float = 1.00) -> float:
    """Mean cost of a COMPLETED batch, for standing in when one is killed.

    ⛔ READ THE BATCH RECORDS, NOT THE SPEND LEDGER. A first version averaged
    ledger rows for this actor and returned $0.0000 -- the ledger mixes a
    $0.000004 haiku probe in with $1.80 audit batches, and one trivial row drags
    the mean to zero. An estimator that silently returns zero is worse than no
    estimator: it writes a confident "nothing was spent" into the budget.
    ⇒ Average the thing you are actually estimating (one batch), from the records
    that only ever contain that thing.
    """
    vals = []
    # ⚠️ TRAILING WINDOW, DELIBERATELY SHORT. Cost per memo is not constant
    # across the queue: the read-weighted ordering puts the largest, most
    # cross-referenced memos first, and the 2026-10-20 head cost $4.18/batch
    # against a $1.75 projection from an older stratified sample. A 60-batch
    # mean dilutes the region actually being worked with runs priced for a
    # different one. Track the recent past, which is what the next batch
    # resembles.
    for f in sorted((HERE / "runs").glob("*/batch-*.json"))[-12:]:
        if ".raw." in f.name:
            continue
        try:
            v = float(json.loads(f.read_text()).get("cost_usd") or 0.0)
        except (ValueError, OSError):
            continue
        if v > 0:
            vals.append(v)
    return round(sum(vals) / len(vals), 4) if vals else default


def _report_from_session(session_id: str | None, cwd: Path) -> dict | None:
    """Pull the JSON report out of the worker's session transcript.

    ⛔⛔ `result` IS ONLY THE LAST ASSISTANT TURN, AND THE REPORT IS OFTEN NOT IN
    IT. Measured 2026-10-21, batch 0 of the 00:07 run: the worker emitted a
    correct report, then added one more turn saying "The report above is
    final". `result` captured the 136-character afterword and nothing else, so
    the batch record held no verdicts and **six route findings for `finance`,
    `super-agent`, `cluster` and `hvac` were lost** — while the memo edits
    themselves had already landed in the corpus.

    ⭐ THAT IS THE WORST SHAPE AVAILABLE: the work happened, the record of WHY
    did not, and the batch looks merely chatty rather than broken. The prompt
    does say "finish with a JSON block and nothing after it", but an
    instruction is not a mechanism, and a lost finding is not recoverable from
    the corpus — an edit shows WHAT changed and never what the worker noticed
    about the fleet while making it.

    The transcript path is deterministic: `~/.claude/projects/<slug>/<id>.jsonl`
    with slug = the cwd's absolute path, '/' and '_' both replaced by '-'.
    """
    if not session_id:
        return None
    slug = str(cwd).replace("/", "-").replace("_", "-")
    t = Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl"
    if not t.exists():
        return None
    texts: list[str] = []
    for line in t.read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue                      # a torn line is not an empty file
        if r.get("type") != "assistant":
            continue
        c = (r.get("message") or {}).get("content")
        if isinstance(c, list):
            texts += [b["text"] for b in c
                      if isinstance(b, dict) and b.get("type") == "text"]
        elif isinstance(c, str):
            texts.append(c)
    for text in reversed(texts):
        for blob in reversed(re.findall(r"```json\s*(.*?)```", text, re.S)):
            try:
                cand = json.loads(blob)
            except ValueError:
                continue
            if isinstance(cand, dict) and REPORT_KEYS.intersection(cand):
                return cand
    return None


def run_batch(batch: list[dict], idx: int, outdir: Path, timeout: int,
              prompt_path: Path = PROMPT, model: str | None = None) -> dict:
    # ⛔ CHECK THE BUDGET HERE, PER BATCH -- NOT ONCE AT KICKOFF. On 2026-10-19
    # this seat went 1.7x over a fleet cap because the budget was checked when
    # the job was a 6-memo pilot and never again as it scaled to 85 sets. No
    # single step felt like a budget decision. A guard consulted once is a guard
    # on the smallest thing you ever did.
    global _INFLIGHT
    est = mean_batch_cost()
    with _INFLIGHT_LOCK:
        ok, why = keyring.guard_ok(reserve=_INFLIGHT + est, batch_est=est)
        if ok:
            _INFLIGHT += est
    if not ok:
        print(f"  batch {idx}: ⛔ SKIPPED — {why}")
        rec = {"batch": idx, "acct": None, "n": len(batch), "ids": [],
               "cost_usd": 0.0, "wall_s": 0.0, "is_error": False,
               "num_turns": 0, "result": "", "skipped": why}
        # ⛔ A SKIP MUST LEAVE A RECORD ON DISK. Until 2026-10-20 it returned this
        # dict and wrote nothing, so a run that skipped 8 of 20 batches left 12
        # files and no trace of the other 8 -- indistinguishable from a run that
        # was only ever asked for 12.
        (outdir / f"batch-{idx:04d}.json").write_text(json.dumps(rec, indent=1))
        return rec

    acct = keyring.next_acct()
    ACCT_FOR_BATCH[idx] = acct
    assignments = "\n".join(_assignment_line(m) for m in batch)
    prompt = prompt_path.read_text().replace("{{ASSIGNMENTS}}", assignments)

    t0 = time.time()
    try:
        proc = subprocess.run(
            # ⛔ PROMPT ON STDIN — a single argv element is capped at 128 KiB
            # (MAX_ARG_STRLEN), and the exec fails before the model is reached,
            # so an oversized batch produces no output, no cost and no log line.
            # It killed 3 of 10 Slack-mining groups on 2026-10-22; this brief is
            # smaller but grows with every lesson added to it.
            NICE + [CLAUDE, "-p", "--output-format", "json",
             "--permission-mode", "bypassPermissions"]
            + (["--model", model] if model else []),
            input=prompt,
            env=keyring.key_env(acct),        # ⛔ key lives only in this env dict
            capture_output=True, text=True, timeout=timeout, cwd=str(HERE))
    finally:
        # Released on timeout and on crash too: a reservation that leaks would
        # shrink the day's budget for every later batch with money never spent.
        with _INFLIGHT_LOCK:
            _INFLIGHT -= est
    d = parse_result(proc.stdout or proc.stderr or "")
    cost = float(d.get("total_cost_usd") or 0.0)

    # Attribute spend to the account that actually paid, per model where known.
    mu = d.get("modelUsage") or {}
    if mu:
        for model, v in mu.items():
            keyring.log_spend(acct, model, float(v.get("costUSD") or 0.0))
    elif cost:
        keyring.log_spend(acct, "unknown", cost)

    # ⛔ LAND THE REPORT BEFORE ANYTHING THAT CAN RAISE. Measured 2026-10-19: a
    # KeyError while BUILDING this record discarded four finished batches --
    # $3.85 of completed audit whose findings existed only in the lost stdout.
    # The writes had already landed in the corpus, so the run printed "0/4
    # completed · $0.0000" over twelve documents it had just rewritten.
    (outdir / f"batch-{idx:04d}.raw.json").write_text(json.dumps(d, indent=1))

    # ⚠️ A consolidation candidate carries `ids` (a set); an audit candidate
    # carries `id`. Anything reading a batch must accept both shapes.
    ids = [x[:8] for m in batch for x in (m.get("ids") or [m["id"]])]

    rec = {"batch": idx, "acct": acct, "n": len(batch), "ids": ids,
           "cost_usd": cost, "wall_s": round(time.time() - t0, 1),
           "is_error": bool(d.get("is_error")), "num_turns": d.get("num_turns"),
           "result": d.get("result") or "", "parse_error": d.get("_parse")}

    # Always store the parsed report as its own field, recovering it from the
    # session transcript when the final turn did not carry it. `report: null`
    # then means "the worker never emitted one", which is a real finding;
    # before this, it was indistinguishable from "it emitted one and then said
    # something else afterwards".
    report = None
    for blob in reversed(re.findall(r"```json\s*(.*?)```", rec["result"], re.S)):
        try:
            cand = json.loads(blob)
        except ValueError:
            continue
        # ⚠ THE SHAPE IS NOT ALWAYS THE AUDIT'S. This runner now drives more
        # than one brief (--prompt), and a probe-only pass reports `probed`, not
        # `audited`. Testing for the audit's key alone made every one of those
        # batches print "NO PARSEABLE REPORT" and store report: null, while the
        # work had in fact landed -- measured 2026-10-22 on all 3 pilot batches.
        # Accept any of the known top-level report keys.
        if isinstance(cand, dict) and REPORT_KEYS.intersection(cand):
            report = cand
            break
    if report is None:
        report = _report_from_session(d.get("session_id"), HERE)
        if report is not None:
            rec["report_recovered_from"] = "session_transcript"
    rec["report"] = report
    if report is None:
        print(f"  batch {idx}: ⚠️ NO PARSEABLE REPORT — verdicts and any route "
              f"findings are unrecorded; the corpus edits may still have landed")
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
    ap.add_argument("--model", default=None,
                    help="pass through to claude -p; a probe-only pass does not "
                         "need the model a truth audit needs")
    ap.add_argument("--skip-probed", action="store_true",
                    help="drop candidates that already carry a REFUTE probe")
    ap.add_argument("--wait-for-lock", type=float, default=0.0,
                    help="seconds to wait for the grant-spender lock; scheduled "
                         "work should wait rather than lose the night")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    # ⛔ One grant spender at a time, across PROCESSES. Held for the whole run;
    # released when this process exits, however it exits.
    if not a.dry_run:
        try:
            _lock = keyring.acquire_spender_lock(f"memo-audit {a.candidates}", a.wait_for_lock)
        except RuntimeError as e:
            print(f"⛔ {e}")
            return 3

    cands = json.loads(Path(a.candidates).read_text())

    if a.skip_probed:
        # ⭐ A MEMO THAT ALREADY CARRIES A PROBE DOES NOT NEED A SECOND ONE.
        # The probe pass selects from the full ranked list, so a resumed or
        # repeated run would pay again for memos it already covered -- and
        # because the second probe usually agrees with the first, the waste is
        # invisible in the output. One scan of /documents costs nothing against
        # the spend it prevents.
        import corpus
        docs = corpus.load()          # shared cache: see corpus.py on why
        probed = {d["id"] for d in docs if (d.get("metadata") or {}).get("refute")}
        before = len(cands)
        cands = [c for c in cands if c["id"] not in probed]
        print(f"skip-probed: {before} candidates → {len(cands)} "
              f"({len(probed)} already carry a probe)")

    sel = cands[a.offset:a.offset + a.batch_size * a.batches]
    batches = [sel[i:i + a.batch_size] for i in range(0, len(sel), a.batch_size)]
    outdir = Path(a.outdir) / datetime.now().strftime("%Y%m%dT%H%M%S")

    print(f"candidates {len(cands)} · selected {len(sel)} · batches {len(batches)} "
          f"· batch-size {a.batch_size} · concurrency {a.concurrency}")
    print(f"brief: {a.prompt}")
    print(f"next account: {keyring.next_acct()} "
          f"(remaining ~${keyring.remaining(keyring.next_acct()):.2f}, {keyring.ledger_source()})")
    _ok, _why = keyring.guard_ok()
    print(("⛔ " if not _ok else "") + _why)
    if not _ok and not a.dry_run:
        print("⛔ refusing to start: the daily guard is already spent. "
              "This is the check that was missing on 2026-10-19.")
        return 2
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
        futs = {ex.submit(run_batch, b, i, outdir, a.timeout, Path(a.prompt),
                          a.model): i
                for i, b in enumerate(batches)}
        for f in as_completed(futs):
            i = futs[f]
            try:
                r = f.result()
            except subprocess.TimeoutExpired:
                # ⛔ A KILLED WORKER STILL SPENT THE MONEY. `log_spend` only runs
                # after the subprocess returns, so without this the cost of every
                # timed-out batch is invisible to the ledger and to the daily
                # guard -- measured 2026-10-19, an aborted run left a finished
                # memo in the corpus and $0.00 recorded.
                est = mean_batch_cost()
                keyring.log_unmeasured(ACCT_FOR_BATCH.get(i, "unknown"),
                                       f"timeout after {a.timeout}s", est)
                print(f"  batch {i}: ⛔ TIMEOUT after {a.timeout}s "
                      f"— INCONCLUSIVE, not a clean audit. Logged ~${est:.2f} as "
                      f"ESTIMATED spend (real cost unknown, never reported back).")
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
