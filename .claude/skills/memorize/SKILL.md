---
name: memorize
description: Store something in memo (semantic document storage). Use when the user asks to remember, memorize, or save something for later retrieval.
argument-hint: [content] [#tag1 #tag2]
disable-model-invocation: false
---

Store content in memo with automatic embedding, tags, and token count.

## Usage

- `/memorize <content>` — store the content as-is
- `/memorize <content> #tag1 #tag2` — store with tags (parsed from trailing #words)
- `/memorize` — summarize and store the most important context from the current conversation

## Steps

1. **Determine content:**
   - If `$ARGUMENTS` has text, that is the content to store.
   - If no arguments, summarize the key facts, decisions, and patterns from the current conversation into a concise, self-contained paragraph.

2. **Parse tags:** extract any `#word` tokens from the arguments as tags (strip the `#`). The remaining text is the content.

3. **Choose a title:** 5–10 words describing the content.

4. **POST to memo:**

```bash
curl -s -X POST http://localhost:8000/documents \
  -H 'Content-Type: application/json' \
  -d '{"content": "...", "title": "...", "tags": [...]}'
```

5. **Confirm:** reply with `Memorized: <title> (id: <uuid>)` and the tags if any.

## Guidelines

- Write content as if the reader has zero context — self-contained facts only.
- Prefer dense, concise prose over bullet lists.
- Wrap code snippets in fenced code blocks within the content string.
- Never store secrets, API keys, or PII.
