"""InjectionSet contract. [001/FR-016 001/FR-017 001/FR-018 001/FR-019 001/FR-020]

Covers FR-019/FR-020 membership, the token-budget drop ORDER (the load-bearing
rule), opt-out, and the rendered additionalContext shape.
"""
import json

import pytest

from memo import db
from memo.injection import posture
from memo.injection import set as inj

NOW = 1_800_000_000.0
HOUR = 3600.0


async def seed(content, *, cls, scope=None, title=None, time_scope=None,
               embedding=None, injection_mode=None):
    doc_id = await db.store(None, content, title, [], {}, embedding)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute(
        "UPDATE documents SET class=?, scope=?, time_scope=?, injection_mode=? WHERE id=?",
        (cls, json.dumps(scope or ["global"]),
         json.dumps(time_scope) if time_scope else None,
         injection_mode or ("forcible-constitutional" if cls == "constitutional"
                            else "on-recall"),
         doc_id),
    )
    conn.commit()
    return doc_id


async def build(**kw):
    kw.setdefault("session_id", "dojo")
    kw.setdefault("current_time", NOW)
    kw.setdefault("agent_family", "dojo")
    kw.setdefault("use_cache", False)
    return await inj.build(**kw)


# --- FR-019: constitutional membership ---

@pytest.mark.asyncio
async def test_constitutional_memos_are_included(embedding):
    doc = await seed("never rest with work pending", cls="constitutional",
                     embedding=embedding)
    r = await build()
    assert doc in [m["id"] for m in r["forcible_constitutional"]]


@pytest.mark.asyncio
async def test_behavioral_injects_but_verbatim_critical_does_not(embedding):
    """`verbatim-critical` is a rule about HANDLING, not about reach.

    It means "quote this exactly if you use it" — not "everyone must read this
    at every session start". Conflating the two put 85,336 tokens (55 memos)
    into a 5,000-token budget on the partial corpus alone, and would have been
    ~400k fleet-wide on the full one.

    Its protection lives on the retrieval path: when such a memo IS returned it
    is returned whole and never summarised. A memo nobody asked for does not
    need protecting.
    """
    b = await seed("always use docker", cls="behavioral", embedding=embedding)
    v = await seed("exact wording matters here", cls="verbatim-critical",
                   embedding=embedding)
    ids = [m["id"] for m in (await build())["forcible_constitutional"]]
    assert b in ids, "behavioral rules still inject"
    assert v not in ids, "verbatim-critical must not be force-injected"


@pytest.mark.asyncio
async def test_plain_facts_are_not_force_injected(embedding):
    """Only the FR-019/FR-020 classes inject. A fact is recalled, not pushed."""
    f = await seed("the ups is a cyberpower", cls="fact", embedding=embedding)
    r = await build()
    all_ids = [m["id"] for m in r["forcible_constitutional"] + r["forcible_current_focus"]]
    assert f not in all_ids


@pytest.mark.asyncio
async def test_superseded_memos_are_not_injected(embedding):
    doc = await seed("old rule", cls="constitutional", embedding=embedding)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET valid_until = ? WHERE id = ?", (NOW - 1, doc))
    conn.commit()
    r = await build()
    assert doc not in [m["id"] for m in r["forcible_constitutional"]]


# --- FR-008 scope ---

@pytest.mark.asyncio
async def test_agent_family_scope_filters(embedding):
    mine = await seed("dojo rule", cls="behavioral", scope=["agent-family:dojo"],
                      embedding=embedding)
    theirs = await seed("quantum rule", cls="behavioral",
                        scope=["agent-family:quantum"], embedding=embedding)
    ids = [m["id"] for m in (await build())["forcible_constitutional"]]
    assert mine in ids
    assert theirs not in ids


@pytest.mark.asyncio
async def test_project_scope_filters(embedding):
    doc = await seed("memo project rule", cls="behavioral",
                     scope=["project:memo"], embedding=embedding)
    assert doc not in [m["id"] for m in (await build())["forcible_constitutional"]]
    r = await build(project="memo")
    assert doc in [m["id"] for m in r["forcible_constitutional"]]


# --- FR-020 + T055: current focus ---

@pytest.mark.asyncio
async def test_goal_memos_are_current_focus(embedding):
    g = await seed("ship the T094 seam", cls="goal", embedding=embedding)
    assert g in [m["id"] for m in (await build())["forcible_current_focus"]]


@pytest.mark.asyncio
async def test_time_scoped_memo_only_inside_its_window(embedding):
    t = await seed("Logan parking L6 R", cls="time-scoped",
                   time_scope={"start": NOW - HOUR, "end": NOW + HOUR},
                   embedding=embedding)
    inside = await build(current_time=NOW)
    assert t in [m["id"] for m in inside["forcible_current_focus"]]

    after = await build(current_time=NOW + 10 * HOUR)
    assert t not in [m["id"] for m in after["forcible_current_focus"]], \
        "an expired trip memo must stop injecting without anyone deleting it"

    before = await build(current_time=NOW - 10 * HOUR)
    assert t not in [m["id"] for m in before["forcible_current_focus"]]


@pytest.mark.asyncio
async def test_time_scoped_without_window_is_not_injected(embedding):
    t = await seed("malformed time-scoped", cls="time-scoped", embedding=embedding)
    assert t not in [m["id"] for m in (await build())["forcible_current_focus"]]


# --- Budget + DROP ORDER (the load-bearing rule) ---

@pytest.mark.asyncio
async def test_constitutional_is_never_dropped_for_budget(embedding):
    """The single most important invariant in this module.

    Dropping a standing rule because a chatty goal memo filled the budget would
    remove a guardrail exactly when context is tight.
    """
    const = await seed("CONSTITUTIONAL RULE " + ("x " * 200), cls="constitutional",
                       embedding=embedding)
    for i in range(10):
        await seed(f"goal {i} " + ("y " * 300), cls="goal", embedding=embedding)

    r = await build(budget=900)
    assert const in [m["id"] for m in r["forcible_constitutional"]]
    assert r["dropped_for_budget"], "focus items should have been dropped"
    assert all(not d.startswith("constitutional") for d in r["dropped_for_budget"])


@pytest.mark.asyncio
async def test_current_focus_dropped_before_transclusions_are_kept(embedding):
    """Drop order is transclusions-then-focus in priority, focus first to go
    when only focus is over — assert focus actually yields under pressure."""
    await seed("small rule", cls="constitutional", embedding=embedding)
    for i in range(6):
        await seed(f"goal {i} " + ("z " * 400), cls="goal", embedding=embedding)
    r = await build(budget=600)
    assert any(d.startswith("current-focus") for d in r["dropped_for_budget"])


@pytest.mark.asyncio
async def test_over_budget_constitutional_is_reported_not_truncated(embedding):
    await seed("huge rule " + ("w " * 2000), cls="constitutional", embedding=embedding)
    r = await build(budget=100)
    assert r["forcible_constitutional"], "must not truncate a standing rule"
    assert "budget-exceeded-by-constitutional" in r["dropped_for_budget"]


@pytest.mark.asyncio
async def test_budget_fields_are_reported(embedding):
    await seed("rule", cls="constitutional", embedding=embedding)
    r = await build()
    assert r["token_budget_ceiling"] == inj.DEFAULT_BUDGET
    assert r["token_budget_used"] > 0


# --- Opt out ---

@pytest.mark.asyncio
async def test_opt_out_short_circuits(embedding, monkeypatch):
    await seed("rule", cls="constitutional", embedding=embedding)
    monkeypatch.setattr(posture, "read_environ",
                        lambda pid: {posture.DISABLE_INJECTION: "1"})
    r = await build(pid=1234)
    assert r["opt_out"] is True
    assert "forcible_constitutional" not in r
    assert inj.render(r) == "", "opted-out sessions get no additionalContext"


@pytest.mark.asyncio
async def test_memory_posture_reported(embedding, monkeypatch):
    await seed("rule", cls="constitutional", embedding=embedding)
    monkeypatch.setattr(posture, "read_environ",
                        lambda pid: {posture.DISABLE_AUTO_MEMORY: "1"})
    r = await build(pid=1234)
    assert r["memory_posture"] == "off"
    assert r["forcible_constitutional"], "posture off means Layer 2 IS the memory"


# --- Rendering ---

@pytest.mark.asyncio
async def test_render_shape(embedding):
    await seed("never rest with work pending", cls="constitutional",
               title="Anti-rest", embedding=embedding)
    await seed("ship T094", cls="goal", embedding=embedding)
    text = inj.render(await build())
    assert "[MEMO Layer 2 injection" in text
    assert "## Forcible constitutional" in text
    assert "## Current focus" in text
    assert "Memory posture: on" in text
    assert "Injection budget:" in text


# --- Caching ---

@pytest.mark.asyncio
async def test_cache_returns_a_hit_within_the_window(embedding):
    await seed("rule", cls="constitutional", embedding=embedding)
    first = await inj.build(session_id="dojo", agent_family="dojo",
                            current_time=NOW, use_cache=True)
    second = await inj.build(session_id="dojo", agent_family="dojo",
                             current_time=NOW, use_cache=True)
    assert first.get("cached") is not True
    assert second.get("cached") is True


def test_injection_reasserts_that_memo_is_reachable():
    """Skill descriptions do not survive /compact (C59), so the injection set
    must restore the REFLEX to reach for memo.

    Explicit `/recall` still works post-compaction; what is lost is automatic
    firing, because the session no longer holds the trigger phrasing. Sessions
    with 11 and 24 compactions made zero memo calls of any kind — this block is
    the fix, and it rides a path that already fires at every session start and
    every compaction.
    """
    from memo.injection import set as injection_set

    rendered = injection_set.render({
        "forcible_constitutional": [],
        "forcible_current_focus": [],
        "transclusions": [],
        "memory_posture": "on",
        "token_budget_used": 0,
    })
    assert "Reaching for memo" in rendered
    assert "/recall" in rendered
    assert "/memorize" in rendered
    # The stand-down condition matters as much as the triggers: a high-recall
    # prompt with no "when not to" turns into noise.
    assert "ephemeral" in rendered


def test_opted_out_sessions_get_no_reachability_block_either():
    """opt_out must stay total — an opted-out session gets NOTHING, including
    this. A block that leaked past opt-out would make the flag a lie."""
    from memo.injection import set as injection_set
    assert injection_set.render({"opt_out": True}) == ""


@pytest.mark.asyncio
async def test_injection_stays_within_budget_on_a_corpus_of_many_injectables(embedding):
    """The assertion whose absence let a 90k-token blowout ship.

    Every existing budget test uses two or three small fixtures, where the
    ceiling cannot plausibly be hit. The real corpus had 55 force-injected memos
    averaging ~1,550 tokens, and NOTHING checked the total — the overrun was
    found by eye, reading a number in a response during unrelated work.

    This seeds enough injectable content to exceed the ceiling several times
    over and asserts the set is actually bounded. It fails against the
    pre-2026-07-30 code.
    """
    body = " ".join(["padding"] * 900)          # ~900 tokens each
    for i in range(12):
        await seed(f"focus item {i}: {body}", cls="goal", embedding=embedding)

    result = await build()
    used = result["token_budget_used"]
    ceiling = result["token_budget_ceiling"]

    assert used <= ceiling, (
        f"injection set is {used} tokens against a {ceiling} ceiling — this "
        "fires at every session start and every compaction, fleet-wide")
    assert result["dropped_for_budget"], "something must be recorded as dropped"


@pytest.mark.asyncio
async def test_going_over_budget_is_only_ever_constitutional(embedding):
    """The ONE legitimate overrun, and it must be explicit.

    Constitutional content is never truncated or dropped — silently removing a
    standing rule because a chatty goal memo filled the budget would remove a
    guardrail exactly when context is tight. So an overrun is allowed, but only
    from constitutional content, and it must announce itself.
    """
    body = " ".join(["rule"] * 6000)
    await seed(f"a very long standing rule: {body}", cls="constitutional",
               embedding=embedding)

    result = await build()
    assert result["token_budget_used"] > result["token_budget_ceiling"]
    assert "budget-exceeded-by-constitutional" in result["dropped_for_budget"], \
        "an overrun must name its cause, not happen quietly"
