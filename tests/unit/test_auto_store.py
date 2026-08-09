"""auto_store LLM routing + degrade behavior. [001/FR-015a]

R-17: auto_store was memo's one pre-existing generative caller (OpenRouter
gpt-4o-mini) and now goes through the shared LLMProvider. These tests pin the
two things that switch introduced — lenient JSON parsing of a prose reply, and
the fail direction when the session is unavailable.
"""
import pytest

from memo import auto_store


class StubLLM:
    def __init__(self, reply=None):
        self.name = "stub"
        self.reply = reply
        self.calls = 0

    async def complete(self, prompt, *, budget_tokens=512, timeout_s=10.0):
        self.calls += 1
        return self.reply

    async def available(self):
        return self.reply is not None


# --- JSON extraction from a prose reply ---

def test_parses_bare_json():
    assert auto_store._parse_json('{"should_store": true}') == {"should_store": True}


def test_parses_fenced_json():
    """A session tends to fence code; the old OpenAI path could demand raw JSON."""
    text = 'Here you go:\n```json\n{"should_store": false, "reason": "chitchat"}\n```'
    assert auto_store._parse_json(text)["reason"] == "chitchat"


def test_parses_unlabelled_fence():
    assert auto_store._parse_json('```\n{"action": "merge"}\n```')["action"] == "merge"


def test_parses_json_with_surrounding_prose():
    text = 'I think this is worth storing. {"should_store": true} Hope that helps!'
    assert auto_store._parse_json(text) == {"should_store": True}


def test_unparseable_returns_none():
    assert auto_store._parse_json("no json at all here") is None
    assert auto_store._parse_json("") is None


def test_non_object_json_returns_none():
    """A bare list/scalar isn't a decision payload."""
    assert auto_store._parse_json('[1, 2, 3]') is None


# --- analyze_for_store: fails CLOSED ---

@pytest.mark.asyncio
async def test_store_analysis_passes_through():
    llm = StubLLM('{"should_store": true, "title": "T", "content": "c", "tags": []}')
    result = await auto_store.analyze_for_store("some exchange", provider=llm)
    assert result["should_store"] is True
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_store_analysis_errors_when_llm_unavailable():
    """Stores NOTHING, and says so as an ERROR rather than as a judgement.

    ⚠️ This test asserted `should_store is False` until the 001-branch merge.
    That was the fail-closed *action* — which is correct and unchanged — wearing
    the label of a decision nobody made. `/auto-store` renders it as
    `action="skipped"`, and a caller banking state at the end of a turn reads
    that as "nothing was worth keeping" and compacts. [v0.3.7]
    """
    result = await auto_store.analyze_for_store("x", provider=StubLLM(None))
    assert "error" in result
    assert result.get("should_store") is not False, (
        "must not also carry a should_store verdict — a caller checking that "
        "key first would never reach the error")
    assert result["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_store_analysis_errors_on_unparseable_reply():
    """An unparseable reply is a failure to hear, not a decision to skip."""
    result = await auto_store.analyze_for_store("x", provider=StubLLM("sorry, what?"))
    assert "error" in result
    assert result.get("should_store") is not False


# --- analyze_for_merge: fails toward CREATE ---

@pytest.mark.asyncio
async def test_merge_analysis_passes_through():
    llm = StubLLM('{"action": "merge", "merged_content": "combined"}')
    result = await auto_store.analyze_for_merge("old", "new", provider=llm)
    assert result["action"] == "merge"


@pytest.mark.asyncio
async def test_merge_analysis_errors_when_llm_unavailable():
    """⛔ The create-bias applies to what the model SAYS, not to whether we heard it.

    Degrading to `create` on a provider failure is the QUIETER half of the v0.3.7
    bug: it doesn't lose a memo, it duplicates one — and a duplicate is much
    harder to notice than an absence. The caller asked "merge or not?"; a failure
    is not an answer.
    """
    result = await auto_store.analyze_for_merge("old", "new", provider=StubLLM(None))
    assert "error" in result
    assert result.get("action") != "create"


@pytest.mark.asyncio
async def test_merge_analysis_errors_on_unparseable_reply():
    result = await auto_store.analyze_for_merge("old", "new", provider=StubLLM("hmm"))
    assert "error" in result
    assert result.get("action") != "create"


@pytest.mark.asyncio
async def test_merge_still_creates_when_the_MODEL_says_create():
    """The genuine create-bias survives — it just has to come from an answer.

    Kept as the positive control for the two tests above: without it, "never
    return create" would pass by never returning create at all.
    """
    llm = StubLLM('{"action": "create", "reason": "distinct enough"}')
    result = await auto_store.analyze_for_merge("old", "new", provider=llm)
    assert result["action"] == "create"
    assert "error" not in result


# --- R-17 structural guarantee ---

def test_no_direct_inference_client_remains():
    """auto_store must hold no generative API client of its own.

    R-17 allows exactly ONE generative path. A direct client here would be a
    second one, and it is the exact thing that was removed.
    """
    src = open(auto_store.__file__).read()
    assert "AsyncOpenAI" not in src
    assert "chat.completions" not in src
    assert "auto_store_model" not in src


def test_auto_store_model_setting_is_gone():
    """Removed rather than left unused, so nobody re-wires a model call to it."""
    from memo.config import settings
    assert not hasattr(settings, "auto_store_model")
