---
name: recall
description: >-
  Recall stored knowledge from memo. Use when the user asks to recall, remember,
  look up, retrieve, find, or search for something from memory or past context,
  AND when you need background before acting. Trigger phrases include: "do you
  remember", "what do you know about", "have we talked about", "what did we say
  about", "pull up", "look up", "find notes on", "any context on", "what's in
  memory about", "search for", "remind me about", "dig up", "what have we
  discussed about", "what was", "do we have", "check memo", "check memory", "how
  do we", "how did we", "where is", "where was", "who is", "who was", "when did
  we", "load context on", "get background on", "bring in context", "pull
  context", "load memories about", "give me context on", "background on",
  "what's stored about", "load notes on". Also trigger on direct factual
  questions memo likely answers — server IPs, credential locations, project
  conventions, contacts, network topology, hardware specs, past incidents.
argument-hint: "<question> [--context <words>]"
disable-model-invocation: false
---

Ask memo a question. The `memo-recall` subagent does the searching and the
reasoning; you get back a short answer with citations, not a pile of results.

## The call

```
Agent(subagent_type="memo-recall", description="recall <short subject>", prompt="""
Question: <the question, as the caller asked it>

Specifics the caller already holds: <hostnames, paths, ids, error strings, dates>
Answer budget: <words — default 400>
""")
```

⭐ **`--context <words>` when you need a briefing, not an answer.** The default is a
short answer (~400 words) because the point is to protect your context. Raise it
when you are loading up to work on something — *"give me everything on the barn
cluster"* — and pass the number through as the budget above. A bigger budget buys
more ground covered, not more words about the same ground.

⭐ **Pass every identifier you have.** Phrasing barely moves ranking; the specific
terms in the query move it a lot. A hostname or error string costs you one line and
saves a search it may not think to run.

⛔ **Do not rephrase the question** — not into keywords, not into prose. Pass it as
asked and let the subagent vary the angle; that is its job.

⚠️ **Say so if the question is historical.** Retired memos are excluded by default,
which is right for *"what is true"* and wrong for *"what did we believe then"*.

## What you owe the caller

⚠️ **Relay an unresolved conflict.** If it reports two memos that disagree and says
it could not tell which is true, that is a decision for the user, not a retrieval
detail. Do not bury it.

⚠️ **Relay a null.** "Memo holds nothing on this, and here is what was tried" is a
real answer. Do not pad it out with tangential memos.

⛔ **Distinguish what memo SAYS from what the subagent INFERRED.** It marks the
difference; preserve it.

⛔ **Never echo a credential value** into Slack, a log, or an email body, even when
a memo you were shown contains one. Say where it lives.

## When NOT to recall

Skip when the request is self-contained (*"write hello world"*), when the answer is
already in this conversation, or when the question is about **live state** rather
than memory (*"what's my CPU load"* is a probe, not a recall).

⚠️ **Memo holds what is TRUE, not what we DID or SAID.** Session history — "when
did we change X", "what did Ben decide", "why is this code like this" — lives in
transcripts. Use `/recall-transcripts`.

⭐ **When in doubt, recall.** The failure it prevents — asking Ben something the
corpus already knows, or acting on an assumption memo would have corrected — is far
more expensive than the call.
