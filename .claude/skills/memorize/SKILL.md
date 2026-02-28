---
name: memorize
description: Store or update something in memo (semantic document storage). Use when the user asks to remember, memorize, save, or update something for later retrieval.
argument-hint: [content] [#tag1 #tag2] [--update <id>]
disable-model-invocation: false
---

Store or update memos with automatic embedding, tags, and token count.

## Usage

- `/memorize <content>` — store the content as-is
- `/memorize <content> #tag1 #tag2` — store with tags (parsed from `#words`)
- `/memorize` — summarize and store the most important context from the current conversation
- `/memorize --update <id> <content>` — update an existing memo's content
- `/memorize --update <id> #tag1 #tag2` — update only tags on an existing memo
- `/memorize --update <id> <content> #tag1 #tag2` — update content and tags

## Steps

### Storing a new memo

1. **Determine content:**
   - If `$ARGUMENTS` has text (and no `--update`), that is the content to store.
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

### Updating an existing memo

1. **Parse `--update <id>`** from arguments to get the target memo ID.

2. **Parse the remaining arguments** the same way as storing: extract `#tags`, remaining text is new content (may be empty if only updating tags).

3. **PATCH to memo** with only the fields being changed (omit fields that aren't changing):

```bash
curl -s -X PATCH http://localhost:8000/documents/<id> \
  -H 'Content-Type: application/json' \
  -d '{"content": "...", "tags": [...]}'
```

4. **Confirm:** reply with `Updated: <title> (id: <uuid>)` and what changed.

## Guidelines

- Write content as if the reader has zero context — self-contained facts only.
- Prefer dense, concise prose over bullet lists.
- Wrap code snippets in fenced code blocks within the content string.
- Never store secrets, API keys, or PII.
