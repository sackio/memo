# Tasks: Memo Renovation

**Feature**: 001-memo-renovation
**Branch**: `001-memo-renovation`
**Generated**: 2026-07-29
**Inputs**: `spec.md` (8 user stories US1-US8, 54 FRs, 10 SCs, 10 resolved clarifications), `plan.md` (Phases A-H, ~8-12 h dev time), `research.md` (16 decisions), `data-model.md`, `contracts/` (11 contracts), `quickstart.md`, `.specify/memory/constitution.md` (v1.3.0, 8 principles).

## Organization note

This tasks.md deviates from the pure per-user-story template on purpose:
memo's 8 user stories are DEEPLY INTERCONNECTED (US1 forcible-injection
needs US2 mediators which need Phase A schema), so a strict-per-story
split would produce N duplicate "create schema" tasks. Instead, tasks
are grouped by **plan.md Phase A-H** (the actual dependency chain), with
`[US#]` labels marking which user story a task primarily serves. Every
FR is covered by at least one task; the phase gate at end of each phase
runs `speckit-trace --require-full <FR-list>` to prove it.

## Marker convention (READ THIS ONCE, APPLIES TO EVERY TASK)

Per the speckit session's guidance:

- **Every implementation task**: add a `001/FR-XXX` marker at the
  NARROWEST OWNING UNIT — a fn/class header comment or docstring — in
  `src/memo/...`. Module-level docstring is a FALLBACK only when the FR
  genuinely spans the whole module. One anchor per owning unit — not
  every line. (Rationale from speckit review 2026-07-29: an FR anchored
  at the docstring of a 900-line module tells a reader the file, not
  the code, and the anchor stops being reviewable.)
- **Every test task**: add the same marker `001/FR-XXX` at the top of
  the test file. **Path decides gate-ness, not the verb** — anything
  under `tests/` is classified as ENFORCING by `test_path_hints`. So
  "enforces" is readability, not load-bearing.
- **Fixture strings**: any test line that mentions a marker string as
  data (e.g. `assert "001/FR-016" in output`) must have
  `# speckit-trace: ignore` on the same line, else the scanner counts
  it as a false anchor. Suppression count prints on every run — never
  silent.
- **NEVER run `speckit-trace --write-baseline`.** Greenfield rule from
  speckit — freezing zero-anchor as accepted floor defeats the gate.
- **Phase gate**: at end of each phase, `cd ../memo-v2 && speckit-trace
  --require-full <FR-list-for-this-phase>` must exit 0. Any
  PARTIAL/INVISIBLE/unknown FR fails the phase. **Do NOT combine with
  `--strict` per-phase** — `--strict` is repo-wide and would fail every
  phase gate for later-phase FRs that haven't been built yet. Default
  gating already catches L1 misses + dangling markers, which is what
  per-phase needs. `--strict` is reserved for the Final Phase T152
  where "every FR anchored" is finally a true statement. (Verified by
  speckit review 2026-07-29 with a control experiment.)
- **Run speckit-trace INSIDE the v2 worktree**, not the main worktree —
  path confinement means it can't reach across, and running it in the
  wrong worktree gives a clean-looking wrong answer (zero anchors when
  the code exists elsewhere).

Tasks marked `[P]` can run in parallel with each other (different files, no shared dependencies).

---

## Phase 1 — Setup (worktree + tooling)

- [X] T001 Create v2 git worktree at `../memo-v2` off branch `001-memo-renovation` — `cd /mnt/nas/data/code/memo && git worktree add ../memo-v2 001-memo-renovation`
- [X] T002 [P] Copy `pyproject.toml`, `docker-compose.yml`, `.env.example` from v1 to `../memo-v2/`; bump `pyproject.toml` version to `2.0.0-alpha1`; rename container to `memo-v2`; change host port to ~~8001~~ **8091** (see deviation note); change data volume path to `./v2-data`
- [X] T003 [P] Verify `speckit-trace --version` on server4 and `cd ../memo-v2 && speckit-trace` produces the PRE-TASKS "not rated" output (baseline sanity)
- [X] T004 [P] Add `../memo-v2/src/memo/__init__.py` with `__version__ = "2.0.0-alpha1"` header comment for the whole package (no FR marker yet — just structure)

**T002 deviation (2026-07-29)**: host port is **8091**, not the 8001 this task
originally specified — port 8001 on server4 is already held by an unrelated
development service (its `/health` returns `{"status":"ok","environment":
"development"}`, which is NOT memo). Binding v2 to 8001 would have collided.
`docker-compose.yml` uses `PORT: ${MEMO_V2_PORT:-8091}` with `network_mode:
host`. Every downstream reference to the v2 port must read 8091. Also note the
data volume is the named volume `memo_v2_data` (→ `/data`) rather than a
`./v2-data` bind mount, to match v1's volume style.

**Phase 1 gate**: ✅ MET (verified 2026-07-29) — `memo-v2` container up + healthy;
`curl -sf http://localhost:8091/health` → `{"status":"ok"}`. No FR markers yet —
Phase 1 is pure setup.

---

## Phase 2 (== plan Phase A) — Schema + core CRUD

Foundational; blocks all user stories.

- [X] T010 Author `../memo-v2/migrations/001_v2_schema.sql` — additive columns on `documents` (`class`, `injection_mode`, `scope`, `provenance`, `valid_from`, `valid_until`, `expires_at`, `time_scope`, `reopenability`, `derived_from`, `constitution_meta`) per data-model.md. Marker `001/FR-001 001/FR-002 001/FR-005 001/FR-006 001/FR-007 001/FR-008 001/FR-009` in header comment.
- [X] T011 Author `../memo-v2/migrations/002_bi_temporal_indexes.sql` — indexes `documents_current_idx`, `documents_class_scope_idx`, `documents_expires_idx`, `documents_time_scope_idx`. Marker `001/FR-002` in header.
- [X] T012 [P] Author `../memo-v2/migrations/003_seed_canonical_tags.sql` — retire `hard-rule`/`ben-hard-rule`/`behavioral-rule` fragmentation to a single canonical vocabulary (C44). Marker `001/FR-001` in header.
- [X] T013 [P] Author `../memo-v2/migrations/004_supersede_edges.sql` — new `supersede_edges` table per data-model.md. Marker `001/FR-002 001/FR-003` in header.
- [X] T014 [P] Author `../memo-v2/migrations/005_mediator_audit_log.sql` — new `mediator_audit_log` table. Marker `001/FR-014 001/FR-015f 001/FR-035` in header.
- [X] T015 [P] Author `../memo-v2/migrations/006_injection_set_cache.sql` — new `injection_set_cache` table. Marker `001/FR-016` in header.
- [X] T016 [P] Author `../memo-v2/migrations/007_constitution_proposals.sql` — new `constitution_proposals` table. Marker `001/FR-023` in header.
- [X] T017 [P] Author `../memo-v2/migrations/008_session_guide_cache.sql` — new `SESSION_GUIDE_cache` table. Marker `001/FR-016` in header.
- [X] T018 Write `../memo-v2/src/memo/models.py` — Pydantic v2 models per data-model.md (Memo, Provenance nested types, TimeScope, Reopenability, ConstitutionMeta, InjectionSet, TransclusionResolution). Module docstring marker `001/FR-001 001/FR-002 001/FR-004 001/FR-005 001/FR-006 001/FR-007 001/FR-008 001/FR-009`.
- [X] T019 Extend `../memo-v2/src/memo/db.py` from v1 — add bi-temporal helpers: `get_current(id)`, `get_as_of(id, t)`, `supersede(old_id, new_memo, actor, reason, operator_directive_ref)`. Marker `001/FR-002 001/FR-003` on each helper's docstring.
- [X] T020 Write `../memo-v2/src/memo/repositories/documents.py` — repository abstraction wrapping db.py raw operations, so future Postgres swap doesn't touch call sites (per R-03). Marker `001/FR-001` on module docstring.
- [X] T021 [P] Write `../memo-v2/src/memo/reaper.py` — 5-minute background task sweeping rows with `expires_at < now`. Marker `001/FR-007` on module docstring.
- [X] T022 [P] Add `POST /supersede` endpoint in `../memo-v2/src/memo/main.py` per FR-003. Marker `001/FR-003` on the endpoint function docstring.
- [X] T023 Write `../memo-v2/tests/unit/test_models.py` — validate every Memo class + special-field-requirement rules from data-model.md §Validation Rules. Marker `001/FR-001 001/FR-005 001/FR-006 001/FR-007 001/FR-008 001/FR-009` in file docstring. Any fixture line with a marker string gets `# speckit-trace: ignore`.
- [X] T024 Write `../memo-v2/tests/unit/test_db_bi_temporal.py` — supersede, as_of, current-filter round-trips. Marker `001/FR-002 001/FR-003` in file docstring.
- [X] T025 Write `../memo-v2/tests/unit/test_reaper.py` — expires_at sweep behavior. Marker `001/FR-007` in file docstring.

**Phase 2 gate**:

```bash
cd ../memo-v2 && speckit-trace --require-full \
  001/FR-001,001/FR-002,001/FR-003,001/FR-004,001/FR-005,001/FR-006,001/FR-007,001/FR-008,001/FR-009
```

Must exit 0. On PARTIAL/INVISIBLE, fix before proceeding to Phase 3.

**Phase 2 gate: ✅ PASS (2026-07-29)** — FR-001..FR-009 all FULL, 0 dangling
markers, 0 L1 misses, exit 0. Full suite: **89 passed**, run in docker —
`docker compose run --rm test` (see note 7; never run tests on the host).

### Phase 2 implementation notes / deviations

1. **T023 marker set widened.** T023 specified FR-001/005/006/007/008/009, but
   the gate requires FR-001..FR-009 FULL and **FR-004 (provenance) had no
   enforcing test in any Phase 2 task** — the gate failed it as PARTIAL. FR-004
   and FR-002 are both genuinely exercised by `test_models.py`, so its marker
   set is FR-001/002/004/005/006/007/008/009. Later phases adding provenance
   behavior should keep an enforcing test anchored to FR-004.
2. **`valid_from` write-path bug fixed (found, not specified).** Migration 001
   adds `valid_from REAL NOT NULL DEFAULT 0`, and `_sync_store` did not set the
   column — so every NEW write silently took 0 and looked valid from the epoch,
   which breaks `get_as_of`. Confirmed empirically: the only row in the fresh v2
   DB had `valid_from=0`. `_sync_store` now sets it explicitly; pinned by
   `test_new_write_sets_valid_from_to_now`.
3. **Supersession keeps ids immutable.** Per data-model.md each version is its
   own `documents` row with its own uuid, linked via `supersede_edges`. So
   `get_current`/`get_as_of` RESOLVE THE CHAIN rather than doing a bare
   `WHERE id = ?` — a caller holding a superseded id still gets the right
   answer instead of None. Chain walks carry a seen-set because
   `supersede_edges` has no FK constraints and a cycle would otherwise hang a
   request (`test_cyclic_edges_do_not_hang`).
4. **As-of window is half-open** (`valid_from <= t < valid_until`), so the
   supersession instant belongs to the new version only — never to both, never
   to neither.
5. **Two extra read endpoints beyond T022.** `GET /documents/{id}/current` and
   `GET /documents/{id}/as-of?t=` were added alongside `POST /supersede`; FR-002
   is a storage requirement with no read surface of its own, and the bi-temporal
   helpers were otherwise unreachable over HTTP.
6. **`src/memo/__init__.py` docstring must not contain a literal marker.** It
   had an illustrative `001/FR-XXX`, which the scanner counted as a real anchor
   and reported as dangling → gate FAIL. Reworded to describe the convention
   without spelling a marker.
7. **Tests run IN DOCKER. NEVER on the host. RESOLVED.**

   ```bash
   cd ../memo-v2 && docker compose run --rm test                       # whole suite
   cd ../memo-v2 && docker compose run --rm test tests/unit -q         # subset
   cd ../memo-v2 && docker compose run --rm --build test               # after editing src/
   ```

   The Dockerfile sets `ENTRYPOINT ["python","-m","pytest"]` with
   `CMD ["tests/","-q"]` precisely so the subset form works. `docker compose
   run <service> <args>` REPLACES the CMD rather than appending to it, so with a
   bare CMD any pytest flag is treated as the executable —
   `exec: "-q": executable file not found`. (My own first draft of these docs
   had that bug.)

   **89 passed, 0 failed.** This unblocks the Phase 3 mediator contract tests,
   which need `TestClient`.

   The host cannot run this suite: the project requires Python >=3.12 (host is
   3.10), and the host's `fastapi`/`starlette` versions are mismatched badly
   enough that `import memo.main` raises
   `TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`
   inside FastAPI's own constructor — so `tests/test_leak_guard.py` could not even
   collect, and the suite had effectively never run.

   ⚠️ **A host run does not fail loudly — it silently reports a SMALLER passing
   number.** Every test that imports the app is dropped at collection, so the
   host reported "73 passed" while the real suite is 89. Treat any test count
   that did not come out of docker as untrustworthy. (Operator correction,
   2026-07-29: "you should be building all this in docker / docker compose.")

   Implemented as a `dev` optional-dependency group (pytest, pytest-asyncio,
   httpx-for-TestClient), a `test` stage in the Dockerfile, and a profile-gated
   `test` service in `docker-compose.yml`. The service uses a throwaway /tmp DB
   and does NOT mount `memo_v2_data`, so a test run cannot touch the real store.
   The `test` profile keeps it out of `docker compose up` and out of
   orphan-cleanup. Things to know:
   * The `test` stage is deliberately NOT the last stage in the Dockerfile —
     docker's default target is the final stage, and `docker compose build` must
     keep producing the runtime image. Verified: the runtime image has no pytest.
   * The test stage installs the package, so tests import from
     `site-packages/memo`, NOT `src/`. `docker compose run` reuses the existing
     image, so after editing `src/` you MUST rebuild or you will be testing stale
     code (this cost a confusing cycle):

     ```bash
     docker compose run --rm --build test
     ```

   `[tool.pytest.ini_options]` pins `asyncio_mode = "strict"` so the mode cannot
   drift and silently disable async fixture teardown (see note 9).
7a. **Pre-existing test-data bug fixed in `tests/test_leak_guard.py`.** Once the
   suite could actually run, `test_discussion_of_bug_mid_body_no_tags` failed its
   own setup assertion: the guard only inspects `content[-400:]`, and the fixture
   was 416 chars with the marker ending at index 59 — i.e. inside the tail window
   the test intended it to be outside of. Padded the fixture past 459 chars with
   the arithmetic documented inline. The guard itself was correct; only the
   fixture was mis-sized.
8. **`tests/unit/conftest.py` isolation is `autouse` on purpose.**
   `db._resolve_path` ignores its `db_path` argument (single-global refactor),
   so a test that merely passes a temp path would still write the REAL store.
   Isolation must not depend on a test remembering to opt in. Verified: after a
   full run, `~/.memo/memo.db` does not exist.
9. **Async fixtures must use `@pytest_asyncio.fixture`.** pytest-asyncio runs in
   strict mode here; a plain `@pytest.fixture` async generator is never awaited
   and silently becomes a no-op (so the reaper-task cleanup did not run, despite
   green tests). pytest 9 will make this a hard error.
10. **Live e2e verified against the running container** (port 8091), then its
    rows were deleted: create → supersede → `get_current` via the STALE id
    returns the new content → `as-of` midpoint returns the OLD content → `as-of`
    at the supersession instant returns the NEW one → double-supersede 409 →
    unknown id 404. The reaper logs `TTL reaper started — sweeping every 300s`
    on boot, and v1 on :8000 was unaffected throughout.
11. **⚠️ Phase 7 input — one row in the v2 alpha DB has `valid_from = 0`**
    (id `2715c4ce…`, content "test v2 memo"). It was written by a smoke test
    AFTER migration 001's one-shot `UPDATE ... WHERE valid_from = 0` backfill had
    already run and been marked applied, so nothing re-backfilled it. **Left in
    place deliberately** — it is a genuine specimen of the pre-fix bug, so the
    Phase 7 migration script should sweep `valid_from = 0` rows and can use this
    row as its fixture. Do not hand-patch it before then.

---

## Phase 3 (== plan Phase B) — Both mediators [US2]

**R-17 amendment (operator directive 2026-07-29)**: the mediators' generative
LLM calls are served by an **interactive Claude Code session** (`memo-llm`),
never by a per-token API and never via `claude -p` — see research.md R-17 for
the full decision and rationale.

Phase 3 therefore builds against the `LLMProvider` INTERFACE with a
deterministic **null adapter** (T029a below), so the mediators land complete
and fully testable without the transport existing. The concrete
`claude_session` adapter lands in **Phase 5** (T085a) with the other provider
adapters. Contract-test note: with the null adapter, LLM-fallback paths assert
the DEGRADED behavior — that is the correct, specified behavior, not a stub.

Everywhere the mediators touch the LLM they must **degrade, never block**:
on unavailability recall returns its search-only answer with an `anomalies`
entry and store writes-new + flags the auditor. Neither may fail the caller.

- [X] T029a [US2] Write `../memo-v2/src/memo/providers/llm/base.py` — abstract `LLMProvider` (`complete(prompt, *, budget_tokens, timeout_s) -> str | None`, returning None on unavailability rather than raising) plus `../memo-v2/src/memo/providers/llm/null.py` — `NullLLMProvider` that always reports unavailable, so every caller exercises the degrade path. Wire `MEMO_LLM_PROVIDER` (default `null` until Phase 5) in config. Marker `001/FR-015` on the base class docstring.
- [X] T030 [US2] Write `../memo-v2/src/memo/mediators/filters.py` — filter chain strategy classes: `DedupFilter` (migration-cluster collapse per C-06), `BiTemporalFilter` (`valid_until IS NULL` unless `as_of` set), `RecencyBoost`, `TagClassBoost` (per FR-013), `ScopeFilter`. Each class's docstring has `001/FR-011 001/FR-012 001/FR-013` marker.
- [X] T031 [US2] Write `../memo-v2/src/memo/mediators/recall.py` — retrieval mediator per contracts/mediator-recall.md. Wires filter chain; LLM-fallback trigger on N candidates or conflict (default N=15). Module docstring marker `001/FR-010 001/FR-011 001/FR-012 001/FR-013 001/FR-014 001/FR-015`.
- [X] T032 [US2] Write `../memo-v2/src/memo/clarify.py` — synchronous clarification round-trip helper for the storage mediator (FR-015d). Marker `001/FR-015d`.
- [X] T033 [US2] Write `../memo-v2/src/memo/mediators/store.py` — storage mediator per contracts/mediator-store.md. Reconcile-before-write (merge/supersede/split/reject/write-new); canonical tag/class inference; clarify round-trip; refute-fact rejection with operator-directive-ref requirement. Module docstring marker `001/FR-015a 001/FR-015b 001/FR-015c 001/FR-015d 001/FR-015e 001/FR-015f 001/FR-015g`.
- [X] T034 [US2] Add `POST /recall` endpoint in main.py; delegates to recall mediator. Marker `001/FR-010`.
- [ ] T035 [US2] Refactor existing `POST /store` (and MCP `memo_store` tool) in main.py to route through the storage mediator. Preserve v1 tool name for back-compat. Marker `001/FR-015a`.
- [ ] T036 [US2] [P] Refactor `../memo-v2/src/memo/auto_store.py` to route through storage mediator instead of raw insert. Marker `001/FR-015a`. **ALSO (R-17, operator clarification 2026-07-29): move auto_store's `openai/gpt-4o-mini` dedup call off OpenRouter onto the `LLMProvider`.** This is memo's one pre-existing generative caller and fires on every hook-triggered store, so it is in scope like any other LLM use — the earlier "leave it for now" note is superseded. After this task, NO generative OpenRouter call should remain: verify with a grep for `auto_store_model` / chat-completion usage. Embeddings stay on OpenRouter (R-05) and are unaffected. Note auto_store must tolerate a None completion (degrade to write-new) like every other caller.
- [ ] T037 [US2] Write `../memo-v2/tests/contract/test_mediator_recall.py` — one test per contract Response section (SUCCESS/NO-RESULTS/ANOMALY/error). Marker `001/FR-010 001/FR-011 001/FR-012 001/FR-013 001/FR-014 001/FR-015`.
- [X] T038 [US2] Write `../memo-v2/tests/contract/test_mediator_store.py` — one test per contract Response section (MERGE/WRITE-NEW/SUPERSEDE/CLARIFY/REJECT/SPLIT). Marker `001/FR-015a 001/FR-015b 001/FR-015c 001/FR-015d 001/FR-015e 001/FR-015f 001/FR-015g`.
- [ ] T039 [US2] [P] Write `../memo-v2/tests/integration/test_dedup_collapse.py` — reproduce the Matt-Sack `0c55a9a3/c664f4a1/98efbda5` scenario (canonical + 2 duplicates); assert retrieval returns only canonical. Marker `001/FR-012`.
- [ ] T040 [US2] [P] Write `../memo-v2/tests/integration/test_recall_parking.py` — reproduce 7/26 parking-recall scenario. Assert July memo (when it exists) ranks #1 over May SF memo. Marker `001/FR-013`.

**Phase 3 gate**:

```bash
cd ../memo-v2 && speckit-trace --require-full \
  001/FR-010,001/FR-011,001/FR-012,001/FR-013,001/FR-014,001/FR-015,\
001/FR-015a,001/FR-015b,001/FR-015c,001/FR-015d,001/FR-015e,001/FR-015f,001/FR-015g
```

---

## Phase 4 (== plan Phase C) — Layer 2 injection + hooks [US1] [US3] [US4]

- [ ] T050 [US1] Write `../memo-v2/src/memo/injection/posture.py` — detects `CLAUDE_CODE_DISABLE_AUTO_MEMORY` per-session via `/proc/<pid>/environ` with roster fallback. Module docstring marker `001/FR-017 001/FR-018`.
- [ ] T051 [US1] Write `../memo-v2/src/memo/injection/guides.py` — SESSION_GUIDE resolver (4 conventions per Agent H; DM to `agents` supervisor OR parses `~/scripts/agents` for `SESSION_GUIDE` array; caches to `SESSION_GUIDE_cache` table). Marker `001/FR-016 001/FR-017`.
- [ ] T052 [US1] Write `../memo-v2/src/memo/injection/transclude.py` — scans given text for `memo:<uuid>` references (regex + validation) and returns resolved memo content. Marker `001/FR-016 001/FR-017`.
- [ ] T053 [US1] Write `../memo-v2/src/memo/injection/set.py` — main `InjectionSet` builder per contracts/injection-set.md. Assembles: spec-kit constitution.md if present + forcible-constitutional/current-focus memos matching scope + time-scoped active memos + transclusions. Enforces 5k token budget (C-02) with drop-priority order. Module docstring marker `001/FR-016 001/FR-017 001/FR-018 001/FR-019 001/FR-020`.
- [ ] T054 [US1] Add `GET /injection-set` endpoint in main.py. Marker `001/FR-016`.
- [ ] T055 [US3] [P] Extend injection/set.py `_current_focus()` to include `class=time-scoped` memos where current time in `[start, end]`. Marker `001/FR-005` (time_scope field) and `001/FR-020` (current-focus).
- [ ] T056 [US1] Write `../memo-v2/src/memo/hooks/session_start.py` — `POST /hooks/session-start` per contracts/claude-code-hooks.md. Marker `001/FR-017`.
- [ ] T057 [US1] Write `../memo-v2/src/memo/hooks/post_compact.py` — `POST /hooks/post-compact`. Replaces the atc-precompact-beacon.py subagent dance (C58 / FR-036). Marker `001/FR-018 001/FR-036`.
- [ ] T058 [US1] Write `../memo-v2/src/memo/hooks/instructions_loaded.py` — `POST /hooks/instructions-loaded`. Scans passed instruction_files content for `memo:<uuid>` references and returns resolved additionalContext. Marker `001/FR-017`.
- [ ] T059 [US1] Write `../memo-v2/src/memo/hooks/session_end.py` — `POST /hooks/session-end`. Triggers auditor final-sweep (async — no additionalContext). Marker `001/FR-025`.
- [ ] T060 [US4] [P] Write `../memo-v2/src/memo/log_queries.py` — intelligent Claude Code log query tool per contracts/log-queries.md. `command grep -m<max>` discipline. Marker `001/FR-033`.
- [ ] T061 [US1] Add `POST /flush` endpoint in main.py per contracts/flush.md. Marker `001/FR-034`.
- [ ] T062 [US1] Write `../memo-v2/tests/contract/test_injection_set.py` — request/response shapes; token-budget drop order. Marker `001/FR-016 001/FR-017 001/FR-018 001/FR-019 001/FR-020`.
- [ ] T063 [US1] Write `../memo-v2/tests/contract/test_claude_code_hooks.py` — one test per hook endpoint. Marker `001/FR-017 001/FR-018 001/FR-036`.
- [ ] T064 [US3] [P] Write `../memo-v2/tests/integration/test_time_scoped_autopin.py` — memo with `time_scope: {start, end}` is in injection-set during the window, absent after `end`. Marker `001/FR-005`.
- [ ] T065 [US1] [P] Write `../memo-v2/tests/integration/test_spec_kit_constitution_injection.py` — session in cwd that contains `.specify/memory/constitution.md` gets it in `additionalContext`. Marker `001/FR-016`.
- [ ] T066 [US1] [P] Write `../memo-v2/tests/integration/test_transclusion.py` — CLAUDE.md with `memo:<uuid>` reference gets resolved on InstructionsLoaded. Marker `001/FR-017`.
- [ ] T067 [US1] [P] Write `../memo-v2/tests/integration/test_memory_posture_detection.py` — memory-on session vs. memory-off session receive different-sized injection sets. Marker `001/FR-017`.
- [ ] T068 [US4] [P] Write `../memo-v2/tests/contract/test_log_queries.py`. Marker `001/FR-033`.
- [ ] T069 [US1] [P] Write `../memo-v2/tests/contract/test_flush.py`. Marker `001/FR-034`.

**Phase 4 gate**:

```bash
cd ../memo-v2 && speckit-trace --require-full \
  001/FR-016,001/FR-017,001/FR-018,001/FR-019,001/FR-020,\
001/FR-033,001/FR-034,001/FR-036
```

---

## Phase 5 (== plan Phase D) — Provider abstractions + adapters [US8]

- [ ] T080 [US8] Write `../memo-v2/src/memo/providers/conductor/base.py` — abstract `Conductor` class with push/pull/scheduled/event-trigger/bridge-event method signatures. Marker `001/FR-041 001/FR-042 001/FR-042a 001/FR-046`.
- [ ] T081 [US8] Write `../memo-v2/src/memo/providers/conductor/atc.py` — concrete ATC adapter using HTTP `POST /messages` + `POST /beacons` + `POST /triggers`. Marker `001/FR-041 001/FR-042`.
- [ ] T082 [US8] [P] Write `../memo-v2/src/memo/providers/agent_controller/base.py` — abstract `AgentController` with spawn/respawn/clear/change-model/compact/interrupt/inject signatures. Marker `001/FR-043 001/FR-046`.
- [ ] T083 [US8] Write `../memo-v2/src/memo/providers/agent_controller/agents_supervisor.py` — concrete `agents`-supervisor adapter. Marker `001/FR-043`.
- [ ] T084 [US8] [P] Write `../memo-v2/src/memo/providers/null.py` — `NullConductor` + `NullAgentController` for standalone mode. Marker `001/FR-045`.
- [ ] T085 [US8] Wire provider selection in main.py — `MEMO_CONDUCTOR_PROVIDER` + `MEMO_AGENT_CONTROLLER_PROVIDER` env vars, default `atc`/`agents_supervisor`, `null` for standalone. Marker `001/FR-045`.
- [ ] T085a [US8] Write `../memo-v2/src/memo/providers/llm/claude_session.py` — concrete `LLMProvider` that serves inference from the **interactive `memo-llm` Claude Code session** over ATC (request/response with a correlation id + 10s soft timeout). Per R-17: **MUST NOT shell out to `claude -p`** — that is billed API usage and is the exact thing this design avoids; an interactive session rides the existing Max subscription. On unavailability return None (never raise) so callers degrade, AND DM the `agents` supervisor to respawn `memo-llm` — **rate-limited to one notify per outage, not per failed call**, or a dead session plus fleet-wide memo traffic floods the supervisor (the thundering-herd failure in CLAUDE.md §5). Switch `MEMO_LLM_PROVIDER` default from `null` to `claude_session`. Marker `001/FR-045`.
- [ ] T085b [US8] Write `../memo-v2/tests/contract/test_llm_provider.py` — null adapter reports unavailable; `claude_session` returns None (never raises) when the session is down; supervisor escalation fires exactly ONCE per outage across N consecutive failures and re-arms only after a recovery. Marker `001/FR-045`.
- [ ] T086 [US8] Add `POST /events` endpoint per contracts/conductor-pull.md. Routes each event kind to its handler. Marker `001/FR-042 001/FR-042a`.
- [ ] T087 [US8] Write `../memo-v2/tests/contract/test_conductor_push.py`. Marker `001/FR-041 001/FR-046`.
- [ ] T088 [US8] Write `../memo-v2/tests/contract/test_conductor_pull.py`. Marker `001/FR-042 001/FR-042a`.
- [ ] T089 [US8] Write `../memo-v2/tests/contract/test_agent_controller.py`. Marker `001/FR-043`.
- [ ] T090 [US8] [P] Write `../memo-v2/tests/integration/test_standalone_mode.py` — start memo with both providers set to `null`; verify CRUD + mediators still work; verify integration features WARN-log the "would have fired" note. Marker `001/FR-045`.

**Phase 5 gate**:

```bash
cd ../memo-v2 && speckit-trace --require-full \
  001/FR-041,001/FR-042,001/FR-042a,001/FR-043,001/FR-044,001/FR-045,001/FR-046
```

---

## Phase 6 (== plan Phase E) — Auditor [US6]

- [ ] T100 [US6] Write `../memo-v2/src/memo/auditor/proposals.py` — `constitution-proposal` writer + `POST /constitution/propose` per contracts/constitution-proposals.md. Marker `001/FR-023`.
- [ ] T101 [US6] Add `POST /constitution/resolve` + `GET /constitution/proposals` in main.py per contracts/constitution-proposals.md. Marker `001/FR-023`.
- [ ] T102 [US6] Write `../memo-v2/src/memo/auditor/shadow.py` — per-session shadow auditor: long-running Conductor-subscribed watcher scoped to an agent-family. Subscribes to session's zone; observes transcript growth, memo query patterns, incoming DMs; classifies frustration signals; can write proposals + request AgentController ops. Module docstring marker `001/FR-021 001/FR-022`.
- [ ] T103 [US6] Write `../memo-v2/src/memo/auditor/liveness.py` — content-based liveness monitor mirroring stale-guide-detector (C70). Marker `001/FR-025`.
- [ ] T104 [US6] Write `../memo-v2/src/memo/auditor/global_sweep.py` — cron-driven global auditor: polices shadow auditors, synthesizes cross-session patterns, reaps `ephemeral-flush` past TTL (belt-and-suspenders with the 5-min reaper), coalesces long supersession chains. Marker `001/FR-024`.
- [ ] T105 [US6] Wire auditor bootstrap into main.py — starts shadow-auditor tasks for each active agent-family in the SESSION_GUIDE roster; registers global-auditor scheduled trigger with Conductor. Marker `001/FR-021 001/FR-024`.
- [ ] T106 [US6] Wire operator-override handling — `POST /events` handler for `operator.directive` events routes to auditor for classification (fact-update? override?). Marker `001/FR-026 001/FR-029`.
- [ ] T107 [US6] Write `../memo-v2/tests/contract/test_constitution_proposals.py`. Marker `001/FR-023`.
- [ ] T108 [US6] [P] Write `../memo-v2/tests/integration/test_shadow_auditor.py` — spawn a mock session; auditor observes; assert proposal fired for a synthetic anti-pattern violation. Marker `001/FR-021 001/FR-022`.
- [ ] T109 [US6] [P] Write `../memo-v2/tests/integration/test_auditor_compaction_trigger.py` — auditor detects composite bloat threshold (C-10: transcript >2.5 MB, cache-read >20M/day, >120 turns) and calls `AgentController.compact()`. Marker `001/FR-022 001/FR-037`.
- [ ] T110 [US6] [P] Write `../memo-v2/tests/integration/test_answer_loop_correction.py` — operator correction → immediate finding log; 3 corroborating in 24h → auto-promote hint. Marker `001/FR-035`.

**Phase 6 gate**:

```bash
cd ../memo-v2 && speckit-trace --require-full \
  001/FR-021,001/FR-022,001/FR-023,001/FR-024,001/FR-025,001/FR-026,\
001/FR-035,001/FR-037
```

Also cover FR-027..FR-032 (reconciliation FRs — many are implicit in mediator + auditor):
- [ ] T111 Write `../memo-v2/src/memo/reconciler.py` — event-triggered reconciliation on ATC `infra.change` events (FR-031); real-time reconcile hook on `class=fact` write path (FR-030). Marker `001/FR-027 001/FR-028 001/FR-029 001/FR-030 001/FR-031 001/FR-032`.
- [ ] T112 Write `../memo-v2/tests/contract/test_reconciliation.py`. Marker `001/FR-027 001/FR-028 001/FR-029 001/FR-030 001/FR-031 001/FR-032`.

**Phase 6b gate** (reconciliation):

```bash
cd ../memo-v2 && speckit-trace --require-full \
  001/FR-027,001/FR-028,001/FR-029,001/FR-030,001/FR-031,001/FR-032
```

---

## Phase 7 (== plan Phase F) — Migration script [US5] [US7]

- [ ] T120 [US7] Write `../memo-v2/scripts/memo-migrate-backfill` per contracts/migration-cli.md — the full per-memo pipeline (fetch/classify/retag/provenance-link/split/merge/redirect/set-bi-temporal/write). Includes per-class rules from migration-cli.md §"Per-class backfill rules". Marker `001/FR-039`.
- [ ] T121 [US7] Write `../memo-v2/scripts/memo-migrate-verify` — post-check per contracts/migration-cli.md §"Post-migration verification". Marker `001/FR-039`.
- [ ] T122 [US5] [P] Extend backfill script to preserve v1 bi-temporal semantics — set `valid_from = v1.created_at` and `valid_until = NULL` for every migrated memo. Marker `001/FR-002`.
- [ ] T123 [US7] Write `../memo-v2/tests/integration/test_migration_backfill.py` — migrate a synthetic 200-memo corpus covering every v2 class; assert all migrated with correct class assignments + provenance where inferrable + duplicates collapsed. Marker `001/FR-039`.
- [ ] T124 [US7] [P] Write `../memo-v2/tests/integration/test_migration_matt_sack_cluster.py` — reproduce the Matt-Sack duplicate cluster case; verify collapse to single canonical + redirects for the other IDs. Marker `001/FR-012 001/FR-039`.

**Phase 7 gate**:

```bash
cd ../memo-v2 && speckit-trace --require-full \
  001/FR-038,001/FR-039,001/FR-040
```

FR-038 (deployable in separate worktree) + FR-040 (reversibility) satisfied by the Phase 1 worktree setup + this phase's audit-log; add markers for those to the migration scripts' module docstrings.

---

## Phase 8 (== plan Phase G) — Soak test (RUNTIME PHASE — not dev)

- [ ] T130 [US8] Author `../memo-v2/scripts/memo-soak-test` — driver script that spawns background test agents via AgentController with a synthetic + real-workload query stream against the ported v2 corpus. Instrumentation captures per-SC metrics per quickstart.md §"SC measurement methodology". Marker `001/FR-035` (uses the mediator-audit-log).
- [ ] T131 [US7] Run the full backfill: `scripts/memo-migrate-backfill --v1-url http://server4:8000 --v2-url http://server4:8001 --audit-log /mnt/backup/memo/migration-YYYY-MM-DD/audit.jsonl` — 7339 memos processed. Deliverable: SC-005 (dupe clusters → 0) + SC-009 (≥95% classified) measurable.
- [ ] T132 [US7] Run `scripts/memo-migrate-verify` — must exit 0 or investigate failing checks.
- [ ] T133 Wire Claude Code hooks on SERVER4 ONLY (edit `~/.claude/settings.json` per contracts/claude-code-hooks.md). DO NOT wire on office/server5 yet.
- [ ] T134 Flip a single non-production test session to v2 MCP via `scripts/memo-mcp-flip --session <test> --to v2`; round-trip validate (store, recall, injection). Flip back; verify clean resume on v1.
- [ ] T135 [US8] Kick off soak test: `scripts/memo-soak-test --duration <operator-chosen>` — writes report to `/tmp/memo-soak-report-<date>.md`.
- [ ] T136 Send soak report to Ben (DM slack:U0NGEHS2J with the report body). Confidence-gate decision belongs to operator; no automated pass/fail here.

**Phase 8 gate**: OPERATOR CONFIDENCE GATE (C-08 step 4). Not a `speckit-trace` gate — this is a human review of the soak report. No cutover shape is committed until Ben approves.

---

## Phase 9 (== plan Phase H) — Cutover (DEFERRED behind operator approval)

- [ ] T140 (BLOCKED until T136 approved) Author `../memo-v2/scripts/memo-mcp-flip --wave <quantum|assistant|rest>` with one-way v1→v2 replication during transition. Shape chosen at gate.
- [ ] T141 (BLOCKED) Execute cutover wave 1 (default: quantum).
- [ ] T142 (BLOCKED) Execute cutover wave 2 (default: assistant).
- [ ] T143 (BLOCKED) Execute cutover wave 3 (default: rest of fleet).
- [ ] T144 (BLOCKED) Post-cutover monitor: 14-day tracking of SC-001 (frustration event drop) + SC-002 (behavioral-memo violations); report to Ben.

**Phase 9 gate**: Not automated. Ben decides both go/no-go and shape.

---

## Final Phase — Polish & cross-cutting

- [ ] T150 [P] Author `~/.claude/skills/trace-driven-tasks/SKILL.md` — reusable skill capturing the marker-discipline + phase-gate authoring pattern this build used (per operator directive; coordinate with speckit session for convention-doc review before publishing).
- [ ] T151 [P] Update `.specify/memory/constitution.md` version footer if any principle text was tightened during the build.
- [ ] T152 Run full `speckit-trace --strict` across the whole feature — no PARTIAL, no INVISIBLE, no dangling markers, no unknown FR references. Whole-feature FULL rating.
- [ ] T153 Update `~/.claude/CLAUDE.md` `## Memo` section (if applicable) with any operator-facing changes to `/recall` / `/memorize` command semantics.
- [ ] T154 [P] Retire memo-minder Phase G cross-host sync + Phase A.6.5 reconcile-lite entries — they become no-ops in the single-global + auditor world.

---

## Dependency graph (high level)

```
Phase 1 Setup
    │
Phase 2 Schema (foundation for everything)
    │
    ├── Phase 3 Mediators [US2] ← required by everything downstream
    │       │
    │       ├── Phase 4 Injection + Hooks [US1] [US3] [US4]
    │       │       │
    │       │       └── Phase 5 Providers [US8] (parallel-able with Phase 4)
    │       │               │
    │       │               └── Phase 6 Auditor [US6]  ← needs providers for AgentController + Conductor
    │       │                       │
    │       │                       └── Phase 7 Migration [US5] [US7]
    │       │                               │
    │       │                               └── Phase 8 Soak Test — HARD OPERATOR GATE
    │       │                                       │
    │       │                                       └── Phase 9 Cutover (deferred)
    │       │
    │       └── (Phase 7 migration doesn't need auditor for basic backfill — can start after Phase 3)
```

**Parallel-safe cross-phase**: Phase 4 (Injection/Hooks) and Phase 5 (Providers) share no files and can run concurrently; Phase 7 (Migration) core script can start after Phase 3 (Mediators) if migration is done with `bypass_mediator=true`. Auditor (Phase 6) benefits from having providers (Phase 5) but shadow-auditor can be null-provider tested first.

## MVP scope (if we wanted to cut early)

Minimum for the operator confidence gate to be meaningful:
- Phase 1 (setup)
- Phase 2 (schema)
- Phase 3 (mediators — the load-bearing user-visible surface)
- Phase 4 up to T054 (POST /injection-set + spec-kit-constitution injection — proves the Layer 2 gap-fill story)
- Phase 5 (providers with at least null adapter — proves Principle VIII)
- Phase 7 (migration — proves the corpus can be ported)
- Phase 8 (soak — the gate itself)

Auditor (Phase 6) is P2 and could be deferred to a second cycle if Phase 3-4 mediator + injection ROI justifies cutover without it.

## Task counts

- Setup (Phase 1): 4 tasks
- Foundational schema (Phase 2): 16 tasks
- Mediators (Phase 3): 11 tasks
- Injection + Hooks (Phase 4): 20 tasks
- Providers (Phase 5): 11 tasks
- Auditor (Phase 6): 13 tasks
- Migration (Phase 7): 5 tasks
- Soak Test (Phase 8): 7 tasks (RUNTIME + operator-gated)
- Cutover (Phase 9): 5 tasks (BLOCKED)
- Polish (Final): 5 tasks
- **Total: 97 tasks**

## Trace gate expectations

At Final Phase T152 completion, `speckit-trace --strict` should report:

- All 54 FRs FULL (impl anchor + test anchor)
- All 7 constitutional principles enforced (I-VII gates present; VIII is architectural — its enforcement is FR-041..046 which are FR-anchored)
- Zero dangling markers
- Zero unknown-FR references in `--require-full` gate lists

Each phase gate MUST exit 0 before proceeding. If a phase gate fails, fix in place before moving on — do not batch.

## Marker-discipline reminder

Every task above expects markers per the convention laid out at the top of this file. If a task's implementation lands without markers, `speckit-trace` at the phase gate will catch it (INVISIBLE rating on the affected FR). Fix in the same session.
