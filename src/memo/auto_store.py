"""LLM-based extraction and deduplication for auto-storing memos from conversation exchanges."""

import json

from openai import AsyncOpenAI

from memo.config import settings

_client = AsyncOpenAI(
    api_key=settings.openrouter_api_key,
    base_url="https://openrouter.ai/api/v1",
)

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
        return {"should_store": False, "reason": f"analysis error: {e}"}


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
        return {"action": "create", "reason": f"merge analysis error: {e}"}
