"""Provider abstractions (Principle VIII / research.md R-08).

Each provider family is an abstract base + one or more concrete adapters + a
`null` adapter for standalone mode, selected by a `MEMO_*_PROVIDER` env var.

Families: `llm` (R-17), and — landing in Phase 5 — `conductor` and
`agent_controller`.
"""
