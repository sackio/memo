# Quickstart: Memo Renovation v2

Runnable validation guide — the sequence an engineer follows to bring up v2, port the corpus, and run the soak-test that gates cutover per C-08.

Not a full implementation guide; that lives in `tasks.md` (produced by `/speckit-tasks`) and the source itself.

## Prerequisites

- server4 with Docker + docker-compose installed.
- v1 memo running at `http://server4:8000` (untouched throughout).
- Backup snapshot verified: `/mnt/backup/memo/pre-renovation-2026-07-29/memo-server4-live.db.gz` present.
- Access to the `agents` supervisor for AgentController integration (or `MEMO_AGENT_CONTROLLER_PROVIDER=null` for standalone).
- Access to ATC broker at `server4:3030` (or `MEMO_CONDUCTOR_PROVIDER=null` for standalone).
- `OPENROUTER_API_KEY` in `/mnt/nas/data/code/memo/public/.env`.

## Step 1 — Create the v2 worktree

```bash
cd /mnt/nas/data/code/memo
git worktree add ../memo-v2 001-memo-renovation
cd ../memo-v2
```

v2 development happens here; the `main` worktree at `/mnt/nas/data/code/memo` continues to run v1.

## Step 2 — Bring up v2 stack

```bash
# In ../memo-v2 (the v2 worktree)
docker-compose up -d
# Container: memo-v2 on port 8001, volume ./v2-data
```

Verify liveness:

```bash
curl -sf http://server4:8001/health   # → {"status":"ok","version":"2.0.0"}
```

Verify standalone mode (Principle VIII / FR-045):

```bash
MEMO_CONDUCTOR_PROVIDER=null MEMO_AGENT_CONTROLLER_PROVIDER=null \
  docker-compose up -d
# Should come up clean; auditor + integrations disabled, core CRUD still works
curl -sf http://server4:8001/health
```

## Step 3 — Port the v1 corpus

```bash
# In ../memo-v2
scripts/memo-migrate-backfill \
  --v1-url http://server4:8000 \
  --v2-url http://server4:8001 \
  --audit-log /mnt/backup/memo/migration-2026-XX-XX/audit.jsonl
```

Expected: 7,339 memos processed. Audit log records every action per `contracts/migration-cli.md`.

Verify:

```bash
scripts/memo-migrate-verify \
  --v1-url http://server4:8000 \
  --v2-url http://server4:8001
```

Success criteria (SC-005, SC-009):

- All v1 IDs resolvable in v2 (directly or via redirect).
- ≥ 95% of memos in a real class (`≤ 5%` `legacy-unattributed`).
- Zero migration-duplicate clusters in v2 (Matt-Sack collapse verified).

## Step 4 — Wire Claude Code hooks (SERVER4 ONLY initially)

Edit `~/.claude/settings.json` on server4 to add the SessionStart + PostCompact + InstructionsLoaded + SessionEnd hooks pointing at `http://server4:8001/hooks/*` (contracts in `contracts/claude-code-hooks.md`).

**DO NOT wire on office/server5 yet.** The soak-test happens on server4 first; wider hook wiring is part of Phase H cutover, gated on the operator confidence gate.

## Step 5 — Bring up a v2-facing test session

Flip a single non-production session's MCP config from v1 to v2:

```bash
scripts/memo-mcp-flip --session <test-session-name> --to v2
```

Round-trip validation:

- `memo_store` a new memo → verify the storage mediator's response includes canonical tags + inferred class.
- `memo_recall` a known query → verify the retrieval mediator returns a concise answer + citation.
- Verify Layer 2 injection: session prompt at start contains the spec-kit constitution.md content + forcible constitutional memos.

Flip back:

```bash
scripts/memo-mcp-flip --session <test-session-name> --to v1
```

Verify the session resumes cleanly on v1 with no data-loss.

## Step 6 — Run the soak test (C-08 step 3)

```bash
scripts/memo-soak-test \
  --v2-url http://server4:8001 \
  --agent-controller <agents supervisor session id> \
  --duration 4h \
  --report /tmp/memo-soak-report-$(date +%Y%m%d).md
```

The soak-test driver spawns background test agents via the AgentController that:

- Kick-tire the retrieval mediator with a real workload (log-driven query stream from the past 30 days' recall calls).
- Kick-tire the storage mediator with synthetic writes covering all class combinations.
- Trigger auditor paths (frustration signals, hard-rule violations, corpus-rot events).
- Exercise injection hooks across memory-on and memory-off sessions.
- Exercise reconciliation loop with synthetic fact-updates.
- Exercise the answer-loop correction feedback loop (simulated operator corrections + verify ranking-hint promotion after 3 corroborating corrections).

Report addresses:

- Mediator latency (median + P95) — must meet SC-006.
- Classification coverage — must meet SC-009.
- False-positive auditor-triggered compactions — used to tune C-10.
- Anomalies + regressions.

### SC measurement methodology

The soak-test report addresses each of the 10 SCs explicitly:

| SC | Measurement approach |
|---|---|
| **SC-001** (60% drop in "emphatic, repeated" frustration events) | Baseline: 9 documented events in 2026-07-15 → 2026-07-29 window (from Agent B report). Post-cutover: script scans same window's session logs on the auditor's `frustration_signals` finding-log; compare rates. |
| **SC-002** (behavioral-memo violations → 0 or caught mid-session) | Auditor's `mediator.anomaly` event stream counts `kind:behavioral-rule-violation`; measure count/day pre vs. post-cutover. |
| **SC-003** (no verbatim copy-paste of load-bearing corrections) | Grep the rewarm-pin content across the fleet for text-blocks longer than N chars that match verbatim across two or more session guides. Baseline: FLAVOR-DUAL 4× copy per Agent B. Target: 0. |
| **SC-004** (95% correct memo #1 for operator-logistics recall) | Soak-test workload includes a canonical set of 20+ operator-logistics queries with known correct memos; mediator ranking measured on this set. |
| **SC-005** (dupe clusters → 0) | `memo-migrate-verify` checks this after backfill. |
| **SC-006** (mediator latency P95 ≤1.5s, median ≤500 ms) | soak-test driver instruments every `/recall` call and produces a latency histogram in the report. |
| **SC-007** (loop-agent cache-read replay drops 30%) | Fleet-wide instrumentation: sum daily cache-read tokens per loop agent (minders, heartbeats, watchers) via ATC status audit. Compare pre vs. post-cutover 7-day windows. |
| **SC-008** (26 orphan constitution files collapse to single-source) | Out-of-scope for memo alone; measured by `agents`-supervisor's own tooling. Memo verifies its own `.specify/memory/constitution.md` remains single-source across worktrees. |
| **SC-009** (≥95% classified, ≤5% legacy-unattributed) | `memo-migrate-verify` checks this after backfill. |
| **SC-010** (MCP-flip ≤60s with no data loss) | soak-test dry-run of `memo-mcp-flip` on a scratch session; time from flip to first successful `/recall` on the new endpoint; verify pre-flip corpus snapshot equals post-flip corpus snapshot. |

## Step 7 — Operator confidence gate (C-08 step 4)

Send the report to Ben. Ben reviews and decides:

- **Cutover proceeds** (default assumption: waves — quantum first, then assistant, then rest with v1→v2 replication during transition).
- **Iterate on v2** — feedback → fix → re-soak-test.
- **Abandon** — remove worktree, no production impact.

**No cutover strategy is committed until this gate.**

## Step 8 — Cutover (Phase H, only after gate approves)

TBD at gate — shape depends on gate findings. Default (waves) covered by `scripts/memo-mcp-flip --wave <quantum|assistant|rest>` invoking one-way v1→v2 replication for the duration of the wave.

Rollback throughout: `scripts/memo-mcp-flip --wave <...> --to v1` reverses.

## Success

- All contract tests pass.
- All 10 spec success criteria (SC-001..SC-010) measurable green in the soak-test report.
- Ben approves cutover at the confidence gate.
- Post-cutover, `SC-001` (60% drop in "emphatic, repeated" frustration events) tracked in the next 14-day window.
