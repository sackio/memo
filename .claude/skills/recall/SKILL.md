---
name: recall
description: Search memo for relevant stored memories. Use when the user asks to recall, remember, look up, retrieve, find, or search for something from memory or past context. Trigger phrases include: "do you remember", "what do you know about", "have we talked about", "what did we say about", "pull up", "look up", "find notes on", "any context on", "what's in memory about", "search for", "remind me about", "dig up", "what have we discussed about".
argument-hint: <query> [#tag1 #tag2] [--limit N] [--min-score F]
disable-model-invocation: false
---

Search memo by semantic similarity and return matching documents.

## Usage

- `/recall <query>` — semantic search
- `/recall <query> #tag1 #tag2` — search filtered to documents with those tags
- `/recall <query> --limit 5` — return at most N results (default 10)
- `/recall <query> --min-score 0.5` — only results above this similarity threshold (0–1)

## Steps

1. **Parse `$ARGUMENTS`:**
   - Extract `--limit <n>` (default 10)
   - Extract `--min-score <f>` (default: omit from request)
   - Extract `#tag` tokens as tag filters (strip the `#`)
   - Remaining text is the search query

2. **POST to memo:**

```bash
curl -s -X POST http://localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "...", "limit": 10, "tags": [...]}'
```

3. **Present results:**
   - For each result show: **title** (or first 60 chars of content), score (2 decimal places), tags, and full content.
   - Include the document `id` after each result for reference.
   - If no results: `No memories found for: <query>`

4. **Highlight relevance:** briefly note if any result is directly applicable to the current task.
