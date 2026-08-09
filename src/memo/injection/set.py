"""InjectionSet builder — Layer 2 gap-fill. [001/FR-016 001/FR-017 001/FR-018 001/FR-019 001/FR-020]

Assembles what a session must be told at SessionStart / PostCompact, per
contracts/injection-set.md:

    spec-kit constitution + forcible-constitutional + current-focus
      + transclusions, all inside a 5k token ceiling (C-02)

The membership rules are FR-019/FR-020 and are not heuristics — a
`class=constitutional` memo is injected because it is constitutional, not
because it scored well. Nothing here ranks by relevance.

**Drop order under budget pressure is the load-bearing decision.** When the set
exceeds the ceiling we drop transclusions first, then current-focus, and
**never constitutional**. Constitutional memos are the operator's standing rules
— silently dropping one because a chatty goal memo filled the budget would
remove a guardrail precisely when context is tight. If constitutional content
alone exceeds the ceiling we go over budget and say so, rather than truncate it.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from time import time

from memo import db
from memo.injection import guides, posture, transclude

logger = logging.getLogger(__name__)

DEFAULT_BUDGET = 5000          # C-02
CACHE_TTL_S = 300              # 5 min per contract

# Where a spec-kit constitution lives, relative to a project root.
CONSTITUTION_REL = Path(".specify") / "memory" / "constitution.md"
# How far up the tree to walk looking for it. Bounded so a session started in a
# deep path cannot walk to / and read something unrelated.
CONSTITUTION_MAX_WALK = 6


def find_spec_kit_constitution(start: str | None) -> tuple[str | None, str | None]:
    """Walk up from `start` for `.specify/memory/constitution.md`.

    Returns `(path, content)` or `(None, None)`. Claude Code does NOT auto-load
    this file (Agent G's finding), which is exactly why memo injects it.
    """
    if not start:
        return (None, None)
    try:
        cur = Path(start).resolve()
    except (OSError, RuntimeError):
        return (None, None)
    for _ in range(CONSTITUTION_MAX_WALK):
        candidate = cur / CONSTITUTION_REL
        if candidate.is_file():
            try:
                return (str(candidate), candidate.read_text(errors="replace"))
            except OSError as e:
                logger.warning("injection: cannot read %s: %s", candidate, e)
                return (None, None)
        if cur.parent == cur:
            break
        cur = cur.parent
    return (None, None)


def _scope_matches(memo_scope: list[str], *, agent_family: str | None,
                   project: str | None, session_id: str | None) -> bool:
    """Does this memo's scope apply to this session? (FR-008 semantics)"""
    if not memo_scope:
        return True                      # unscoped == global
    for s in memo_scope:
        if s == "global":
            return True
        if agent_family and s == f"agent-family:{agent_family}":
            return True
        if project and s == f"project:{project}":
            return True
        if session_id and s == f"session:{session_id}":
            return True
    return False


def _sync_fetch_injectable(db_path: str) -> list[dict]:
    """Current memos in the classes that can ever be forcibly injected.

    Filtered in SQL by class + `valid_until IS NULL` so the scope/time work
    below runs over a small set. This is on the session-start path, so it must
    not scan the corpus.
    """
    conn = db._get_or_create_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM documents WHERE valid_until IS NULL AND class IN "
        "('constitutional','behavioral','goal','time-scoped') "
        "ORDER BY created_at DESC"
    ).fetchall()
    return [db._row_to_memo(r) for r in rows]


def _sync_fetch_flush(db_path: str, session_id: str, generation: int) -> list[dict]:
    """Previous generation's ephemeral-flush memos, for post-compact continuity."""
    conn = db._get_or_create_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM documents WHERE valid_until IS NULL AND class = 'ephemeral-flush' "
        "AND json_extract(metadata, '$.session_id') = ? "
        "AND json_extract(metadata, '$.flush_generation') = ? ORDER BY created_at",
        (session_id, generation),
    ).fetchall()
    return [db._row_to_memo(r) for r in rows]


def _brief(memo: dict) -> dict:
    """Trim a row to what the hook actually renders."""
    return {"id": memo["id"], "title": memo.get("title"),
            "content": memo.get("content") or "", "class": memo.get("class")}


def _cost(items: list[dict]) -> int:
    return sum(db._count_tokens(i.get("content") or "") for i in items)


def _sync_cache_get(db_path: str, key: str) -> dict | None:
    conn = db._get_or_create_conn(db_path)
    row = conn.execute(
        "SELECT injection_set, expires_at FROM injection_set_cache WHERE cache_key = ?",
        (key,),
    ).fetchone()
    if row is None or (row["expires_at"] or 0) < time():
        return None
    try:
        return json.loads(row["injection_set"])
    except ValueError:
        return None


def _sync_cache_put(db_path: str, key: str, payload: dict) -> None:
    conn = db._get_or_create_conn(db_path)
    now = time()
    conn.execute(
        "INSERT INTO injection_set_cache (cache_key, injection_set, computed_at, expires_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET "
        "injection_set=excluded.injection_set, computed_at=excluded.computed_at, "
        "expires_at=excluded.expires_at",
        (key, json.dumps(payload), now, now + CACHE_TTL_S),
    )
    conn.commit()


async def build(*, session_id: str, agent_family: str | None = None,
                project: str | None = None, pid: int | None = None,
                current_time: float | None = None,
                flush_generation: int | None = None,
                cwd: str | None = None,
                instruction_text: str | None = None,
                budget: int = DEFAULT_BUDGET,
                use_cache: bool = True) -> dict:
    """Compute the injection set for one session. [001/FR-016]"""
    now = current_time if current_time is not None else time()
    env = posture.read_environ(pid)

    # Explicit opt-out short-circuits everything, including the cache.
    if posture.injection_opted_out(env=env):
        return {"session_id": session_id, "opt_out": True,
                "reason": f"{posture.DISABLE_INJECTION}=1",
                "computed_at": now}

    if agent_family is None:
        agent_family, _guide, _conv = await guides.resolve_agent_family(session_id)

    # 5-minute bucket in the key: the contract wants caching without pinning a
    # stale set across a window where time-scoped memos may activate or expire.
    cache_key = (f"{session_id}|{agent_family}|{project}|{int(now // CACHE_TTL_S)}"
                 f"|{posture.memory_posture(env=env)}|{flush_generation}")
    if use_cache and instruction_text is None:
        try:
            hit = await asyncio.to_thread(_sync_cache_get, db.global_path(), cache_key)
            if hit is not None:
                hit["cached"] = True
                return hit
        except Exception:
            logger.exception("injection: cache read failed — recomputing")

    rows = await asyncio.to_thread(_sync_fetch_injectable, db.global_path())
    in_scope = [
        m for m in rows
        if _scope_matches(m.get("scope") or [], agent_family=agent_family,
                          project=project, session_id=session_id)
    ]

    # FR-019 — constitutional set.
    #
    # `verbatim-critical` is deliberately NOT here. [audit 2026-07-30]
    #
    # The class means "if you use this, quote it exactly — do not paraphrase".
    # That is a rule about HANDLING, not a claim that everyone must read it at
    # every session start. Force-injecting it conflated the two and put 85,336
    # tokens (55 memos) into a 5,000-token budget on the partial corpus alone.
    #
    # Its protection lives on the retrieval path instead: when a
    # verbatim-critical memo IS returned, it is returned whole and never
    # summarised. A memo nobody asked for does not need protecting.
    constitutional = [_brief(m) for m in in_scope
                      if m.get("class") in ("constitutional", "behavioral")]

    # FR-020 — current focus. time-scoped memos join only inside their window
    # (T055), which is what makes an expired trip memo stop being injected
    # without anyone deleting it.
    focus: list[dict] = []
    for m in in_scope:
        cls = m.get("class")
        if cls == "goal":
            focus.append(_brief(m))
        elif cls == "time-scoped":
            ts = m.get("time_scope") or {}
            start, end = ts.get("start"), ts.get("end")
            if start is not None and end is not None and start <= now <= end:
                focus.append(_brief(m))

    # Previous generation's flush, for post-compact continuity.
    if flush_generation is not None:
        try:
            prev = await asyncio.to_thread(_sync_fetch_flush, db.global_path(),
                                           session_id, flush_generation)
            focus.extend(_brief(m) for m in prev)
        except Exception:
            logger.exception("injection: flush-generation fetch failed — continuing")

    transclusions: list[dict] = []
    if instruction_text:
        transclusions = await transclude.resolve(
            instruction_text, source_file=cwd or "instructions")

    const_path, const_content = find_spec_kit_constitution(cwd)

    # --- Budget enforcement, in drop-priority order ---
    dropped: list[str] = []
    const_cost = db._count_tokens(const_content) if const_content else 0
    used = const_cost + _cost(constitutional)

    if used > budget:
        # Constitutional alone blows the ceiling. Report it and go over rather
        # than truncate a standing rule into something that reads as complete.
        logger.warning(
            "injection: constitutional content (%d tokens) exceeds the %d budget "
            "for session %s — over-budget rather than dropping a standing rule",
            used, budget, session_id,
        )
        dropped.append("budget-exceeded-by-constitutional")
        # Record what is being dropped BEFORE clearing — testing the lists
        # afterwards would always see them empty and report nothing dropped.
        dropped.extend(f"current-focus:{i['id']}" for i in focus)
        dropped.extend(f"transclusion:{t['referenced_uuid']}" for t in transclusions)
        focus, transclusions = [], []
    else:
        kept_focus: list[dict] = []
        for item in focus:
            c = db._count_tokens(item.get("content") or "")
            if used + c > budget:
                dropped.append(f"current-focus:{item['id']}")
                continue
            kept_focus.append(item)
            used += c
        focus = kept_focus

        kept_trans: list[dict] = []
        for t in transclusions:
            c = db._count_tokens(t.get("resolved_content") or "")
            if used + c > budget:
                dropped.append(f"transclusion:{t['referenced_uuid']}")
                continue
            kept_trans.append(t)
            used += c
        transclusions = kept_trans

    payload = {
        "session_id": session_id,
        "agent_family": agent_family,
        "project": project,
        "memory_posture": posture.memory_posture(env=env),
        "spec_kit_constitution_path": const_path,
        "spec_kit_constitution_content": const_content,
        "forcible_constitutional": constitutional,
        "forcible_current_focus": focus,
        "transclusions": transclusions,
        "dropped_for_budget": dropped,
        "token_budget_used": used,
        "token_budget_ceiling": budget,
        "computed_at": now,
    }

    if use_cache and instruction_text is None:
        try:
            await asyncio.to_thread(_sync_cache_put, db.global_path(), cache_key, payload)
        except Exception:
            logger.exception("injection: cache write failed — non-fatal")
    return payload


def render(payload: dict) -> str:
    """Serialize an injection set into the hook's `additionalContext` string.

    Format is fixed by contracts/injection-set.md. Returns "" for an opted-out
    set so the hook emits no additionalContext at all.
    """
    if payload.get("opt_out"):
        return ""

    parts = ["[MEMO Layer 2 injection — this is your persistent memory]"]

    if payload.get("spec_kit_constitution_content"):
        parts.append("\n## Constitution (spec-kit)\n"
                     + payload["spec_kit_constitution_content"].rstrip())

    if payload.get("forcible_constitutional"):
        parts.append("\n## Forcible constitutional")
        for m in payload["forcible_constitutional"]:
            title = f"{m['title']}: " if m.get("title") else ""
            parts.append(f"- [{m['id'][:8]}] {title}{m['content'].strip()}")

    if payload.get("forcible_current_focus"):
        parts.append("\n## Current focus")
        for m in payload["forcible_current_focus"]:
            title = f"{m['title']}: " if m.get("title") else ""
            parts.append(f"- {title}{m['content'].strip()}")

    if payload.get("transclusions"):
        parts.append("\n## Transclusions")
        for t in payload["transclusions"]:
            parts.append(f"[{t['referenced_uuid'][:8]} from {t['source_file']}] "
                         f"{t['resolved_content'].strip()}")

    # Re-assert that memo is REACHABLE and when to reach for it. [001/FR-018]
    #
    # Skill descriptions carry `noSurviveCompact: true` — they are NOT
    # re-injected after /compact (spec constraint C59). Explicit `/recall` still
    # works post-compaction, but AUTOMATIC firing does not: the session no longer
    # holds the trigger phrasing that would make it reach for memo unprompted.
    # That is the mechanism behind sessions with 11 and 24 compactions making
    # zero memo calls of any kind — it was never a discipline problem.
    #
    # This block costs ~70 tokens and rides a path that already fires at every
    # session start and every compaction, so it restores the reflex without
    # depending on skill descriptions surviving.
    parts.append(
        "\n## Reaching for memo\n"
        "memo is available now and holds facts you will otherwise re-derive or "
        "ask about. Reach for it WITHOUT being told when you need: an IP, "
        "hostname or port · where something lives · a credential's location · "
        "a past decision or its reasoning · a contact · a convention or "
        "standing rule · anything you are about to guess at.\n"
        "`/recall <topic>` for specific facts · `/recall-context <topic>` to "
        "load background · `/memorize <content> #tags` when a durable fact, "
        "decision or convention comes up.\n"
        "Do NOT reach for it for ephemeral or current state — that is a live "
        "probe, not memory.")

    posture_note = payload.get("memory_posture", "on")
    suffix = ("memo augments; native MEMORY.md also loads" if posture_note == "on"
              else "native auto-memory is OFF — this injection IS your memory")
    parts.append(f"\nMemory posture: {posture_note} ({suffix})")
    parts.append(f"Injection budget: {payload.get('token_budget_used', 0)} / "
                 f"{payload.get('token_budget_ceiling', DEFAULT_BUDGET)} tokens")
    if payload.get("dropped_for_budget"):
        parts.append(f"Dropped for budget: {len(payload['dropped_for_budget'])} item(s)")
    return "\n".join(parts)
