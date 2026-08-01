"""LLM-based extraction and deduplication for auto-storing memos from conversation exchanges."""

import json
import logging

from openai import AsyncOpenAI

from memo.config import settings

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    api_key=settings.openrouter_api_key,
    base_url="https://openrouter.ai/api/v1",
)


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
  "tags": ["updated", "tags"] (only if action=merge)
}"""


async def analyze_for_store(content: str) -> dict:
    """Ask the LLM whether content is worth storing and extract title/tags/cleaned content."""
    try:
        resp = await _client.chat.completions.create(
            model=settings.auto_store_model,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": f"Analyze this exchange:\n\n{content[:6000]}"},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=1200,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        # NOT `should_store: False`. "The provider refused us" and "this exchange
        # wasn't worth keeping" are states the caller acts on completely
        # differently, and folding them together is what made 2026-07-31
        # invisible: an agent banks state at the end of a turn, gets HTTP 200
        # action="skipped", and compacts believing the state is durable. It is
        # not, and nothing is recoverable.
        #
        # The reason string always carried the truth ("analysis error: ..."); the
        # `action` did not, and the hook only reads `action`. So the fix is to put
        # the distinction where the caller actually looks.
        return {"error": _provider_error(e)}


async def analyze_for_merge(existing_content: str, new_content: str) -> dict:
    """Ask the LLM whether to merge new content into an existing similar memo."""
    prompt = (
        f"Existing memo:\n{existing_content[:3000]}\n\n"
        f"New information:\n{new_content[:2000]}"
    )
    try:
        resp = await _client.chat.completions.create(
            model=settings.auto_store_model,
            messages=[
                {"role": "system", "content": _MERGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=2000,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        # Degrading to "create" on a provider failure is the quieter half of the
        # same bug: it does not lose the memo, it duplicates one. The caller asked
        # "merge or not?" and a failure is not an answer.
        return {"error": _provider_error(e)}
