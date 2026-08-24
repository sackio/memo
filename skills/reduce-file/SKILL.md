---
name: reduce-file
description: >-
  Read a file that is too big to read — get back only the part that answers your
  question, inside a token budget you set, measured with a real tokenizer. Use when
  a single file would blow your context: a huge log, a 5,000-line module, a spec you
  need three sections of. For a whole directory use /reduce-dir.
---

# reduce-file

**Delegate. Do not read the file yourself** — that is the entire point.

⛔⛔ **YOUR CONTEXT RECEIVES THE FINISHED SUMMARY AND NOTHING ELSE.** No chunk, no
intermediate summary, no consolidation work lands here. `file-content-reducer` does all
of it in its own context and its children's. ⇒ **Never invoke `chunk-summarizer`
yourself, and never chunk a file by hand** — doing either pulls the raw content back
into the context you were protecting, which is the whole failure this replaces.

**Size is not your problem.** The subagent handles a file of any size: under one
agent's context it reduces in a single pass; above that it chunks, fans out to parallel
`chunk-summarizer` children, and consolidates. It picks the route from a measured token
count — you do not need to know which one it took, though it reports it.

```
Agent(subagent_type="file-content-reducer",
      description="reduce <filename> to <N> tokens",
      prompt="""file_path: /abs/path/to/thing.py
token_budget: 2000
instructions: <what you are actually looking for — be specific>
strategy: extract        # extract | summarize | sample | preserve  (default extract)
preserve_patterns: []    # optional; lines matching these always survive
single_pass_ceiling: 120000   # optional override — above this it chunks""")
```

⚠️ **Chunking is a last resort and the subagent treats it as one.** Below the ceiling,
one agent reading the file end to end gives a more cohesive answer than several reading
fifths of it — every chunk boundary is somewhere a definition got cut in half. Do not
ask for chunking; let the measured size decide.

## Pick the budget from the room you actually have

Not from the file. "How much of my context can I spend on this?" — then subtract ~10%
headroom, because the tokenizer is `cl100k_base` (GPT's) and Claude's differs slightly.

## Before you delegate: is this the right tool?

| you want | use |
|---|---|
| the part of a file that answers a question | **this** |
| whether a string appears at all | `Grep` — far cheaper, exact |
| a specific known line range | `sed -n '400,460p'` — cheaper, exact |
| a stored fact the fleet already knows | `/recall` — the file may not be the source of truth |
| what a past session did or said | `/recall-transcripts` |

⭐ **A reducer is for when you must look at content you cannot afford to hold.** If you
know the line numbers, or you only need a yes/no, a plain tool beats an agent.

## Reading the result honestly

The subagent returns a `--- reduction ---` block. **Read the `route:` and `dropped:`
lines.** `route: chunked (5 chunks, 4 returned)` means one span of the file was never
summarised — the answer covers four fifths of it, and nothing else in the output will
tell you that. **Read the `dropped:` line.** It names
what is *not* in front of you, and it is the difference between "this file does not
mention X" and "the part I was shown does not mention X" — which are not the same claim
and will not feel different.

⛔ **Never quote a reduced extract as if it were the whole file.** If you go on to record
a conclusion in memo, say it came from a reduction and name the budget.

⚠️ Three outcomes, and the subagent is required to keep them apart: reduced content ·
**"file exists and is empty"** · **"COULD NOT READ: <reason>"**. If you get the third,
you learned nothing about the file's contents — do not report it as "nothing relevant
found".

## The tokenizer, if you want it directly

```bash
/mnt/nas/data/code/memo/scripts/token-count <path>
/mnt/nas/data/code/memo/scripts/token-count --text "some string"
```

tiktoken `cl100k_base`, JSON out. Identical to
`mcp__context-helpers__count_tokens_file` (verified equal, 2026-08-24) but needs no
ToolSearch and no per-host MCP config. ⛔ **Never estimate tokens from character count**
— measured on this fleet, that runs ~8% low on average with 4x density variation, and
one real document came in at 1.55 chars/token against the assumed ~4.
