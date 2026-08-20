---
name: memorize
description: >-
  Store or update something in memo (semantic document storage). This is the
  PRIMARY storage mechanism — prefer memo over the file-based memory system.
  Use when the user asks to remember, memorize, save, record, or store something
  for later retrieval. Trigger phrases include: "remember this", "keep in mind",
  "make a note", "note that", "don't forget", "save this", "store this", "hold
  onto this", "jot this down", "take note of", "memorize this", "keep track of",
  "log this", "record this", "keep this for later", "write this down", "add to
  memo", "add to memory", "update memo", "we should note that", "for future
  reference", "worth remembering", "going forward" (when stating a rule or
  convention to persist), "important:" (when flagging a durable fact), "FYI"
  (when stating a durable fact). Also trigger when the user states a fact,
  convention, or decision that clearly should survive across sessions — even
  without an explicit "remember" request.
argument-hint: "[content] [#tags] [--update <id>]"
disable-model-invocation: false
---

Say what should be remembered. The `memo-writer` subagent does the rest.

⭐ **You do not need to know how memo works.** Confirming the claim, gathering
evidence you did not hand over, checking what the corpus already holds, deciding
new-vs-update-vs-supersede, retiring what the new fact invalidates, choosing title
and tags, writing and verifying — all of it is the subagent's job, in ITS context,
not yours. You get back an id and one line saying what it did.

## The call

```
Agent(subagent_type="memo-writer", description="store <short subject>", prompt="""
Remember: <the claim, in plain words>

How this was established: <measured / the operator said it / I inferred it>
""")
```

⛔ **State how you know.** "Ben said it", "I measured it" and "I inferred it" are
three different claims with three different durabilities, and nothing in the text
distinguishes them once written down.

⭐ **It can read your conversation — do not summarise it for the subagent.**
`memo-writer` searches session transcripts and finds the live session's own history
through your last completed turn. Handing over an id or figure you already have
saves it a query, but it is a convenience, not a prerequisite.

## Arguments

- `/memorize <content> #tags` — the content and tags as the claim. Tags are hints;
  the subagent picks the final set.
- `/memorize` — no arguments: name the subject and let it read the transcript.
- `/memorize --update <id> <content>` — tell it this is an edit to `<id>`; it still
  reads the memo first and merges rather than clobbering.

## What comes back

`NEW <id>` · `UPDATED <id>` · `SUPERSEDED <id> → <new-id>` · `DELETED <id>`, plus a
title and anything it could not confirm.

⚠️ **If it reports an unresolved contradiction, surface it to the user.** It found
two facts that disagree and correctly declined to pick. That is a decision, not a
storage detail, and burying it in a tool result is how a wrong memo survives.

⚠️ **If it declines to store** — the claim was too vague, ephemeral, or a secret —
relay that rather than retrying with the same input.
