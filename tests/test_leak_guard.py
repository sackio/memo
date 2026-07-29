"""Tests for `_reject_leaked_tool_call` — the malformed-write guard.

Bar (per alpaca + mind 2026-07-21): pass-cases must outnumber refuse-cases.
The dangerous failure mode is OVER-blocking on a shared store.

Historical context: on 2026-07-21 a repair sweep with a looser predicate over-caught
alpaca's memo `3fce1547` (which discusses the corruption as subject matter) and
trimmed 692 tokens of legitimate content. Test cases DISCUSSION_MID_BODY and
DISCUSSION_WITH_TAGS pin that specific failure so it can't happen again.
"""
import pytest

from memo.main import _reject_leaked_tool_call


# --- Expected-to-PASS controls (majority of the suite) ---

@pytest.mark.asyncio
async def test_normal_memo_with_tags():
    await _reject_leaked_tool_call("Just a normal memo body.", ["foo"], "test")


@pytest.mark.asyncio
async def test_normal_memo_with_no_tags_is_common():
    await _reject_leaked_tool_call("Just a normal memo body.", None, "test")


@pytest.mark.asyncio
async def test_normal_memo_with_empty_list_tags():
    await _reject_leaked_tool_call("Just a normal memo body.", [], "test")


@pytest.mark.asyncio
async def test_empty_content():
    await _reject_leaked_tool_call("", None, "test")


@pytest.mark.asyncio
async def test_none_content():
    await _reject_leaked_tool_call(None, None, "test")


@pytest.mark.asyncio
async def test_marker_in_body_but_not_tail():
    body = "The bug was that </content>" + "x" * 500 + " and here is the actual conclusion."
    await _reject_leaked_tool_call(body, None, "test")


@pytest.mark.asyncio
async def test_marker_only_no_fragment():
    body = "Text with </content> in it."
    await _reject_leaked_tool_call(body, None, "test")


@pytest.mark.asyncio
async def test_fragment_only_no_marker():
    body = "Discussion of <parameter name='foo'/> in XML."
    await _reject_leaked_tool_call(body, None, "test")


@pytest.mark.asyncio
async def test_discussion_of_bug_mid_body_no_tags():
    """A memo about the corruption that quotes the literals mid-body must pass.

    The 2026-07-21 sweep got this wrong on alpaca's 3fce1547.
    """
    # The guard only inspects content[-400:], so this fixture must be long
    # enough that the marker falls OUTSIDE that window. The marker ends at
    # index ~59, so the body needs to exceed 459 chars; it was 416, which put
    # the marker back inside the tail and tripped the setup assertion below.
    # (Latent since the suite could not run at all until 2026-07-29 — no pytest
    # in the container, and importing main.py fails on the 3.10 host.) Padding
    # is generous so incidental edits don't silently re-break the arithmetic.
    body = (
        "Root cause analysis: the corruption signature is </content><parameter name='tags'>[...] "
        "in the body with empty tags. Rest of the memo continues with actual conclusions and "
        "shouldn't be trimmed. The last 400 chars are entirely clean prose ending here."
        + " Extra tail content here to push the fingerprint out of the last 400 chars."
        + " Yet more content ensuring no marker or fragment in the tail window at all. "
        + " The remediation was to scope the predicate to the tail window instead of"
        + " scanning the whole body, which is why a mid-body quotation like the one"
        + " above is legitimate prose and must be stored untouched."
        + "Final sentence."
    )
    assert "</content>" not in body[-400:], "test setup: marker leaked into tail"
    await _reject_leaked_tool_call(body, None, "test")


@pytest.mark.asyncio
async def test_discussion_of_bug_with_tags_present():
    """Even with markers in the tail, tags-present short-circuits the guard. Load-bearing."""
    body = "Full mid-tail quote of the bug: </content><parameter name='tags'>[list...]"
    await _reject_leaked_tool_call(body, ["bug", "postmortem"], "test")


@pytest.mark.asyncio
async def test_variant_tags_fragment_with_tags_present():
    body = 'Discussion </content><tags>["a"]</tags></invoke> preserved.'
    await _reject_leaked_tool_call(body, ["variant-doc"], "test")


@pytest.mark.asyncio
async def test_variant_invoke_fragment_with_tags_present():
    body = "</content></invoke>"
    await _reject_leaked_tool_call(body, ["invoke-doc"], "test")


# --- Expected-to-REJECT (the actual bug) ---

@pytest.mark.asyncio
async def test_reject_full_fingerprint_none_tags():
    body = "The actual memo content here " * 20
    body += "</content><parameter name='tags'>['a','b']"
    with pytest.raises(ValueError, match="refusing a malformed write"):
        await _reject_leaked_tool_call(body, None, "test")


@pytest.mark.asyncio
async def test_reject_full_fingerprint_empty_list_tags():
    body = "Content " * 50 + "</content><parameter name='tags'>['x']"
    with pytest.raises(ValueError, match="refusing a malformed write"):
        await _reject_leaked_tool_call(body, [], "test")


@pytest.mark.asyncio
async def test_reject_variant_tags_fragment():
    """The `<tags>[...]</tags></invoke>` variant caught in 2026-07-21 byte-diff (2d807b9e)."""
    body = "Content " * 50 + '</content><tags>["a"]</tags></invoke>'
    with pytest.raises(ValueError, match="refusing a malformed write"):
        await _reject_leaked_tool_call(body, None, "test")


@pytest.mark.asyncio
async def test_reject_variant_bare_invoke():
    body = "Content " * 50 + "</content>\n</invoke>"
    with pytest.raises(ValueError, match="refusing a malformed write"):
        await _reject_leaked_tool_call(body, None, "test")
