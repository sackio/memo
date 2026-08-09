"""LLM-based extraction and dedup for auto-storing memos from conversation exchanges.

**R-17**: these two calls used to go straight to OpenRouter
(`openai/gpt-4o-mini`) and were memo's only pre-existing generative caller. They
now go through the shared `LLMProvider`, i.e. an interactive Claude Code session
riding the Max subscription — never a per-token API, and never `claude -p`.
Operator directive 2026-07-29: *"basically anytime memo is using an llm I want
it to be able to use an interactive session"*.

Consequences of that switch, both handled below:

* **The provider can be unavailable.** `complete()` returns None rather than
  raising. Both functions still take the safe *action* — nothing is stored, and
  nothing is merged — but they report it as `error`, never as a judgement.

  ⛔ **This paragraph used to say they "treat None as don't-store / create-a-new
  memo — the safe direction", and that was half right in a way that cost a
  memo.** The action was safe; the *label* was not. v0.3.7 (main, after this
  branch was cut) established the distinction: an agent banks state at the end of
  a turn, gets HTTP 200 `action="skipped"`, and compacts believing the write is
  durable. It is not, and nothing is recoverable. **"The provider refused us" and
  "this exchange wasn't worth keeping" are states the caller acts on completely
  differently.** Failing closed and reporting honestly are independent choices;
  this module now does both.
* **No `response_format={"type":"json_object"}`.** That was an OpenAI-API
  feature; a session returns prose. Both prompts now ask for bare JSON and the
  responses are parsed leniently (fenced blocks tolerated).

Embeddings are untouched and still go to OpenRouter (R-05) — there is no Claude
embedding endpoint, and the operator confirmed they stay.
"""
from __future__ import annotations

import json
import logging
import re

from memo.config import settings
from memo.providers.llm import get_llm_provider
from memo.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


def _status_of(exc: Exception) -> int | None:
    """HTTP status from an OpenAI-SDK exception, if it carries one."""
    for attr in ("status_code", "http_status"):
        code = getattr(exc, attr, None)
        if isinstance(code, int):
            return code
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code if isinstance(code, int) else None


def _provider_error(exc: Exception) -> dict:
    """Describe a provider failure so a caller can tell it from a deliberate skip.

    402 is called out explicitly and separately from 429. OpenRouter signals
    exhausted credit as **402 Payment Required**, not as a rate limit, so a retry
    table that only special-cases 429 sails straight past it — quantum-feed's
    embedder learned this the same way (`news_embcache_prewarm.py`) and notes memo
    "took 14 of them that morning". 402 is not transient and must not be retried:
    it needs a human to top up.
    """
    status = _status_of(exc)
    kind = {402: "payment_required", 429: "rate_limited"}.get(status, "provider_error")
    detail = f"{type(exc).__name__}: {exc}"
    logger.error("auto-store provider failure (%s, status=%s): %s", kind, status, detail)
    return {
        "kind": kind,
        "status": status,
        "detail": detail,
        "retryable": status == 429,
    }


def _provider_unavailable(provider_name: str, detail: str) -> dict:
    """Same shape as `_provider_error`, for the no-exception failure path.

    `LLMProvider.complete()` returns None instead of raising, so a provider that
    is down arrives here as a falsy value rather than an exception. ⚠️ That is
    the easier failure to mislabel precisely *because* nothing was thrown — there
    is no traceback to make it feel like an error, so it reads as a result.

    `retryable` is True: unlike a 402, an unreachable session or a timeout is
    transient by default. A caller that retries once is doing the right thing.
    """
    logger.error("auto-store provider unavailable (%s): %s", provider_name, detail)
    return {
        "kind": "provider_error",
        "status": None,
        "detail": f"{provider_name}: {detail}",
        "retryable": True,
    }

_EXTRACT_SYSTEM = """\
You are a memo curator. Analyze a conversation exchange and extract any knowledge worth storing persistently.

STORE if the exchange contains:
- Facts about the user, their environment, system, project, or preferences
- Solutions to specific technical problems (include the actual solution, not just "it was fixed")
- Decisions made with their rationale
- Research findings, configurations, or domain knowledge gained
- Reusable procedures, commands, code snippets, or patterns
- Infrastructure details, service specifics, or discovered system behavior

SKIP if the exchange is primarily:
- Generic chitchat, greetings, or social pleasantries
- Simple yes/no confirmations or acknowledgments with no substance
- Navigation/exploration with no new findings (e.g., "show me file X", "list files")
- Well-known general knowledge with no user-specific context
- Inconclusive debugging with no resolution
- Content already obvious from the conversation itself

Respond ONLY with valid JSON — no markdown, no explanation outside the JSON:
{
  "should_store": true | false,
  "reason": "one-sentence explanation of decision",
  "title": "Concise, specific title (required if should_store=true)",
  "tags": ["relevant", "tags"],
  "content": "Cleaned, self-contained knowledge to store. Omit conversational filler. Keep all technical details needed to be useful in isolation."
}"""

_MERGE_SYSTEM = """\
You are a memo curator deciding how to handle new information that resembles an existing stored memo.

MERGE: new info updates, expands, or corrects the existing memo (same topic, adds value)
CREATE: new info is distinct enough to stand alone as a separate memo
SKIP: existing memo already covers the new info adequately — nothing to add

If MERGE, produce the complete merged content (not a diff — write the full result).

Respond ONLY with valid JSON:
{
  "action": "merge" | "create" | "skip",
  "reason": "one-sentence explanation",
  "merged_content": "Full merged content (required only if action=merge)",
  "title": "Updated title (only if action=merge and title should change)",
  "tags": ["updated", "tags"]
}"""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _parse_json(text: str) -> dict | None:
    """Parse a JSON object out of a completion.

    A session replies in prose and may fence the JSON or add a sentence around
    it, where the old OpenAI path could demand `response_format=json_object`.
    So: try bare, then a fenced block, then the outermost brace span. Returns
    None if nothing parses — callers treat that like unavailability.
    """
    if not text:
        return None
    for candidate in (
        text.strip(),
        (_FENCE.search(text).group(1).strip() if _FENCE.search(text) else None),
        (text[text.find("{"):text.rfind("}") + 1]
         if "{" in text and "}" in text else None),
    ):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            continue
    return None


async def analyze_for_store(content: str, *,
                            provider: LLMProvider | None = None) -> dict:
    """Decide whether an exchange is worth storing. [001/FR-015a]

    Fails CLOSED — nothing is stored when the provider is unavailable or the
    reply doesn't parse. Auto-store is opportunistic background capture, so a
    missed memo is a small loss; a hallucinated or malformed one pollutes the
    corpus that everything else reads.

    ⛔ But it returns `{"error": ...}`, NOT `{"should_store": False}`. The action
    is the same; the report is not. A caller cannot distinguish "judged not worth
    keeping" from "we never got an answer" if both arrive as a skip. [v0.3.7]
    """
    provider = provider or get_llm_provider()
    # `complete()` is CONTRACTED to return None rather than raise — but a
    # contract is not an enforcement, and a provider that raises would otherwise
    # 500 the endpoint instead of reporting a provider failure. Both failure
    # shapes land in the same place. [v0.3.7]
    try:
        completion = await provider.complete(
            f"{_EXTRACT_SYSTEM}\n\nAnalyze this exchange:\n\n{content[:6000]}",
            budget_tokens=1200,
            timeout_s=settings.memo_llm_timeout_seconds,
        )
    except Exception as e:
        return {"error": _provider_error(e)}
    # ⛔ NOT `should_store: False` on either branch. Nothing gets stored either
    # way — that part of the branch's fail-closed design is right and is kept —
    # but the caller is told this was an ERROR, not a judgement. [v0.3.7]
    if completion is None:
        return {"error": _provider_unavailable(provider.name, "complete() returned None")}
    parsed = _parse_json(completion)
    if parsed is None:
        logger.warning("auto_store: unparseable extract reply: %.200s", completion)
        return {"error": _provider_unavailable(
            provider.name, f"reply was not valid JSON: {completion[:200]!r}")}
    return parsed


async def analyze_for_merge(existing_content: str, new_content: str, *,
                            provider: LLMProvider | None = None) -> dict:
    """Decide whether new info should merge into a similar existing memo.

    On a genuine LLM answer of "unsure", fails toward `create` — the opposite
    direction from `analyze_for_store`, and deliberately so. A spurious extra
    memo is visible and mergeable later; a wrong merge rewrites an existing
    memo's content and silently destroys what was there.

    ⛔ That bias applies to what the model SAYS, never to whether we heard it.
    A provider failure returns `{"error": ...}` — see the note at the call site.
    """
    provider = provider or get_llm_provider()
    try:
        completion = await provider.complete(
            f"{_MERGE_SYSTEM}\n\nExisting memo:\n{existing_content[:3000]}\n\n"
            f"New information:\n{new_content[:2000]}",
            budget_tokens=2000,
            timeout_s=settings.memo_llm_timeout_seconds,
        )
    except Exception as e:
        return {"error": _provider_error(e)}
    # ⛔ NOT `action: "create"`. Degrading to create on a provider failure is the
    # QUIETER half of the same bug: it does not lose the memo, it duplicates one,
    # and a duplicate is far harder to notice than an absence. The caller asked
    # "merge or not?" — and a failure is not an answer. [v0.3.7]
    if completion is None:
        return {"error": _provider_unavailable(provider.name, "complete() returned None")}
    parsed = _parse_json(completion)
    if parsed is None:
        logger.warning("auto_store: unparseable merge reply: %.200s", completion)
        return {"error": _provider_unavailable(
            provider.name, f"reply was not valid JSON: {completion[:200]!r}")}
    return parsed
