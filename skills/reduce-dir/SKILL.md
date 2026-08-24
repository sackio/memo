---
name: reduce-dir
description: >-
  Answer a question that spans a whole directory without reading it — walk the tree
  with pattern filters, allocate one token budget across the files, reduce each, and
  get back a consolidated answer with a coverage report. Use for "what does this
  package do", "find every caller of X", "summarise these logs". For one file use
  /reduce-file.
---

# reduce-dir

**Delegate.** The subagent walks, measures, allocates, fans out to per-file reducers in
parallel, and consolidates.

⛔⛔ **YOUR CONTEXT RECEIVES THE FINISHED ANSWER AND NOTHING ELSE** — not the files, not
the per-file reductions, not the chunk summaries of any single large file inside the
tree. All of it happens one and two levels down. ⇒ never fan out over the files
yourself, and never call `file-content-reducer` or `chunk-summarizer` per file from
here; that pulls the intermediates into the context you were protecting.

```
Agent(subagent_type="directory-content-reducer",
      description="reduce <dir> to <N> tokens",
      prompt="""directory: /abs/path/to/dir
token_budget: 8000
instructions: <the actual question — this drives the allocation>
pattern: '*.py'          # optional glob, default *
recursive: true
exclude: ['.git', 'node_modules']""")
```

⚠️ **Always exclude `.git`, and `node_modules` on any JS/TS tree.** Otherwise the budget
is spread across thousands of vendored files and every file you care about gets a
starvation slice.

## ⛔ Read the coverage block before you believe the answer

The subagent returns four numbers that are **not** the same number:

```
found: 200   counted: 186   skipped: 14
read in full: 3   reduced: 9   NOT READ: 174
```

**A consolidated answer over 12 of 200 files reads exactly like an answer over all 200**
— same prose, same confidence. The coverage block is the only thing that tells them
apart, and `NOT READ` is the line that decides whether the answer means what it looks
like it means.

- `skipped` = could not be counted (binary, unreadable). **Not** "empty".
- `NOT READ` = counted, then given no budget. Real content nobody looked at.
- If `NOT READ` is large and the question was "find every X", the honest reading is
  **"X was not found in the 12 files that were read"** — not "X is not in the directory."

⚠️ **Zero files found has two causes**: the pattern matched nothing in a directory that
exists, or the directory does not exist. The subagent must say which; if it did not, ask.

## Scoping beats budgeting

⭐ **Narrowing the pattern is almost always better than raising the budget.** `*.py`
under `src/` answers a code question better at 4,000 tokens than `*` under the repo
root does at 20,000 — the second spends most of its budget on lockfiles and fixtures.
Think about which files could possibly contain the answer, and filter to those first.

If you already know which handful of files matter, skip this skill: call
`/reduce-file` on each in parallel, or just `Grep`.

## The tokenizer

```bash
/mnt/nas/data/code/memo/scripts/token-count <dir> --pattern '*.py' \
    --exclude .git --exclude node_modules --budget 8000
```

Prints per-file counts largest-first, the total, what could not be counted and why, and
a proportional-with-a-floor allocation. Cheap and read-only — worth running yourself
before delegating, just to see whether the tree fits already
(`fits_without_reduction: true` means don't reduce at all, just read it).

⛔ **Never estimate tokens from character or byte count.** ⚠️ `cl100k_base` is GPT's
tokenizer, not Claude's — real and stable (identical counts on all four hosts across
tiktoken 0.11/0.12/0.13, verified 2026-08-24), but leave ~10% headroom.
