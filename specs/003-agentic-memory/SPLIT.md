# What memo builds now vs. what waits for a coordinator

Operator directive 2026-07-30: *"focus on building the things that we know memo can do on its
own, and spec out the things that it's going to need the event coordinator and agent coordinator
for."*

Two external dependencies, both already abstracted behind providers with null implementations
(`memo_conductor_provider` defaults to `atc`, `memo_agent_controller_provider` to
`agents_supervisor`):

- **Event coordinator (ATC)** — inbound events, outbound notifications.
- **Agent coordinator (`agents`)** — tmux access: spawn, respawn, compact, send into a session.

## BUILD NOW — memo alone

| capability | module | state |
|---|---|---|
| corpus, storage, deletion log | `db.py` | done |
| passage index + chunker | `chunking.py`, `passages.py` | built, **not yet wired into search** |
| passage-level retrieval | — | **next** |
| deterministic Layer-2 injection | `injection/` | done; budget bug fixed 2026-07-30 |
| constitutional proposal gate (Principle V) | `auditor/proposals.py` | done, pure DB |
| answer-loop audit | `auditor/answer_loop.py` | done, pure DB |
| TTL reaper | `reaper.py` | done |
| migration + classifier | `migrate/` | done; over-classification fixed 2026-07-30 |
| retrieval bench | `scripts/memo-retrieval-bench` | done, baselines committed |
| MCP tool surface for agents | `main.py` | needs narrowing |
| the agents + method skills | `specs/003-agentic-memory/` | drafted |

Note the agents themselves are **not** a coordinator dependency: they are spawned by the
*calling session*, so memo only has to expose the right MCP tools.

## SPEC ONLY — needs the AGENT coordinator

| capability | module | why |
|---|---|---|
| session shadow: compact / respawn on bloat | `auditor/shadow.py` | a session cannot compact itself; documented. Needs tmux. |
| session liveness / stall detection | `auditor/liveness.py` | needs the session roster |
| auto-memorization loop | — | see `DEFERRED-shadow.md` |
| durable-fact promotion from a live session | — | the real gap; memo-judge's job, never ran |

## SPEC ONLY — needs the EVENT coordinator

| capability | module | why |
|---|---|---|
| reconcile on infra-change events | `reconciler.py` | subscribes to ATC zones |
| corpus-wide sweep on event | `auditor/global_sweep.py` | event-triggered |
| anomaly reporting to the auditor | `mediators/` | outbound ATC notification |
| coordinator-dispatched action log | `auditor/actions.py` | partial — the log itself is standalone |

## Why this split is worth stating

Three things were built and wired to nothing (passage index, shadow auditor, `/admin/access-stats`),
and nothing surfaced it because unwired code does not fail. Sorting by *what it depends on*
rather than *what it does* makes that visible: anything in the SPEC-ONLY columns is expected to
be inert, and anything in BUILD NOW that is inert is a defect.
