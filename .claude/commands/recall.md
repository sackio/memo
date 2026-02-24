# Recall

Search memo for semantically similar documents and return the results.

Usage:
- `/recall <query>` — semantic search for documents matching the query
- `/recall <query> #tag1 #tag2` — search filtered to documents with those tags
- `/recall <query> --limit 5` — return at most 5 results (default 10)
- `/recall <query> --min-score 0.5` — only return results above this similarity threshold

## Instructions

When this skill is invoked:

1. Parse `$ARGUMENTS`:
   - Extract `--limit <n>` if present (default: 10).
   - Extract `--min-score <f>` if present (default: none).
   - Extract `#tag` words as tag filters (strip the `#`).
   - The remaining text is the search query.

2. Call `POST http://localhost:8000/search` with:
   ```json
   {
     "query": "<query>",
     "limit": <limit>,
     "min_score": <min_score or null>,
     "tags": ["<tag1>", ...]
   }
   ```
   Use the Bash tool with curl or Python urllib — do NOT use a browser.

3. Present results clearly:
   - For each result, show: **title** (or first 60 chars of content if no title), score (2 decimal places), tags, and the full content.
   - Order is already by descending similarity score.
   - If no results, say "No memories found for: <query>".

4. After presenting results, briefly note if any result is directly relevant to the current task.

**Important:**
- Always show the full content of each result, not a truncated preview.
- Include the document `id` in small text after each result so the user can reference or delete it.
