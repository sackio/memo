# Spec-annotation convention — linking code to specs and tasks

**Scanner**: `speckit-trace` (the enforcement half — this doc is nothing without it,
per *enforce-don't-document*). Together the levels close the loop:
**FR → task (names the FR) → code/gate (anchors both)**.

Installed via `uv tool install speckit-trace`. Drop this doc + `.specify/trace.toml`
into a project with `speckit-trace init`.

## The marker

One canonical, greppable grammar — a **spec-qualified id** inside any comment:

```
NNN/FR-XXX        e.g. 002/FR-044, 005/FR-D-SERVING     (a requirement)
NNN/Tnnn          e.g. 002/T110, 008/T9.6, 001/T060c    (a task)
NNN/SC-nnn        e.g. 008/SC-009                       (a success criterion)
NNN/C-nn          e.g. 008/C-03                         (a clarification, where ids exist)
CONST-I..VII      e.g. CONST-II                         (a constitution principle)
```

`NNN` is the spec's three-digit directory prefix (`specs/008-watermarks/` → `008`).

Definitions are read from the spec's own Markdown. An `FR-`, `SC-` or `C-` id counts as
*defined* when it appears as a bold list item in `spec.md`:

```markdown
- **FR-003**: The service MUST refuse a stale watermark loudly.
- **SC-009**: p99 ingest latency stays under 200ms at 10k events/s.
- **C-03**: "Stale" means older than the last committed checkpoint.
```

Task ids are read from the sibling `tasks.md`. CONST markers cannot dangle — the numeral
set is closed — they can only be absent.

Recommended placement form (any language's comment syntax):

```rust
//! Bar-lane derivation service. [002/FR-044 002/T110]
/// Refuses a stale watermark LOUD. [008/FR-003]
#[test] // enforces 008/FR-005 (append-before-ack) — 008/T016
```

```python
"""Watermark gate. [008/FR-003 008/T016]"""
```

The bracket/prose framing is free; the scanner keys ONLY on the `NNN/<id>` token, so any
comment style in any language works.

## Scoped anchors — when a gate enforces only part of a requirement

A requirement often names several arms. A test may honestly enforce one of them. Writing the
limitation in prose beside the marker is the right instinct, but the scanner cannot read
prose — so a `FULL` rating ends up resting on partial enforcement.

Say it in the marker instead:

```rust
#[test] // enforces 002/FR-017 scope:refdata-arm — only the refdata arm; see FR text
```

```python
# enforces 002/FR-017 scope:refdata-arm
```

The token is `scope:<slug>` on the same line as the marker, and it applies to every marker on
that line. Any slug you like — it names *which* part.

A requirement whose **every** enforcing anchor is scoped is reported as a **scoped gate**:
enforced in part, rated as enforced. One unscoped gate clears it, because something then
enforces the whole thing. `--require-full-scope` makes it a failure.

Scope on an *implementation* anchor is fine and is not reported — implementations legitimately
span units. It is enforcement scope that decides whether a rating is honest.

> **The tool will never report a fraction.** A slug says which part is covered; nothing
> declares how many parts exist, so `1/5` is unknowable and will not be invented. If you want
> completeness computed rather than flagged, enumerate the arms in the spec (give each its own
> `SC-nnn` or requirement id) — then each becomes independently anchorable and the denominator
> is *declared* rather than guessed.

> ⚠️ **Which tool reads `scope:`.** `speckit-trace` does. A project's own older in-repo
> scanner probably does not — it will match the bare `NNN/FR-XXX` and ignore the trailing
> token, so the anchor still resolves and the run still passes, but it is **counted as a full
> anchor**. The failure is silent and points the wrong way: over-reporting, not erroring.
>
> If your gate is a legacy in-repo scanner, a `scope:` token is honest to `speckit-trace` and
> invisible to the thing that blocks your landings. Either teach that scanner the token, or
> move the gate to `speckit-trace`. Documenting the hole is not closing it — this note exists
> so nobody mistakes a green legacy run for a scope-aware one.

*(Grammar added 2026-07-26 after real usage: an FR naming five arms was anchored to a test
enforcing one, with the limit stated in prose. Real usage precedes the rule.)*

## ⚠️ Files that *contain* markers as data — read this before writing tests for a scanner

The scanner is deliberately language-agnostic and line-based. **It cannot tell a marker from
a string that looks like one.** Any file that carries markers as *data* — test fixtures,
worked examples, a changelog quoting one — will have them read as real anchors.

This is not theoretical. It has produced wrong answers **in both directions** in real repos:

- A requirement rated **FULL on a fixture alone** — its only "enforcing test" was a marker
  inside a string literal in a scanner's own unit test. There was no test.
- A **true finding suppressed** — an unscoped marker in a fixture satisfied the "one unscoped
  gate clears it" rule and hid a requirement that really was enforced only in part.

Put the directive on every such line:

```python
'"""Bar-lane derivation. [002/FR-044] scope:one-lane"""')   # speckit-trace: ignore
```

```markdown
Example marker: `002/FR-044`  <!-- speckit-trace: ignore -->
```

`speckit-trace` skips the line and **prints how many lines were skipped on every run**, so
the suppression is visible from outside rather than buried in the source.

**If you are writing tests for a marker scanner, assume every fixture line needs it.** That is
the canonical case, and both this tool's repo and quantum-feed's hit it independently.

## Retiring a spec

A spec whose subject moved, or whose direction was abandoned, keeps producing debt that is
accurate and meaningless. Say so in the spec and the tool stops counting it:

```markdown
**Status**: SUPERSEDED
```

or the form real projects reach for — a callout at the top, with the reason:

```markdown
> **⚠️ SUPERSEDED (2026-07-27).** Subject moved to the dojo repo under
> `dojo/specs/001-…`. Kept here unarchived because `experiments.md` beside it is
> the live experiment record.
```

`SUPERSEDED`, `ARCHIVED`, `DEPRECATED` and `RETIRED` all work, in the first 30 lines only.
The spec is **still listed and still rated**, with its status and the declaring line shown —
hiding it would be the abandoned-spec problem inverted: invisible rather than loud, and
equally dishonest.

The tool never *infers* retirement. A spec that merely discusses superseding something stays
live, because a measurement that can switch itself off by talking is not a measurement.

## Rules

1. **Qualify the spec number — always.** A bare `FR-009` is ambiguous once more than one
   spec defines one. Bare FR cites in NEW code are drift; the scanner does not resolve them.
2. **Anchor at the owning unit, once.** The module/fn that IMPLEMENTS an FR gets one marker
   at its header — not every line (comment-noise rule: a comment states what the code can't).
3. **Every gate/tooth names what it enforces — MANDATORY for new tests.** A test that
   enforces an FR carries `NNN/FR-XXX`; if it was born from a task, the task id too. This is
   the enforce-don't-document pairing made greppable: the scanner can then answer *"which FRs
   have zero enforcing code anywhere?"* — at the code level.
4. **Markers must resolve.** A marker citing an FR the spec doesn't define, or a task id
   absent from that spec's `tasks.md`, is DANGLING — the scanner exits non-zero on it.
   Renumbering or deleting an FR/task means sweeping its markers in the same landing.
5. **Manifests and scripts count too.** A manifest implementing a placement FR anchors it in
   a YAML comment — infrastructure is code.

## What the scanner reports

`speckit-trace` rates every FR in every spec:

| Rating | Meaning |
|---|---|
| `FULL` | task reference **and** code anchor **and** enforcing (test-path) anchor |
| `PARTIAL` | at least one of the three links present |
| `INVISIBLE` | no links at all — the requirement exists only in prose |

Plus: all DANGLING markers (exit 1), per-spec totals, and CONST principle anchor counts.

An FR with zero code anchors is not necessarily unbuilt — but it is INVISIBLE to code-level
tooling, the same invisibility class the task-coverage level closes one step up. New FRs
should land with their first anchor (usually the gate's).

## The ratchet

`speckit-trace --write-baseline` freezes today's debt into `.specify/trace-baseline.json`.
After that, frozen debt may only shrink: anything that enters a debt class *after* the
baseline fails the gate. Re-freezing requires an explicit `--write-baseline` — a deliberate
act, never drift. Commit the baseline so the gate reads the same for everyone.

Debt classes: FRs with no task reference (`l1`), FRs with no code anchor (`zero_anchor`),
and tasks marked `[x]` DONE whose id appears nowhere in code (`done_unanchored` — a claim
without a receipt).

## Wiring it into CI

```bash
speckit-trace            # exit 1 on dangling markers or new ratchet debt
speckit-trace --strict   # additionally fail on zero-anchor FRs and blind specs
speckit-trace --json trace.json   # machine-readable matrix for dashboards
```
