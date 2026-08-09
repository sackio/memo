# Repoint proposal: `192.168.1.42:32000` — REPORT ONLY, no corpus writes

**2026-08-03 · memo seat · scope: report-only** (the 18:09 approval covered a read-only
sweep; this stays inside it). Nothing in the corpus was modified.

## What changed against the previous report

`specs/truth-sweep-2026-08-02.txt` says **26 claims / 19 memos**. Re-derived directly
against the live v1 corpus (7,955 memos): **41 claims / 28 memos.**

⚠️ **The sweep undercounted, and it also silently truncated.** Its per-target `memos:` line
prints six ids and stops — with no ellipsis and no "+N more" — directly beneath a header
that says 19. A reader has no way to see the cap from the output. **That is a defect in my
instrument, not a discrepancy in the corpus**, and it is the same class as everything else
found tonight: a bounded result rendered as a complete one. Fix before the sweep is used
again for anything that counts.

## The replacement target exists — verified, per the rule

`PROPOSAL-corpus-truth-verification.md`: *before any repoint, verify the replacement
contains the thing being repointed to.* Only **two distinct images** are cited across all
41 claims:

| image | `192.168.1.168:32000` (server4) | `nas:5050` (192.168.1.207) |
|---|---|---|
| `kokoro-tts` | ✅ present | ✅ present |
| `media-faces` | ✅ present | ✅ present |

server4 carries 63 repositories, nas 58. `192.168.1.42:32000` is connection-refused.

⚠️ **A catalog probe rendered a malformed request as an empty registry.** `?n=10000`
exceeds the registry's pagination cap, so it returns an *error object* with no
`repositories` key, and `.get("repositories", [])` turns that into **zero repos** — for
both live registries at once. It reads exactly like "the registry is empty". Use `n=1000`
and assert the key is present.

## ⛔ THE REPOINT CANNOT BE MECHANICAL, AND A KEYWORD CLASSIFIER DOES NOT RESCUE IT

A blind substitution across 41 claims would be wrong on at least three distinguishable
classes:

1. **Memos that cite the dead target IN ORDER TO DENY IT.** `b7581785` is titled
   *"Registry is nas:5050 = 192.168.1.207:5050, NOT .42:32000"*. Substituting turns it into
   *"nas:5050, NOT server4:32000"* — **a true correction rewritten into a false one, with
   its meaning exactly reversed.**
2. **Deliberate historical record.** `a4c0c234` documents the June migration *from*
   `.42:32000`; `5e28cb01` is last night's consolidation decision and cites the dead
   registry as the prior state. The old address is the point of the sentence.
3. **Config listings that legitimately name both.** `5b5eb2e2` contains an ansible loop
   creating `certs.d/` trust directories for `.42:32000` *and* `.168:32000`, describing the
   former as "legacy server5 registry (still answers)". Substituting corrupts working
   config into a duplicate entry.

**A keyword classifier was tried and is unreliable in both directions.** Flagging denial
words (`not|was|legacy|migrated|…`) within ±160 chars split 41 claims into 28 asserted / 13
denied, and hand-checking the first eight found errors *both* ways:

- `839cc8c7` → flagged DENIED, is a plain assertion; the `NOT` belongs to an unrelated
  sentence about a health endpoint.
- `5b5eb2e2` → flagged ASSERTED, is the ansible config listing above.

⭐ **This is the ASSERTED-vs-QUOTED distinction from the Feature 004 requirements (memo
`9e20d461`) reappearing in a different workstream.** There it was transcript ingest; here it
is corpus repair. **Proximity cannot recover whether a string is being used or mentioned**,
and a repair tool that assumes it will confidently damage the memos that were already right.

## ⚠️ AND THE TARGET IS NOT SINGLE-VALUED EITHER

My own rewarm pin said *"correct target is nas:5050 TODAY"*. That is now wrong, or at least
incomplete: last night's consolidation (`5e28cb01`) made **server4 primary with nas as
fallback**. So the correct replacement depends on the claim's context — a barn-k8s pull
reference and a docker/dev reference do not resolve to the same host — **and on the claim's
date**, since a memo describing June's state should keep June's address.

⇒ **There is no single find-and-replace that is correct for all 41.**

## Proposed handling — for approval, not yet executed

| class | n (est.) | action |
|---|---|---|
| live operational reference, target unambiguous | ~20 | repoint to `192.168.1.168:32000`, add a dated note |
| historical / migration record | ~8 | **leave verbatim**; add a forward pointer to `5e28cb01` |
| denial or correction of the dead target | ~2 | **leave verbatim — never touch** |
| config listing naming both registries | ~3 | review individually; likely leave |
| ambiguous | ~8 | leave, list for a human pass |

Counts are estimates from an unreliable classifier and **must be re-derived per claim before
any write**. They are shown to size the work, not to authorise it.

**Recommendation:** do not batch this. The classes that must not be touched are exactly the
memos that were already correct, and the failure is silent — a reversed correction reads as
a normal memo. If it is worth doing, it is worth doing one claim at a time with the context
in view.
