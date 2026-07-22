"""Tests for `_reject_leaked_tool_call` — the malformed-write guard.

Bar (per alpaca + mind 2026-07-21): pass-cases must outnumber refuse-cases.
The dangerous failure mode is OVER-blocking on a shared store.

Historical context: on 2026-07-21 a repair sweep with a looser predicate over-caught
alpaca's memo `3fce1547` (which discusses the corruption as subject matter) and
trimmed 692 tokens of legitimate content. Test case DISCUSSION_MID_BODY and
DISCUSSION_WITH_TAGS pin that specific failure so it can't happen again.
"""
import pytest

from memo.main import _reject_leaked_tool_call


# --- Expected-to-PASS controls (majority of the suite) ---

def test_normal_memo_with_tags():
    _reject_leaked_tool_call("Just a normal memo body.", ["foo"])


def test_normal_memo_with_no_tags_is_common():
    """Untagged writes are a valid, common pattern. MUST NOT block."""
    _reject_leaked_tool_call("Just a normal memo body.", None)


def test_normal_memo_with_empty_list_tags():
    _reject_leaked_tool_call("Just a normal memo body.", [])


def test_empty_content():
    _reject_leaked_tool_call("", None)


def test_none_content():
    _reject_leaked_tool_call(None, None)


def test_marker_in_body_but_not_tail():
    """A memo that mentions `</content>` mid-body with clean tail must pass."""
    body = "The bug was that </content>" + "x" * 500 + " and here is the actual conclusion."
    _reject_leaked_tool_call(body, None)


def test_marker_only_no_fragment():
    """`</content>` alone in tail — no fragment — must pass. The bleed requires both."""
    body = "Text with </content> in it."
    _reject_leaked_tool_call(body, None)


def test_fragment_only_no_marker():
    """`<parameter name=` alone in tail — no marker — must pass."""
    body = "Discussion of <parameter name='foo'/> in XML."
    _reject_leaked_tool_call(body, None)


def test_discussion_of_bug_mid_body_no_tags():
    """A memo about the corruption that quotes the literals mid-body must pass.

    This is the case the 2026-07-21 sweep got wrong on alpaca's `3fce1547`.
    """
    body = (
        "Root cause analysis: the corruption signature is </content><parameter name='tags'>[...] "
        "in the body with empty tags. Rest of the memo continues with actual conclusions and "
        "shouldn't be trimmed. The last 400 chars are entirely clean prose ending here."
        + " Extra tail content here to push the fingerprint out of the last 400 chars."
        + " Yet more content ensuring no marker or fragment in the tail window at all. "
        + "Final sentence."
    )
    # Verify preconditions of this test
    assert "</content>" not in body[-400:], "test setup: marker leaked into tail"
    _reject_leaked_tool_call(body, None)


def test_discussion_of_bug_with_tags_present():
    """Even with markers in the tail, tags-present short-circuits the guard. Load-bearing."""
    body = (
        "Full mid-tail quote of the bug: </content><parameter name='tags'>[list...]"
    )
    _reject_leaked_tool_call(body, ["bug", "postmortem"])


def test_variant_tags_fragment_with_tags_present():
    """The `<tags>[...]</tags></invoke>` variant with tags supplied — short-circuit."""
    body = "Discussion </content><tags>[\"a\"]</tags></invoke> preserved."
    _reject_leaked_tool_call(body, ["variant-doc"])


def test_variant_invoke_fragment_with_tags_present():
    body = "</content></invoke>"
    _reject_leaked_tool_call(body, ["invoke-doc"])


# --- Expected-to-REJECT (the actual bug) ---

def test_reject_full_fingerprint_none_tags():
    """The original mind-observed pattern: `</content><parameter name=` in tail, tags None."""
    body = "The actual memo content here " * 20  # padding to ensure tail is > 400 chars
    body += "</content><parameter name='tags'>['a','b']"
    with pytest.raises(ValueError, match="refusing a malformed write"):
        _reject_leaked_tool_call(body, None)


def test_reject_full_fingerprint_empty_list_tags():
    """Same pattern with tags=[] — must still reject."""
    body = "Content " * 50 + "</content><parameter name='tags'>['x']"
    with pytest.raises(ValueError, match="refusing a malformed write"):
        _reject_leaked_tool_call(body, [])


def test_reject_variant_tags_fragment():
    """The `<tags>[...]</tags></invoke>` variant caught in 2026-07-21 byte-diff (2d807b9e)."""
    body = "Content " * 50 + "</content><tags>[\"a\"]</tags></invoke>"
    with pytest.raises(ValueError, match="refusing a malformed write"):
        _reject_leaked_tool_call(body, None)


def test_reject_variant_bare_invoke():
    """A more minimal variant — just `</content>` + `</invoke>` in tail, empty tags."""
    body = "Content " * 50 + "</content>\n</invoke>"
    with pytest.raises(ValueError, match="refusing a malformed write"):
        _reject_leaked_tool_call(body, None)
