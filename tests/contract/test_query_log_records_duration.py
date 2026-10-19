"""Every query_log write must record how long the operation took. [2026-10-19]

⛔ THE BUG THIS PINS. `memo_update` and `memo_context` called
`db.log_query_async` without `latency_ms`, so they wrote rows with a NULL
duration while `memo_store`, `memo_search` and the two REST paths all recorded
one. Measured on the live corpus 2026-10-19: 0 of 3,189 `update` rows and 0 of
238 `context` rows carried a latency, against 16,114 of 16,114 `store` and
3,318 of 3,318 `search`. The cause was simply that neither function set `_t0`.

⚠️ WHY IT MATTERED, AND WHY A UNIT TEST OF ONE FUNCTION WOULD NOT HAVE CAUGHT
IT. The missing field is the only evidence that can diagnose `5d42b0ea` — a
write that completed server-side on 2026-09-17 while the caller got a 300s
abort. The query_log row proves the handler REACHED the logging call, and the
row is written BEFORE the response is built, so without a duration the row
cannot distinguish "handler finished fast, response lost in transport" from
"handler ran ~300s, caller aborted, handler completed afterwards". Those want
opposite fixes.

⭐ THIS IS A SOURCE-LEVEL TEST ON PURPOSE, and that is the point rather than a
shortcut. Two reasons:
  1. `db.log_query` IGNORES db_path (single-global since 2026-06-29), so driving
     `memo_update` end-to-end to inspect the row it writes would write to the
     REAL fleet corpus — the trap already documented in
     tests/test_log_query_rollback.py.
  2. A per-function test only guards the functions someone thought to cover. The
     defect here was an OMISSION at a call site, so the invariant worth pinning
     is over ALL call sites: a new op added next year without timing fails this
     test on the day it is written, with no new test required.
"""
from __future__ import annotations

import ast
import pathlib

MAIN = pathlib.Path(__file__).resolve().parents[2] / "src" / "memo" / "main.py"


def _log_query_calls(tree: ast.AST) -> list[ast.Call]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if name in {"log_query_async", "log_query"}:
            out.append(node)
    return out


def test_main_has_the_log_query_call_sites_we_expect():
    """⭐ NEGATIVE CONTROL. If the walk found nothing — a rename, a refactor, a
    bad parse — the real assertion below would pass vacuously and this file
    would silently stop testing anything. That exact failure happened to
    tests/unit/test_oversized_document_refusal.py earlier today, so it is worth
    one line to make it impossible here."""
    tree = ast.parse(MAIN.read_text())
    calls = _log_query_calls(tree)
    assert len(calls) >= 5, (
        f"expected at least 5 log_query call sites in {MAIN.name}, found "
        f"{len(calls)} — did the call get renamed? This test is now vacuous."
    )


def test_every_query_log_write_records_a_latency():
    tree = ast.parse(MAIN.read_text())
    missing = []
    for call in _log_query_calls(tree):
        kw = {k.arg for k in call.keywords if k.arg}
        if "latency_ms" not in kw:
            # The op is the second positional arg: log_query_async(db_path, "update", ...)
            op = "?"
            if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
                op = str(call.args[1].value)
            missing.append(f"line {call.lineno} (op={op!r})")
    assert not missing, (
        "query_log writes with no latency_ms — a row with a NULL duration "
        "cannot diagnose a slow-vs-lost response: " + ", ".join(missing)
    )
