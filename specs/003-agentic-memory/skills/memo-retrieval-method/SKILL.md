---
name: memo-retrieval-method
description: How to answer a question from memo — iterate, follow cross-references, cite everything, report absence honestly. Loaded by the memo-recall agent.
---

# Answering a question from memo

You are answering, not retrieving. The caller wants the fact, not the filing.

## 1. Read the question properly

Separate three things the caller may have given you:
- **the question** — what they actually want to know
- **narrowing context** — project, host, time period, who was involved
- **the shape of the answer** — how much detail, default brief-but-complete

If the question is really several questions, answer each.

## 2. Search more than once

One query is rarely enough, and the corpus punishes lazy phrasing.

- Start with the caller's own words.
- Then rephrase: semantic search misses obvious matches when you only try the
  obvious phrase. For "VyOS proxy-ARP bug", also try "DHCP DECLINE Apple devices"
  and "Kea declined lease".
- Use distinctive nouns and identifiers over generic ones — an error string, a
  hostname, a ticket number beats a topic word.
- If a result is close but not right, **use its vocabulary for the next query**.
  The corpus has its own dialect; borrow it.

## 3. Read, then follow the thread

- Fetch the promising memos in full. A snippet is not enough to answer from.
- **Follow cross-references.** Memos link to each other by id; a linked memo is
  frequently the one that actually answers.
- Watch for supersession banners (`> Superseded … by <id>`) and go read the newer
  memo. Answering from a superseded memo is the worst failure available to you,
  because it is confidently wrong.
- Prefer recent memos where they conflict, but note the conflict.

## 4. Know when to stop

Stop when the question is answered, or when you can state precisely what the corpus
does not contain. **Do not keep searching to avoid saying "memo doesn't know".**

## 5. Answer

- **Answer the question first.** Not a summary of your search.
- **Cite every claim** with the memo id it came from. The caller cannot see the raw
  memos; citations are the only way a wrong synthesis is ever caught, and the only
  way they can correct or supersede what you drew on.
- **Mark inference.** If you combined two memos to reach something neither says,
  label it. Your inference presented as a stored fact is how the corpus acquires
  claims nobody wrote.
- **Report conflicts** rather than silently picking a side.
- **Report absence plainly.** "memo has nothing on this" is a real, useful answer.
  Returning the nearest three unrelated memos is the behaviour that taught agents
  not to trust search in the first place.
- **Respect the requested length.** If they asked for one line, give one line plus
  citations.

## 6. If retrieval itself looks broken

If a memo you know exists will not surface, say so in your answer. That is a corpus
health signal worth surfacing, not an embarrassment to hide — long memos are
measurably hard to retrieve, and a report of it is how that gets fixed.
