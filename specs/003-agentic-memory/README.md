# 003 — Agentic memory: the artifacts

Draft, 2026-07-30, operator-directed. These are the concrete files the architecture
needs. Nothing here is installed — they live in the spec until reviewed.

## Layering (four pieces, not one)

```
  caller says /recall or /memorize
        │
        ▼
  skills/recall  ·  skills/memorize          ← thin entry points; spawn the agent
        │
        ▼
  agents/memo-recall  ·  agents/memo-memorize ← thin declarative agents: tools, model
        │                                        and which method skill to preload
        ▼
  skills/memo-retrieval-method  ·  memo-storage-method
                                              ← the actual PROCEDURE, single-sourced
        │
        ▼
  memo MCP tools → passage index → corpus
```

**Why the method lives in a skill and not in the agent's prompt:** the agent file
gets copied into every session's config. The procedure is the part that will change
most often, so single-sourcing it in a skill means a fix lands once rather than
across ~50 stale copies. Agent definitions load at session start, so drift is real
and already measured.

**Why the entry-point skill is separate from the agent:** the caller needs something
to invoke; the agent needs somewhere to live. Keeping them apart means `/recall` can
change how it presents results without touching retrieval behaviour.

### Entry-point skills (thin, shown inline)

`skills/recall/SKILL.md`:
```markdown
Spawn the `memo-recall` subagent with the user's question, any narrowing context
(project, host, timeframe), and the requested answer length if given. Relay its
answer and its citations. Do NOT search memo yourself — a single lookup is the
failure mode this exists to replace.
```

`skills/memorize/SKILL.md`:
```markdown
Spawn the `memo-memorize` subagent with the content to store (text, file path, or
URL), plus why it matters and any context about where it came from. Relay what it
did and the memo ids. Do NOT write to memo directly — a raw write cannot reconcile.
```

## The split: what stays CODE, what becomes JUDGMENT

The interesting design question. Agent judgment is better than a constant for
*discrimination* — deciding whether two memos are the same thing. It is strictly
worse for *constraints*, because a constraint an agent can reason its way around is
not a constraint.

**Stays code, enforced server-side, not delegated:**
- **Never fabricate provenance** (R-18). Unknown source → null + `provenance-pending`.
- **`verbatim-critical` is never rewritten.** Enforced in the result assembler.
- **The auditor may only PROPOSE constitutional changes** (Principle V).
- **Bi-temporal integrity** — supersession writes a new row and never mutates ids;
  `valid_from` always set explicitly.
- **Passage index is replaced wholesale or not at all**, and refuses a partial write.
- **v1 is read-only** during any migration.

**Becomes agent judgment, replacing a guessed constant:**
- **Is this a duplicate?** Today: `MERGE_SIM = 0.94` / `RECONCILE_SIM = 0.82`, both
  chosen by intuition. Measured 2026-07-30: a memo's own 800-char prefix retrieves
  itself at only 0.70–0.89, so the sibling 0.80 "duplicate" bar sits *above* some
  documents' self-similarity. A number that cannot separate a document from itself
  should not be adjudicating duplicates — an agent reading both memos can.
- **Which of the six actions** (merge / supersede / split / reject / clarify /
  write-new) applies.
- **Does this new memo refute an existing one?** Today a threshold-gated LLM call;
  becomes ordinary reasoning.
- **How to phrase, title, tag, and cross-reference** a memo.
- **When to stop searching** during recall.

**Deliberately kept as a fast deterministic path (NOT agentic):**
- **Layer-2 injection.** Fires at every session start and every compaction across
  ~50 sessions. Measured subagent floor is ~2s and 15k–41k tokens *before any work*,
  so an agent cannot sit here. Stays a `command` hook reading a precomputed
  injection set.
- **A plain vector query** for callers who just want the fact and know it is a
  one-lookup question.

## The tiers

| tier | latency | token cost | use |
|---|---|---|---|
| **fast** — direct passage search | ~100ms | ~0 overhead | a known one-lookup fact |
| **agentic** — spawn `memo-recall` | 2–20s | 15–41k floor | needs iteration or synthesis |
| **injection** — precomputed, hook-read | ~100ms | 0 | session start / post-compaction |
| **coordinator** — ATC-delivered | seconds | agent floor | push a finding into a running session |

The coordinator tier is viable because an ATC message that wakes a session and lands
content in its context achieves the goal — it costs a turn, which is a price rather
than a blocker. There is no supported way to push context into a running session
without one.

## Open questions

1. **Version drift.** Agent definitions load at session start, so a change reaches
   ~50 sessions only as each respawns. Proposal: each agent reports a version stamp
   in its result, so a stale one is visible rather than silent.
2. **Does the injection set get built by an agent, offline?** The fast path needs
   precomputed content; nothing says a background agent can't be what precomputes
   it. That gets agent quality without agent latency — `injection_set_cache` already
   exists (migration 006) and is the natural place to put it.
3. **What happens when the agent is wrong?** Citations make it catchable. Nothing
   currently makes it *correctable* — a caller who spots a bad synthesis has no
   route to flag it.
