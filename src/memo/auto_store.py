"""LLM-based extraction and dedup for auto-storing memos from conversation exchanges.

**R-17**: these two calls used to go straight to OpenRouter
(`openai/gpt-4o-mini`) and were memo's only pre-existing generative caller. They
now go through the shared `LLMProvider`, i.e. an interactive Claude Code session
riding the Max subscription — never a per-token API, and never `claude -p`.
Operator directive 2026-07-29: *"basically anytime memo is using an llm I want
it to be able to use an interactive session"*.

Consequences of that switch, both handled below:

* **The provider can be unavailable.** `complete()` returns None rather than
  raising, and both functions here treat None as "don't store / create a new
  memo" — the safe direction. Auto-store is opportunistic background capture,
  so skipping is acceptable; guessing is not.
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

    Fails CLOSED (`should_store: False`) when the provider is unavailable or
    the reply doesn't parse. Auto-store is opportunistic background capture, so
    a missed memo is a small loss; a hallucinated or malformed one pollutes the
    corpus that everything else reads.
    """
    provider = provider or get_llm_provider()
    completion = await provider.complete(
        f"{_EXTRACT_SYSTEM}\n\nAnalyze this exchange:\n\n{content[:6000]}",
        budget_tokens=1200,
        timeout_s=settings.memo_llm_timeout_seconds,
    )
    if completion is None:
        return {"should_store": False,
                "reason": f"LLM unavailable ({provider.name}) — skipping auto-store"}
    parsed = _parse_json(completion)
    if parsed is None:
        logger.warning("auto_store: unparseable extract reply: %.200s", completion)
        return {"should_store": False, "reason": "analysis reply was not valid JSON"}
    return parsed


async def analyze_for_merge(existing_content: str, new_content: str, *,
                            provider: LLMProvider | None = None) -> dict:
    """Decide whether new info should merge into a similar existing memo.

    Fails toward `create` — the opposite direction from `analyze_for_store`, and
    deliberately so. A spurious extra memo is visible and mergeable later; a
    wrong merge rewrites an existing memo's content and silently destroys what
    was there.
    """
    provider = provider or get_llm_provider()
    completion = await provider.complete(
        f"{_MERGE_SYSTEM}\n\nExisting memo:\n{existing_content[:3000]}\n\n"
        f"New information:\n{new_content[:2000]}",
        budget_tokens=2000,
        timeout_s=settings.memo_llm_timeout_seconds,
    )
    if completion is None:
        return {"action": "create",
                "reason": f"LLM unavailable ({provider.name}) — storing separately"}
    parsed = _parse_json(completion)
    if parsed is None:
        logger.warning("auto_store: unparseable merge reply: %.200s", completion)
        return {"action": "create", "reason": "merge reply was not valid JSON"}
    return parsed
