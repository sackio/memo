# bench/ — the re-runnable comparison store

`scripts/memo-qa-suite` writes one JSON per run to `bench/results/qa-<UTC>.json`.

## Why results live here and not in chat

Every number this project had before 2026-08-03 was a one-off quoted from a
conversation, and **three of them were measuring something other than their
label**:

- `ok=490 fail=0` (registry mirror) — a post-hoc count over its own plan file.
  It cannot see whether the plan covered the right repos. Only the destination
  answers scope.
- R-14's "incumbent" — a **v2** build, not v1. So "the cutover loses" compared
  two candidates and called one of them the status quo.
- v1's rank-1 of 0 in every band — the harness called `/search-documents`, which
  v1 does not expose. Every query 404'd. **The table read as a result and was an
  instrument failure.**

⇒ None of those were careless readings. **They are what happens when a number is
separated from the configuration that produced it.** Every run here stores that
configuration, read back off the *live* services.

## Reading a result

- `targets[].resolved` — model/dims/url from the RUNNING container. Never from
  compose: this host's shell exports `EMBEDDING_MODEL=…-3-small`, and a
  `${VAR:-default}` in compose takes the shell's value. A container has come up
  on 3-small while the file said 3-large.
- `targets[].search_paths` — which endpoints each build actually exposes. v1 has
  only `/search`; v2 has the explicit ones.
- Per-band **raw integers**, not just percentages. A percentage without its
  denominator cannot be re-checked later.
- `failures` is separate from `absent`. **A query that never ran is not a
  retrieval miss.**
- `status` is `ok` / `failed` / `could_not_run`. ⛔ The third is never folded
  into the second, and a task whose positive control did not fire is never
  reported as a score of zero.

## ⚠️ The totals are UNWEIGHTED means of the five band rates

`n` is fixed per band, so the total is **immune to corpus composition by
construction**. Do not "defend" a total with an argument about corpus mix — the
design already excluded it.

⛔ What composition *does* change is **which memos populate each band**. A harder
pool inside a band shows up as a lower band rate, and neither `n` nor `seed`
touches it. That confound is real and unexamined; it is not the one stratification
handles.
