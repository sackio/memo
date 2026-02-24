# Memorize

Store something in memo with automatic embedding, tags, and token count.

Usage:
- `/memorize <content>` — store the content as-is
- `/memorize <content> #tag1 #tag2` — store with tags (parsed from trailing #words)
- `/memorize` — store a summary of the current conversation context

## Instructions

When this skill is invoked:

1. Determine what to store:
   - If `$ARGUMENTS` contains text, that is the content to memorize.
   - If no arguments, summarize the most important context from the current conversation (decisions made, facts learned, code patterns, etc.) into a concise paragraph.

2. Parse tags: extract any words prefixed with `#` from the arguments as tags (strip the `#`). The remaining text (with `#tags` removed) is the content.

3. Choose a short descriptive `title` (5–10 words) for the document.

4. Call `POST http://localhost:8000/documents` with:
   ```json
   {
     "content": "<content>",
     "title": "<title>",
     "tags": ["<tag1>", "<tag2>"]
   }
   ```
   Use the Bash tool with curl, or Python urllib — do NOT use a browser.

5. Confirm to the user: "Memorized: <title> (id: <uuid>)" and list the tags if any.

**Important:**
- Keep content self-contained and factual — write as if the reader has no context.
- Prefer concise, dense content over long prose.
- If the content is a code snippet, wrap it in a fenced code block inside the content string.
- Never store PII, secrets, or API keys.
