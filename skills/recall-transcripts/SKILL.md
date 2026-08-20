---
name: recall-transcripts
description: >-
  Search past Claude Code sessions for what WE DID or SAID, scoped to a project
  directory and a time range. Use when the question is about session history
  rather than a stored fact. Trigger phrases include: "what did we do", "when did
  we change", "what was that error", "did we already try", "what did Ben say
  about", "why is this code like this", "what happened in that session", "have I
  worked on this before", "what was I doing yesterday", "search my transcripts",
  "check the logs for what we", "did anyone on the fleet hit this", "what did we
  decide", "who worked on this", "find the session where".
argument-hint: "<question> [--path <dir>] [--all-projects] [--since <when>] [--hosts all|local]"
disable-model-invocation: false
---

Ask what past sessions say. The `transcript-recall` subagent searches, reads and
judges; you get back a cited finding, and the raw transcripts never enter your
context.

## ⭐ This is NOT `/recall`, and the distinction is the whole point

| | |
|---|---|
| **`/recall`** | what we KNOW — durable facts in memo. Curated, deduped, superseded-aware. |
| **`/recall-transcripts`** | what we DID — raw session history. Uncurated, in-flight, includes things later found wrong. |

Memo holds conclusions somebody decided were worth keeping. Transcripts hold
everything else: the error text, the false start, the thing Ben said once in
passing, the reason a line of code looks odd. **When memo comes back empty on
something you are sure happened, this is where it is.**

## The call

```
Agent(subagent_type="transcript-recall", description="search transcripts: <subject>", prompt="""
Question: <the question, as the caller asked it>

Scope: --path <dir> (or --all-projects) --since <when> [--hosts all]
Caller's cwd: <cwd>   Caller's host: <hostname>
""")
```

**Defaults: `--since 24h`, scoped to the calling session's project, across ALL FOUR
HOSTS.** ⭐ Narrow by project, wide by host — and the two depend on each other. The
fan-out is cheap (~4–6s) *because* `--path` restricts it to one project;
`--all-projects --hosts all` is the expensive shape and stays opt-in.

⚠️ **Host-wide is correctness, not thoroughness.** `~/.claude` is HOST-LOCAL and
seats migrate. `assistant` moved office→server3 on 2026-08-07, freezing its office
transcript — a local-only search of its own project would have reported a six-day
silence that never happened.

## What you owe the caller

⛔ **Keep the negative it gives you.** There are three, and they are not
interchangeable: **"not discussed"** (searched, found nothing — a real negative),
**"nothing was searched"** (zero sessions read, so the work may have happened
elsewhere), and **"host X was not searched"**. Only the first means the thing did
not happen.

⚠️ **Keep its citations** — `<project>/<session8> <date>`. A finding without one
cannot be checked by you or the next session.

⚠️ **Keep its distinction between what was SAID and what was TRUE.** Transcripts
are full of in-flight reasoning that was later corrected.
