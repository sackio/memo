# Memo renovation — user-story interview

Raw interview log. Refined into `.specify/spec.md` via `/speckit-specify` after all rounds.

Interview arc:
- R1: Users & Jobs
- R2: What works well today
- R3: What's broken / convoluted
- R4: Retrieval quality
- R5: Reconciliation / ground-truth
- R6: Cross-system boundary (memo ↔ ATC ↔ agents)
- R7: Ideal state / renovation goals

Interviewer: memo session
Interviewee: Ben (slack:U0NGEHS2J)
Started: 2026-07-29
Corpus at interview start: 7339 memos, 8567 unique tags (58% orphan), single-global on server4

---

## R1 — Users & Jobs (2026-07-29 12:56 EDT)

**Q1a** — primary users?  **Q1b** — core job?

**Ben:**
> memo is primarily a knowledge-base for RAG used by agents. The idea is that agents are responsible both for maintaining the knowledge base — adding to it — and recalling it as needed. Yes, especially important when loading initial context or context after compaction, but also agents should be auto-curating memo as they work and automatically recalling things as they work. It is true that users might manually prompt a recall or manually prompt memorization with memo, but in practice agents have mostly done all of this and I want agents to be responsible for this. The idea is memo is the long-term memory AND short-term memory for multiple agents working together, both on collaborative projects and on individual lanes of knowledge.

**Key claims to hold onto:**
1. PRIMARY user = agents, not humans. Human write/read is fallback, not central.
2. Agents own BOTH sides: write (auto-curate as they work) + read (auto-recall as they work).
3. High-value moments: (a) first-turn context load, (b) post-compaction re-warm, (c) mid-session auto-recall.
4. Memo spans BOTH long-term (archival, weeks-old) AND short-term (in-progress, hours-old).
5. Memo spans BOTH collaborative work (multiple agents on same project) AND individual lanes (per-agent private knowledge).
6. Corpus is the fleet's shared brain — write-one-agent, read-any-agent.

**Design tensions surfaced:**
- 2×2 matrix: (long/short) × (shared/lane) → four modes memo must serve.
- "Auto-curate as they work" is aspirational vs current — most writes today come from batched hooks + memo-minder cron, not mid-session self-curation.
- "Individual lanes" implies per-agent scoping/filtering that today is only tag-based.

---

## R2 — What works vs. aspirational (2026-07-29 13:00 EDT)

**Q2** — rate the 9 candidate behaviors (works / half-works / broken / aspirational).

**Ben:**
> Overall it's working pretty well because it's almost invisible in the background for me. It's clear memo is memorizing things and background processes are memorizing important facts. Where I focus is just getting agents to perform better in their work and then I think about how memo could be improved to do this. One concept we introduced recently is rewarming pins — using ATC (real-time comms between agents/users, multiple channels) to keep a piece of context or a reference to a particularly topical memo/series of memos in a "pin" so an agent can call upon it or get it automatically during compactions. One thing I want to think about more is how we can more forcibly inject context into agents, or as agents are working make sure they maintain and remember certain memos — especially memos about anti-patterns, pitfalls, goals, and desired working processes. This is an area where we're seeing issues, especially on the much larger quantum-feed project where we have a massive spec, many many files, and long-running agents. I really need them to keep certain memories — especially about working style, anti-patterns, and goals — in their context. Overall memo is working pretty decently; I haven't dug too much into internals. Within a session it would be nice if we could use memo more to reduce agent context — keep it just to important pertinent temporal memos in combination with ATC and maybe the agents supervisor session. Also — you could dispatch background workflow agents to analyze memo and reconcile it with Claude Code logs, which are often the source of truth for many memos. I think one thing we should be building is **links between the stored memo and its source material** — the Claude Code log (where and when), files, git commits, Gmail, etc. Important we have an **auditable track record** of where memos were sourced from.

**Key claims:**
1. Background works — invisible, mostly fine as ambient infra.
2. Rewarm-pins (ATC-based) exist but need to be MORE FORCIBLE / systematic.
3. **PAIN**: on large projects (quantum-feed) with long-running agents + massive specs, agents FAIL to retain behavioral memos (anti-patterns, working style, goals) in-context.
4. Aspirational: use memo + ATC + agents-supervisor together to REDUCE per-agent context to just the pertinent temporal memos.
5. Aspirational: background workflow that reconciles memo against Claude Code logs (logs = source of truth).
6. **NEW REQUIREMENT — SOURCE-LINKING / PROVENANCE**: every memo should link to its source material (Claude Code log line, file, git commit, Gmail msg, etc.) for auditable track record. Not just source-tag, but a real reference.

**Design implications:**
- Behavioral / anti-pattern memos need a *stickier* class than facts — maybe an "always-in-context" pin tier tied to project/agent.
- Provenance is a first-class field, not a tag afterthought (elevates from A.6 source-tag heuristic).
- Reconciliation loop needs to walk Claude Code logs (already partially done in memo-minder A.1); output should update provenance links, not just tags.
- Context-reduction ambition means memo must be able to answer "which memos are pertinent RIGHT NOW for this agent on this task" — implies richer scoping than tags.

---

## R3 — Behavioral-memo forgetting pain (2026-07-29 13:03 EDT)

**Q3** — concrete quantum-feed examples where agents violated behavioral memos; pin-system diagnosis.

**Ben:**
> I'll talk about the pin system first because it grew up very haphazardly and while it's used today it could be streamlined. Other collaborations could be linked to ATC — such as when a memo is stored having it automatically added to a pin that then gets re-injected into the agent by ATC or a timer or every so many turns or at compaction. I think it's better for you to look at what we have there with a background agent and get a report. With the quantum-feed project you can look at quantum-navigator, and basically the past two weeks of wrestling where we've had back-and-forth about the design of the project — maybe grep the Claude Code logs for emotional responses from me where I'm upset or say something is wrong, and think about what happened there. Background agents should be used by you at this point to research some of those pitfalls. And understand there's a big difference between **behavioral instructions**, **broad goals**, and **just facts** that memo needs to memorize. More broadly, we want to come up with an algorithm for memo that helps store these different types of information in different ways — similar to how brain and consciousness works.

**Key claims:**
1. Pin system is haphazard — needs streamlining; primitives are there but composition isn't.
2. **NEW PRIMITIVE PROPOSAL**: on `memo_store`, auto-attach a pin that ATC re-injects via timer / N-turns / compaction hooks. Wire memo↔ATC as a closed loop.
3. **TAXONOMY** (brain analogy): memo stores at least three distinct classes —
   - **Behavioral instructions** (working style, anti-patterns, "don't do X")
   - **Broad goals** (what we're trying to achieve, current mission)
   - **Facts** (traditional recallable knowledge)
4. Each class needs its own **storage + surfacing algorithm** — not one-size-fits-all.
5. Directive to me: dispatch background agents to research (a) pin-system usage today, (b) quantum-feed wrestling / Ben-frustration events → behavioral-memo failures.

**Design implications:**
- Spec must define a **class field** on memos (behavioral / goal / fact / other?), each with its own lifecycle.
- Behavioral + goal classes need a **forced-injection channel** (pin↔ATC↔compaction hook) — not just search-and-hope.
- Fact class stays with RAG semantic search (current model works).
- The renovation is not incremental — it's a memory-architecture rethink, with brain-inspired zones (procedural vs. semantic vs. episodic).

**Dispatched (2026-07-29 13:04 EDT):**
- Explore agent A — ATC pin/rewarm-beacon usage audit ✓ (complete, findings below)
- Explore agent B — quantum-feed frustration-event → behavioral-memo failure correlation (running)
- Explore agent C — memo-driven session-compaction architecture + DM to `agents` supervisor (dispatched 13:15)

### Agent A findings (pin/beacon audit) — key extracts

**Two mechanisms conflated under "pin/beacon":**
- `atc_post_beacon` — real Redis JSON + scheduler; `/mnt/nas/data/code/atc/server/src/beacons.ts:50-63,220-234`. Repeat_seconds, max_repeats, ack-decrement, expiry notify.
- `atc_post(zones=["rewarm:<id>"])` — **status board record**, not a beacon; `rewarm:*` is a FAKE ZONE (zero subscribers) used as a query tag. `/home/ben/.claude/skills/rewarm-pin/SKILL.md:34-39`.
- SessionStart:compact hook stitches them: subagent queries `rewarm:<id>` on the status board → folds hits verbatim into a new beacon posted to the post-compact session.

**Beacon traffic (past 11 days, docker logs atc):**
- 2,321 beacons total → **1,876 (81%) are auto-promoted Slack DMs** to Ben (delivery-reliability, not rewarm)
- Only ~354 real `atc_post_beacon` calls. Top posters: quantum-library (62), agents (57), quantum-navigator (41), quantum-exec-impl (38), quantum-library-guardian (29)
- **Genuine post-compaction rewarm-builder subagents fired only 13 times across 8 distinct names** — most sessions never trigger a rewarm at all
- Zero `beacon expired without ack` events — when beacons arrive, they get acked reliably

**Live `rewarm:*` status pins in Redis right now: 43**, dominated by dojo (26 overlapping "PIN AMENDMENT N" records) — supersede/dedupe missing. atc-minder explicitly refuses to touch `rewarm:*`.

**Re-injection triggers today: ONLY the SessionStart:compact hook.** No N-turns cadence, no memo-triggered re-inject, no fresh-session-start rehydration (only *post-compact*-start).

**"Auto-pin on memo_store" feasibility:** ~50-line change to memo (opt-in `pin=True` + `target=` on POST /documents → forward to atc `/beacons`). Blockers: (1) MCP has no implicit caller identity — caller must supply `target`; (2) turn-count / compaction triggers don't exist on beacons yet (only fixed repeat_seconds); (3) no upsert-beacon primitive → duplicate pins accumulate.

**Streamlining candidates:**
1. Collapse the two "pin" mechanisms into ONE (beacons only, with `expires_after_acks=∞ + expires_at=+7d` mode for pin-tier)
2. Rename or eliminate the fake `rewarm:*` zone
3. Separate delivery-retry beacons (Slack DM promotion) from rewarm/pin beacons at the tool level so analytics aren't polluted
4. Add `atc_supersede(old_id, new_content)` upsert so amendments replace prior pins
5. Guidance lives in 4 places (CLAUDE.md #10, rewarm-pin SKILL, atc-minder SKILL, atc-precompact-beacon.py hook) — subtly divergent; consolidate

---

## R3-extension — Ben follow-up 13:07 EDT

**Ben:**
> There's another piece I want you to dispatch a background agent to research — including talking to the agents supervisor. I think we could make use of memo to **compact our sessions more frequently and reduce the context to just what's truly important**, and reduce a lot of bloat which leads to both drift, distraction, less precise results, and costs us more usage. So this is another goal of the memo product, and something that could really get unlocked as we do more of this research.

**Key claim:** memo has a role BEYOND storage — it should enable more aggressive per-session compaction by serving as the durable substrate that lets us safely drop in-transcript bloat. Today compaction is a summary of the transcript; ideal-state compaction is "memo already has this — drop it from the transcript."

**Design implications:**
- Compaction becomes memo-aware: pre-compact hook writes recoverable state to memo, then compaction can be much more aggressive about dropping in-conversation history because the "if I need it, memo has it" invariant holds.
- Every agent gets a per-session "working memory" scratchpad in memo (short-term lane) that survives across compactions with lower-latency retrieval than semantic search.
- Cost / drift / distraction reduction is now an explicit product goal.

---

## Supervisor-perspective input — from `agents` session (2026-07-29 13:20 EDT)

Unsolicited (but requested by Agent C) — the fleet supervisor answered directly.

**Hot / bloated sessions today:**
- Quantum fleet are the repeat offenders — continuous multi-hour campaigns → transcript ballooning.
  - `quantum-library` respawned ~5× in one night
  - `quantum-navigator` ran ~21h through a compaction into Bun-segfault territory
  - `quantum-data` hit 10.5 MB (past the 6 MB Bun `--resume` segfault line)
  - `quantum-dashboard`, `hi-score` similar
- Bun 6MB `--resume` segfault is the HARD line — crash-looped `storybook@6MB` / `phony@44MB` / `mind@9MB` on 2026-07-21
- Top burners replay 300-450M cache-read tokens/day of un-compacted context
- Office champion: `assistant` at ~$1730/day usage

**Flush-and-forget IS the established fleet pattern** — but critically it's `memo + REWARM BEACON`, **not memo alone**.
- Well-run agents bank a resume-queue memo (often chained) before respawn + post a beacon pointing at it by FULL UUID
- Fresh instance re-hydrates from memo, not from transcript
- Memo does NOT auto-deliver post-compaction — agent must know to `memo_search` for it
- BEACON is what redelivers ("re-warm me")
- Poorly-run agents let `/compact` summarize (lossy → data loss)

**Auto memo-flush + auto-compact WOULD HELP but:**
- Fleet already does manual flush (a) + manual compact (c); the compact engine (b) was recently fixed (compact-session ghost-detection bug)
- Daily compact-sweep already handles (b) for utility agents
- Pre-compact flush is only worth it if it captures MORE structured state than the built-in `/compact` summary preserves (task-lists + summary are already saved)
- Auto-triggering must be IDLE-gated — supervisor just hit a ghost false-positive that silently blocked memo's own compact
- Re-hydrate must be DEMAND-driven, NOT "reload everything" (that just re-bloats)

**Anti-patterns to spec against (CRITICAL):**
1. **Session-scoped memos going UNDISCOVERABLE post-respawn** — a fresh instance won't `memo_search` a predecessor's tag unless the GUIDE or a BEACON tells it to. **Pair every flush with a beacon pointer or a deterministic guide-queried tag.**
2. **INDEX LAG** — a memo banked <~1 min ago isn't search-findable yet. "banked ≠ findable". Flush-then-immediately-search misses. **Use get-by-FULL-UUID** (prefixes return null by design) **+ a settle window.**
3. **Don't flush RAW transcript chunks** (memo bloat + reap burden). Flush DISTILLED resume-state.
4. **Mid-turn retrieval latency** — keep the hot re-hydrate to ONE pointer memo; lazy-load the rest.

Supervisor offered to review the spec draft.

**Spec constraints derived:**
- **C1**: Flush-and-forget path is a memo↔beacon coupling, not memo-alone. Beacon pointer is mandatory.
- **C2**: INDEX LAG is a first-class invariant. Post-write reads use full-UUID + settle window.
- **C3**: Any auto-triggered flush/compact must be idle-gated with a supervisor-fallback.
- **C4**: Storage class taxonomy needs a "resume-state" class distinct from behavioral/goal/fact — smaller lifetime, hot-pointer semantics.
- **C5**: The `/compact` summary already covers a lot — spec must NOT duplicate what it preserves; only add what's LOSSY today.

---

### Agent B findings (quantum-navigator / quantum-feed frustration mining) — key extracts

**Scope:** 2026-07-15 → 2026-07-29 · server5 quantum-feed jsonls · memo corpus 7,339 docs (88 unique quantum-scoped behavioral/hard-rule/coaching memos).

**Dominant failure mode: "memo not loaded" > "memo loaded but ignored".** Behavioral memos are worst-served class.

**Memo-loading behavior across 9 quantum-navigator sessions (past 14d):**
- All load `memo_context` exactly ONCE on spawn (spawn-prompt boilerplate)
- Mid-session `memo_search` / `memo_get` = single digits over multi-MB sessions
- Two flagrant ZERO-memo-call sessions:
  - `721045d5-abce-462b-97dc-787c997db369` (nav-spawned attester, 11 MB, **11 compactions, 0 memo calls of any kind**)
  - `bce5cbe9-fd44-4967-8fc3-21398ed427f1` (quantum-minder, 41 MB, **24 compactions, 0 memo calls**)
- `SessionStart:compact` hook is NOT re-loading memo context — that's the primary loss vector
- Nav guide's "read MEMORY.md" runs once at boot, then never again through compactions

**Concrete frustration events where memo said X, agent did Y:**

1. **Anti-capture / over-delegation (memo `ae52afce-3e2a-4873-869f-c543fc5a2dbf` — behavioral-rule + operator-coaching tags) — re-issued 3× in window.** Nav sessions never `memo_search`'d "delegation" / "seam" / "over-delegation" / "anti-capture" / "§3.1" once in 14 days across ALL nav sessions.
2. **Constitutional done-line drift** (SC-018 union scope — re-pin drifted for 7 days before Ben caught it, post-compaction).
3. **ETF-proxies rule (spec FR-027 + memos 36605235, fb1b47f4)** — Ben had to say `"recall we want to use etf proxies for indexes for now"` — the word "recall" is his pain signal (knows he's said it before).
4. **FLAVOR-DUAL FROM BIRTH correction (2026-07-16 14:08)** — so load-bearing that nav had to **VERBATIM COPY-PASTE it into 4+ successor session rewarm prompts** because the memo system wasn't trusted to carry the framing. **No canonical "FLAVOR-DUAL" memo exists in corpus.** Reading rewarm text works but doesn't survive taxonomy — it lives only in copy-pasted paragraphs.
5. **Ben force-halted the fleet 2026-07-20 to nuke stale/contradicting memos + reviews.** His own words: "stale fog", "wrong-old memos", "dead rollups". This is the meta-frustration event: the corpus rot itself became the load-bearing problem.

**Ben's frustration signature (verbatim):**
- `"OPERATOR DIRECTIVE (Ben, emphatic, repeated; relayed by nav DM 21:53Z)"` — "emphatic, repeated" is the tell
- `"recall we want to use etf proxies for indexes for now"` — "recall" = "you should already know this"
- `"HALT LIFTED… nuke the contradicting .reviews/memos"` — corpus hygiene as blocker
- No profanity or "!!!" — just escalating adverbs and REAFFIRMED/OVERRIDDEN caps

**Pattern confirmation:**
- Factual tags (bug, refdata, etf, session-sourced) are well-served by memo_search
- Behavioral / anti-pattern / operator-coaching tags are **effectively invisible** to nav agents
- Compaction-heavy sessions show worst drift (96988477=13x, 721045d5=11x, bce5cbe9=24x)
- Some rules never get memo'd at all (self-noted "ssh compound-cd trap recurred 6×" in `41659778`, `d24bdf75` — recurrence acknowledged in transcript, never promoted to memo)

**Spec constraints derived:**
- **C6**: Behavioral / anti-pattern / operator-coaching memos need a FORCED-INJECTION path, not "hope agent searches". Retrieval-only is proven-insufficient.
- **C7**: SessionStart:compact hook MUST re-load a pertinent-memo set — the current path drops it entirely.
- **C8**: Verbatim copy-paste of load-bearing rules into successor rewarm text (FLAVOR-DUAL case) is a design failure — rules should live in memo with a canonical id, and rewarm should REFERENCE the memo, not embed the text.
- **C9**: Corpus rot ("stale fog") is itself an operational blocker. Reconciliation must be aggressive enough that Ben doesn't have to halt the fleet to purge.

---

### Agent D findings (ecosystem scan) — key extracts

**Projects surveyed:** mem0, Letta/MemGPT, LangGraph checkpointer+store, LlamaIndex memory, Zep+Graphiti, Cognee, Chroma agent-memory recipes, Anthropic memory tool, plus 2025-26 academic surveys ("Memory in the Age of AI Agents", "AI Hippocampus").

**Convergence across ecosystem:**
- Short-term (thread/session) vs. long-term (cross-session) split is universal
- Vector + increasingly graph retrieval
- "LLM decides what's worth remembering" is dominant write model
- Brain-analog trichotomy (episodic / semantic / procedural) is the 2025-26 default taxonomy
- **Memory ≠ RAG** — Chroma team's insight: "similarity search and memory are not the same thing" — validates leaning harder on tags/metadata than pure semantic similarity

**Sharpest divergence — INJECTION MODEL:**
- **Letta = FORCIBLE** — Core-memory-blocks ride in-context every turn (agent self-edits via tool calls)
- Everyone else = RETRIEVAL-ONLY
- This gap maps directly to Ben's goal #2 (behavioral memos MUST stay in-context)

**Storage substrate split:**
- Vector-only: Chroma, LlamaIndex, early mem0
- Graph-first (winning for agent memory): Graphiti, Cognee, Mem0g
- Bi-temporal (only Graphiti): every edge has `valid_from / valid_until / source_episode_ref`

**Gaps in ecosystem that memo could fill (novel positioning):**
1. **Fleet-wide vs per-lane scoping** — every system surveyed is single-agent or single-user. NONE models N agents sharing one substrate with per-agent overlays. **Ben's shared-memo + per-lane split is genuinely novel.**
2. **Forcible behavioral injection as a first-class class dimension** — Letta's Core comes closest but is a single monolithic block. Classifying WHICH memos ride in context (behavioral) vs. retrieve on demand (semantic/episodic) is unaddressed.
3. **Provenance to real-world artifacts** — Graphiti tracks source-episode within its own graph, but nobody links memos to Claude Code log lines / git SHAs / Gmail message IDs across heterogeneous sources.
4. **Compaction-substrate contract** — Anthropic's memory tool + context management points at this but doesn't formalize "the transcript is disposable because memo has it."

**Three ideas to STEAL:**
1. **Graphiti's bi-temporal edges + source-episode pointers** — solves goals #3 (provenance) and #5 (brain-inspired zones) simultaneously. Every memo edge: `valid_from / valid_until / source_ref`. Enables "what did we believe on 2026-06-15?" queries + honest supersession (not destructive updates).
2. **Letta's Core-memory-forcible-injection, generalized as per-memo `injection_mode` field:** `forcible` (behavioral, every turn), `on_recall` (semantic/episodic), `on_procedure_match` (procedural, inject when agent's task type matches).
3. **LangGraph's checkpointer/store split** adapted per-lane vs. fleet-wide. Thread-scoped checkpointer ≈ per-agent working memory; cross-thread store ≈ fleet shared long-term. Adds per-lane long-term as a scoped variant.

**Three ideas to explicitly REJECT:**
1. **mem0/Cognee LLM-extraction pipelines that flatten source trails** — memo authors already curate; running through an LLM synthesizer erases provenance
2. **LlamaIndex-style rolling summarization of conversation memory** — lossy + uncitable; wrong for goal #4 (memo as durable ground truth)
3. **Letta's single-agent OS framing** — steal the Core-injection mechanism, reject the single-agent-owns-its-memory ownership model (breaks fleet-wide sharing)

---

## Additional supervisor input — `agents` follow-up DM (13:21 EDT)

Four more fleet-vantage insights, unsolicited:

1. **REFRAME THE COST: not transcript SIZE — per-turn cache-read REPLAY.** A loop/daemon agent reloads its ENTIRE un-compacted context on every fire (300-450M cache-read/day for top burners). **Highest-value compaction targets = always-on loop agents** (minders, heartbeats, watchers), NOT episodic campaign agents (quantum/hi-score already self-respawn-fresh + self-manage). "More frequent compaction" is right — but aim it at the LOOPS.
2. **COMPACTION DESTROYS PRECISION** — this is why the beacon pattern exists. `/compact`'s summary is PROSE; it flattens the load-bearing precise stuff: full 36-char UUIDs, git SHAs, exact `file:line`, hard-constraints ("don't re-run T064", "no sealed evidence set exists"). Agents don't trust the summary with those, so they bank verbatim pins instead. → **memo substrate's core value = preserving VERBATIM-CRITICAL state the summary would destroy.**
3. **THE GUIDE IS THE DURABLE DISCOVERY ANCHOR.** A fresh instance's `--guide` (always-on system prompt) is the ONE thing guaranteed to load on every respawn/compact. So the reliable answer to "how does a fresh instance find its memos" is: **the GUIDE names the deterministic tag/query** — don't hope the agent thinks to `memo_search`. Any per-session memo discovery MUST root in the guide (or a beacon), never in agent initiative.
4. **CONFIRM SUBSTRATE IS ALREADY GLOBAL.** memo went cross-host global recently (verified tonight: `memo_get` from s4 resolves s5-written memos). If any part of renovation assumes per-host, it's on outdated model.

**Spec constraints derived:**
- **C10**: Primary compaction targets = always-on LOOP agents (cache-read replay pain), not campaign agents.
- **C11**: `verbatim-critical / do-not-summarize` is a **first-class memo class**, distinct from behavioral / goal / fact. Highest value, lowest frequency, full-UUID discipline. Compaction MUST NOT flatten it.
- **C12**: The GUIDE (always-on system prompt) is the discovery anchor. Per-session memo discovery must root in guide-named deterministic tags/queries or beacon pointers — never agent initiative.
- **C13**: memo substrate is confirmed global (cross-host reads work). Spec assumes global; no per-host fallback.

---

### Agent C findings (memo-driven compaction architecture) — key extracts

**Current compaction architecture:**

*Triggers today:* (1) Claude Code built-in auto-compact at context limit; (2) explicit `/compact`; (3) external `/mnt/nas/data/code/scripts/compact-session` via `tmux send-keys` from outside session (`--self` mode backgrounds waiter until pane idle — written 2026-07-15 for parked utility/loop agents whose per-turn context reload dominates token cost).

*PreCompact hook chain (`~/.claude/settings.json:101-140` in order):*
1. `smart-periodic-hook.py:110-113` — prints "run /memo-sync now" — **ADVISORY ONLY, doesn't flush.**
2. `inject-time-precompact.sh` — timestamp injection
3. `pre-compact-tracking.sh` — usage telemetry
4. `atc-precompact.sh:1-16` — **INTENTIONAL NO-OP.** v1 tried injecting additionalContext from PreCompact; the PreCompact schema rejects `hookSpecificOutput.additionalContext`. **Ripe for repurposing.**
5. `memo-judge.py:380-383` — reads last ~250 lines, Haiku decides PASS or WRITE one memo. **Opt-in via `MEMO_JUDGE_ENABLED=1`.**

*SessionStart:compact chain:*
- `atc-precompact-beacon.py:82-108` prints "REWARM-BEACON DISPATCH" block into post-compact session's first system reminder. Instructs fresh agent to spawn a subagent that (a) reads preserved pre-compact transcript file, (b) queries `rewarm:<subscriber-id>` zone for user-pinned context, (c) posts an ATC beacon (30s repeat, 10 max, expiry_notify=ben) with distilled rewarm content.
- `/rewarm-pin` skill writes to `rewarm:<subscriber-id>` zone with 7-day TTL.

*What memo already contributes:*
- `memo-judge.py` (opt-in) — auto-captures on Stop/user-correction/PreCompact/SubagentStop/SessionEnd
- `POST /auto-store` — LLM extract → embed → dedup vs. existing → merge/create/skip. **Server-side dedup is the key primitive.**
- `POST /context` and `memo_context` MCP tool — token-budgeted multi-query recall returns a single budgeted string
- `memo-minder` daily cron — batch, not real-time
- `smart-periodic-hook.py` — nudges `/memo-sync` at 30-min/120-call/50k-token thresholds

*What Claude Code compaction keeps vs. drops:*
- **Keeps**: last user turn(s), work summary, pinned system reminders
- **Drops**: full tool outputs, long assistant reasoning, sub-agent transcripts, MCP tool-list expansions
- Preserved OUTSIDE transcript: pre-compact `.jsonl` file (used by beacon builder), any memos/pins the agent explicitly wrote

**Leverage — what's in-transcript that memo COULD hold:**
1. **In-flight state at compact time** — background bash jobs, dispatched agents awaiting results, pending tasks, DMs owed (currently extracted by beacon-builder subagent; memo-backed store would let compaction happen more often even without beacon dispatch)
2. **Repeatable research context** — "here's what I found in X/Y/Z" narrative that agent will need again next turn
3. **Recent tool outputs consumed but not synthesized** — grep hits, file reads, JSON API responses. Consume-then-drop is safe if reference lives in memo
4. **Sub-agent reports** — long research summaries returned from Task/Agent calls that parent barely re-reads

**Four proposals, ranked A > (A+D) > B > C:**

- **Option A — Session-scoped flush-and-forget memos** (RECOMMENDED, complexity M): Pre-compact hook writes small memos tagged `session:<subscriber-id>` + `ephemeral-flush` + `flush-generation:<N>`. Each covers one slot: `active-threads`, `in-flight-work`, `pending-dms`, `open-tasks`, `key-decisions`, `follow-ups-owed`. Compaction free to be aggressive. Post-compact hydrate via `memo_search(tags=["session:<id>","ephemeral-flush"])`. Needs: first-class `session_id` metadata OR tag convention, optional `POST /flush` upsert endpoint for one-call slot writes, TTL/`expires_at` on memos for auto-reap (currently only weekly by memo-minder — too slow for per-compact churn). Hook needed: convert atc-precompact.sh from no-op to real flush trigger. SessionStart:compact seeds agent with `flush-generation:<N>` so it knows what to search for. **Estimated 30-50% utility-agent transcript growth savings.**

- **Option B — Consume-and-drop tool-output cache** (S if supported): Wrap large tool outputs (grep >1KB, reads >500 lines, sub-agent reports) with memo_store at emission; agent sees pointer + summary. Requires PostToolUse `hookSpecificOutput` transform capability — **speculative, not audited whether Claude Code supports rewriting tool results.** 15-30% pre-compact savings.

- **Option C — Pre-compact self-summarization** (S): Formalize `/memo-sync` as obligatory pre-compact step (auto-invoke bounded subagent). Modest per-cycle; improves memo quality for future recall.

- **Option D — Aggressive auto-compact cadence** (S, orthogonal): Extend `compact-session --self` into hook-driven scheduler; call `/compact` at N-turn/M-token intervals. Multiplies A/B/C by making them fire more often. Brittle to Claude Code REPL updates.

**Agent C's DM to `agents` didn't receive a reply within its 3-min window** — but `agents` supervisor DID reply directly to the parent memo session (twice), captured above under Supervisor input sections.

**Spec constraints derived:**
- **C14**: `atc-precompact.sh` is a NO-OP today — ripe for repurposing as the real flush trigger. No new hook needed, just wire the existing slot.
- **C15**: memo needs an `expires_at` / TTL field for ephemeral-flush class — weekly reap by memo-minder is too slow for per-compact churn.
- **C16**: `session_id` should be a first-class metadata field (or a strict tag convention), not something reconstructed from ad-hoc tags.
- **C17**: Compaction savings from Option A (30-50% typical utility-agent transcript growth) is estimate-only; instrument via `~/scripts/claude-usage/hooks/` to measure real impact.
- **C18**: Option B (tool-output cache) blocked on unknown whether Claude Code supports PostToolUse rewrite. Spec should flag this as a research spike, not a build item.

---

### Agent B2 findings (mind + dojo frustration mining, past 4 days) — key extracts

**All mind/dojo sessions live on `office`**, not server4. Sessions are almost entirely agent-driven autonomous loops (evergreen-loop + hourly dojo loop); Ben directs 100% via Slack DMs relayed through ATC beacons.

**Frustration events (chronological, direct quotes):**
- **E1 (07-25 01:33)** — mind agent wrote *taxonomy analysis* of unbuilt strategies rather than building. Ben: *"so you should be building all these out … I want you kind of heads down just working on building this library"*. No behavioral memo naming "build > analyze". `memo/recall` calls first 3000 lines = 0.
- **E2 (07-25 12:45)** — assistant confidently declared *"loop is fully operational with reliable hourly delivery"*. Ben 7h later: *"no I didn't get any hourly updates"*. No memo "verify delivery actually reached Ben before declaring reliability".
- **E3 (07-26 19:26)** — preemptive guardrail *(not rebuke)*: *"just make sure you don't bother any of the other quantum stuff … keep everything read only"*. No "read-only when auditing another project" rule.
- **E4 (07-28 22:48) — STRONGEST** — agent delivered dividend-matrix work faithful to a decision memo Ben made hours earlier (`5d2cd356`, "quarterly cap on book-137 sleeve, next rebalance 2026-09-30"). Ben: *"now this is all wrong … you getting too dogmatic and too rigid we literally started working on this last week I don't care if you tear everything down … this is a serious flaw in your reasoning"*. **This is NOT "memo ignored" — it's memo PRESENT AND OBEYED TO A FAULT.** Post-compaction session (SessionStart:compact fired). `memo/recall` calls first 3000 lines = 0.
- **E5 (07-28 23:12)** — dojo fork genesis: *"honestly let's stop wasting cycles trying to make sure alpaca doesn't waste any time … stop holding up our stuff"*. Rewarm:dojo pin posted immediately.
- **E6 (07-29 00:53)** — dojo agent talking about outdated "arms". Ben: *"these arms you keep talking about also seem outdated, just focus on the harness"*. Post-compaction. `memo/recall` = 1 (rewarm:dojo pin only, not working-style memos).

**Pattern synthesis — DIFFERENT FROM QUANTUM-FEED:**
1. **Behavioral memos MOSTLY DON'T EXIST** for the failure modes Ben is hitting in mind/dojo — build > analyze, verify delivery reached recipient, don't over-guard shared resources, don't be rigid to a decision Ben moved on from, focus on the pillar Ben is reviewing.
2. **When memos DO exist, they encode the WRONG-LEVEL rule.** `5d2cd356` (quarterly-cap decision) — agent obeyed dogmatically. **Missing meta-rule**: *"a decision memo <2 weeks old on a fast-moving experiment can be re-opened cheaply — challenge it when your action would delay the build by weeks."*
3. **`memo/recall` loading is essentially ZERO at session start** across 4 main frustration sessions (0/0/0/1 in first 3000 lines). Working-style memos (`2719ce79`, `1221e0a7`, `46a378e4`, `2341d4be`) are never pulled.
4. **Compaction correlation confirmed**: 2 of top 3 events (E4, E6) had SessionStart:compact — post-compaction lost tone/priority thread.
5. **Cross-project recurring rule**: `2719ce79` (quantum-planner 2026-06-29, "don't re-frame a clear Ben directive as scope-fork") — same "too rigid / re-frame" shape reappears in mind (E1) and dojo (E4). Not tagged `mind` or `dojo`, so semantic recall wouldn't surface in these sessions.

**Speculation (marked):**
- **[spec]**: The dojo fork itself may be a memo-hygiene tell. Ben's fix for too-rigid mind was to spawn a new agent with a fresh pin, NOT to update the existing memo. **Memo-renovation should model "durable behavioral corrections that survive respawns AND supersede stale decision memos" as a first-class primitive.**
- **[spec]**: The 16-MB mind session `6810e0a8` never called `memo_context`. Suggests the hourly-loop prompt template is missing a "recall behavioral rules before acting" step — **systemic fix (template + guide), not a memo-corpus fix.**

**Spec constraints derived:**
- **C19**: NEW FAILURE MODE — "memo present and obeyed to a fault" (rigid-to-stale-decision). Memos need a **temperature/reopenability** dimension: hard-rules (never violate) vs. decisions-in-progress (challenge if situation changes) vs. facts (update on new evidence).
- **C20**: The corpus has GAPS in behavioral coverage — many failure modes have no memo at all. Reconciliation isn't just "prune stale memos" — it's also "surface uncovered failure patterns and prompt writes."
- **C21**: Cross-project recurring rules (memo `2719ce79`) aren't tagged for the projects they apply to — retrieval-only can't find them. Requires either richer cross-project tagging, project-agnostic behavioral-class routing, or forcible injection.
- **C22**: Fresh agent-loops (hourly cron) never call `memo_context` — the loop prompt template is the fix point, not memo itself. The GUIDE / loop-template convergence is C12 restated for a specific channel.

---

## R4 — Taxonomy validation + injection model (2026-07-29 13:38 EDT)

**Ben:**

**(a) Taxonomy**: Good starting taxonomy — likes the approach. **Will need to retrofit existing memos into it — but that's SECONDARY work, comes later after finishing the design work first.**

**(b) Reopenability temperature — MIDDLE ROAD**:
- Try different things over time and modify
- **Facts: DON'T want agents questioning** (stable, high-trust)
- Other taxonomy categories: MORE flexibility, open to review
- **NEW ROLE: secondary background AUDITOR agents** — run OVER a session (not the running agent itself) — decide what needs to be remembered / modified — potentially re-inject things into the running or future session
- Existing sweep pattern (memo-minder) still runs — historical prune/delete/update as facts change

**(c) Forcible injection — YES, unbounded cost is fine**:
- Every session always gets certain info in context; comfortable spending the tokens
- **TWO-LAYER "always in context" model:**
  1. **Constitutional layer**: constitutional rules + goals + anti-patterns + most important behavioral stuff — always in session, **always reminded at compaction**
  2. **Current-focus / short-term layer**: "what's on our mind, what's most important at this point in what we're working on" — starts in session, re-injected at compaction OR at trigger-based autocompact levels; rewarm in different ways

**Spec constraints derived:**
- **C23**: Retrofit sequence — design taxonomy + primitives first, migrate existing corpus SECOND. Do not conflate.
- **C24**: Facts = stable / high-trust class (agents don't question, only reconciler updates). Everything else has "reopenability" scale.
- **C25**: **AUDITOR AGENT is a first-class architectural component** — separate from running agents, reviews sessions after-the-fact, has powers to (a) write new memos, (b) modify existing, (c) re-inject into a running or future session. Distinct from memo-minder's global sweep.
- **C26**: Forcible-injection has TWO tiers, not one:
  - Tier 1 **constitutional** (durable, fleet/agent-scoped, hard-inject every session + every compaction)
  - Tier 2 **current-focus** (short-term, session-scoped, session-start + compaction-rewarm)
- **C27**: Autocompact TRIGGERS are a real design surface — not just at Claude Code's built-in limit; can fire at operator-chosen thresholds tied to memo-flush + rewarm.

---

## R5 — Auditor role (2026-07-29 13:44 EDT)

**Ben preamble (fact-refutation model):**
- Agents in-session CAN store new facts (facts come to light as agents work)
- Agents in-session CANNOT refute existing facts (memo is authoritative)
- **ONLY the operator (human) directly contradicting can trigger a fact update** — either updated in place OR invoke auditor
- Refutation path: operator → (direct or via auditor) → fact update

**(a) Scope — HYBRID:**
- Per-session shadow auditor (one per running session)
- AND global auditors (fleet-wide)

**(b) Trigger — MIX OF ALL:**
- Skills baked in, triggered when certain rules invoked
- After N context has elapsed in a session
- On compaction (goes back through transcript)
- Proactively on cron (Claude Code logs)
- **Expectation: auditor is CONSTANTLY IN THE BACKGROUND of every session**
- **Auditor SHOULD BE ABLE TO TRIGGER COMPACTION** to discard bloating context, keep it to goals + short-term memory

**(c) Powers — FULL AUTONOMY:**
- Only human operator with explicit direction can overrule
- No approval needed
- Session auditors POLICED BY broader global auditors / nightly cron auditors
- Constitutional rules for auditors refined as they develop
- Architecture: **"combination of skills and agent definitions used alongside running sessions"**

**(d) Mid-session vs. after-fact:** BOTH — wants my recommendation.

**Spec constraints derived:**
- **C28**: **Fact-refutation flow**: agents can WRITE new facts, cannot REFUTE existing facts. Refutation requires operator input (direct or via auditor). Facts remain highest-trust class.
- **C29**: Auditor architecture is TWO-LAYER: per-session shadow (running in parallel to every live session) + global (fleet-wide, cron-driven). Session auditors are policed by global auditors — hierarchical governance.
- **C30**: Auditor triggers are MULTI-CHANNEL: (i) skill-baked reactive triggers, (ii) context-length threshold, (iii) SessionStop/SessionEnd, (iv) SessionStart:compact, (v) cron (Claude Code logs), (vi) operator on-demand DM.
- **C31**: Auditor can TRIGGER COMPACTION on the observed session. This makes auditor + `compact-session --self` a natural coupling — auditor decides bloat threshold breached → flushes to memo → triggers compaction → primes rewarm.
- **C32**: Auditor autonomy is FULL. Only operator override. No approval workflow. Design implication: auditor mistakes are self-correcting (global auditor polices session auditor, next-day cron polices global).
- **C33**: Auditor architecture = **skills + agent-definitions living alongside running sessions**. Not a new process type — leverages existing agent-fleet primitives (agent-supervisor spawns, ATC coordination, session lifecycle).

## R5-recommendation for (d) — mid-session vs. after-fact injection

My recommendation: **BOTH, with different thresholds** —
- **Mid-session injection**: HIGH-PRECISION only. Fires on:
  - Frustration signal detected in Ben's incoming DMs ("no", "wrong", "emphatic", "recall")
  - Action about to be taken that violates a `verbatim-critical` or `hard-rule` behavioral memo
  - Extended stretch (>N turns) without behavioral-memo recall in a session with pinned behavioral memos
  Low bar for OBSERVE, high bar for INTERRUPT. Injects via ATC beacon (existing primitive) or a targeted `system-reminder`-shaped DM.
- **After-fact meta-rule write**: DEFAULT, always runs at SessionStop. Auditor scans the transcript, writes a new memo for any observed pattern that lacks corpus coverage. Cheap.
- **Escalation**: session-auditor uncertain → DM global-auditor → global decides whether to inject mid-session. Prevents session-auditor from being either too quiet (misses) or too loud (interrupts legitimate work).

The mind 7/28 dividend-matrix event specifically: mid-session auditor SHOULD have caught it — the frustration signal was clear + the agent's action delayed the build by months. But the auditor's ability to know that requires either a "reopenability temperature" on the quarterly-cap memo (per C24) or an operator-preference signal ("Ben's tempo has shifted"). The after-fact meta-rule write would still fire regardless, capturing "don't be rigid to a <2-week-old decision when build-timeline shifts."

---

## R6 — Provenance + reconciliation (2026-07-29 13:50 EDT)

**(a) Source-linking:**
- ALL of the artifact types listed are good — the list is exhaustive
- **Provenance = first-class data structure**, not a tag or afterthought
- **NEW TOOL requirement**: intelligent Claude Code log query tools — both grep and MAYBE embeddings (uncertain but worth exploring)
- Claude Code logs = "raw stream of consciousness" — noisy but ultimately useful for building context

**(b) Bi-temporal — HESITANT/NO** (but open to contradiction):
- Might convolute things
- Backup archives (kept forever for both source materials + memo corpus) can recreate point-in-time truth
- Worried about agents being confused by refuted / pruned facts
- Doesn't see clear benefit

**(c) Reconciliation cadence — all-three-tiered PLUS the biggest new idea:**
- **RETRIEVAL MEDIATOR AGENT** — inserted between the calling agent and memo. Not just RAG — a filtered/reconciled/summarized RAG.
  - Session agent calls for a memory → actually calls a background agent/skill
  - Mediator: pulls raw memo corpus, filters ("not relevant for this session", "older fact contradicted by newer", etc.), reconciles conflicts in real-time on the memory, returns ONLY the actual response wanted (potentially MUCH shorter than raw memo)
  - When mediator sees issues → reports to background auditing agent for proper sweep
- **NEW REQUIREMENT — tool-call recording/observability**: record what queries are being made + what's being returned; constantly audit + improve on architectural AND content levels

**Spec constraints derived:**
- **C34**: **Provenance = first-class data structure** on every memo. Fields:
  - `claude_log_ref` — `{host, project_dir, session_uuid, line_range_start, line_range_end}`
  - `git_ref` — `{repo, sha, file, line_start, line_end}`
  - `gmail_msg_id` — thread + message id
  - `phony_ref` — `{record_type: sms|call|voicemail, record_id}`
  - `atc_ref` — `{kind: message|beacon|status, id, from, zones}`
  - `url` — for web-sourced facts
  - `derived_from` — list of parent memo IDs (for meta-rules built on other memos)
- **C35**: New tool surface — intelligent Claude Code log query (grep-based, embedding-optional). Not just retrieval but semantic filtering of the raw stream.
- **C36**: **RETRIEVAL MEDIATOR ARCHITECTURE**. Session agents don't call memo directly on the RAG path — they call a mediator skill/agent. Mediator responsibilities:
  - Filter by session-relevance (mediator knows the session's project, goals, current focus)
  - Reconcile contradicting memos in real-time (newest supersedes; fact-class updated only via operator/auditor)
  - Return the ANSWER, not the raw memo (potentially <10% of raw context)
  - Log every query + response for observability
  - Report anomalies (conflicting memos, stale memos, gaps) to background auditor
- **C37**: **Tool-call observability** — every memo tool call recorded (query, filters, results, latency, calling session id, calling agent role). Feeds auditor + spec-refinement cycle.
- **C38**: Backup posture — keep backups FOREVER for both source materials AND memo knowledge base. Backup archive is the fallback point-in-time record.

## R6-pushback drafted on (b) bi-temporal (to send)

Ben's core concern is VALID: agents shouldn't see refuted facts. But that's a READ-PATH concern, not a STORE concern.
- Backups can't answer forward queries. Auditor asking "when did the K8s IP change" can't practically parse a nightly-snapshot chain — needs the live store.
- Provenance chains BREAK at supersession without bi-temporal. Delete+replace loses the "here's what we thought before, here's what changed, here's WHY" audit trail — precisely the trail C37 tool-call recording wants.
- **Solution**: bi-temporal ON THE STORE + retrieval-mediator (C36) filters `valid_until IS NULL` on the READ path by default. Agents NEVER see refuted facts. Auditor + explicit point-in-time queries see full history. Ben's UX concern solved; audit trail preserved.
- This actually makes the retrieval mediator (Ben's own C36 idea) MORE POWERFUL, not less — it uses bi-temporal fields as one of its filters.

---

## R7 — Constitutional set + migration (2026-07-29 13:58 EDT)

**Ben:**

**Bi-temporal**: ACCEPTED my pushback. Bi-directional temporal design going in as store model, mediator filters on read.

**(a) Constitutional content**: Dispatch background agent to look at quantum-feed's constitution as the exemplar. Constitution is either `.specify/memory/constitution.md` (spec-kit) or `CLAUDE.md`. **Agents CANNOT modify the constitution — that's the whole point.** They CAN propose modifications, very high bar, operator-approved only. Constitution should rarely change. **Per-session rules may change more frequently.**

**(b) Ownership/curation**: (implicit from a) — operator owns constitution; agents propose via high-bar path.

**(c) Migration**: Migrate EVERYTHING, safely. **Build v2 in a separate working tree** with entirely separate MCP + dataset. Flip memo MCP config to point at v2, ability to flip back if broken. **Backfill process**: convert every existing memo — aggressive pruning, aggressive consolidation, expansion, splitting memos, redirecting, retagging. **Big part of the project.**

**Also**: dispatched Agent E — parking-miss deep dive on assistant sessions past week. Concrete short-term-memory example: Ben landed BOS 7/22, parking spot never made it to a pin, on return `/recall parking` returned WRONG (May SF) memo. Also uses `assistant` as a non-code use case profile.

**Spec constraints derived:**
- **C39**: Bi-temporal accepted. `valid_from` + `valid_until` on every memo. Mediator filters `valid_until IS NULL` for agents by default; auditor + explicit point-in-time queries see full history.
- **C40**: Constitution is OPERATOR-OWNED. Agents can PROPOSE additions/changes via high-bar proposal flow — never modify directly. Applies to constitutional-class memos AND the spec-kit constitution.md.
- **C41**: Constitutional memos rarely change. Per-session current-focus rules change more frequently. Two-tier maps onto R4's constitutional/current-focus layers.
- **C42**: Migration architecture = **v2 in separate worktree + separate MCP + separate dataset**. MCP config flip is the switch. Backup/rollback is native. Backfill is a distinct workstream (prune/consolidate/expand/split/redirect/retag).
- **C43**: Retrofit is a FIRST-CLASS work item, not an afterthought. Requires per-memo classification, deduping, splitting compound memos, retag against canonical vocabulary.

### Agent F findings (constitutional-practices audit) — key extracts

**quantum-feed constitution v1.5.0** is the exemplar. 40 lines, 7 numbered inviolable principles, incident-anchored governance clauses, backed by `constitution_coverage.rs` gate tests (Principle I–VII → live byte-exact test coverage). Meta-rule for admission: *"a rule belongs here only if violating it makes the system wrong or unsafe regardless of how it is built."*

**Cross-project practice survey:**
- 26 `.specify/memory/constitution.md` files found. Breakdown:
  - **7 real constitutions** (quantum-feed, mind, storybook/Inkling, pantry, buffet-buster, puppet, zg/zeitgeist) + quantum-reactor's `docs/constitution.md`
  - **5+ untouched Spec Kit templates** with `[PROJECT_NAME]` / `[PRINCIPLE_1_NAME]` placeholders — **INCLUDING memo's own** `/mnt/nas/data/code/memo/.specify/memory/constitution.md`. **First-drink-of-champagne failure — the tool that describes a knowledge store doesn't have a real constitution about itself.**
  - **~14 worktree duplicates** (qf-t108arm, qf-lb, qf-fr042, etc.) — no observable sync mechanism, drift risk
- 60+ `CLAUDE.md` files found. Most are pure reference (524-652 lines of commands + architecture, not rules). Only quantum-feed + quantum-dojo CLAUDE.md are genuinely rule-dense.
- **Only cross-project propagated pattern**: copy-pasted "Memo — Background Context" boilerplate block (present verbatim in 6+ projects).

**Four channels compared:**

| Channel | Loaded when | Editable by | Auth of | Content today |
|---|---|---|---|---|
| **Global `~/.claude/CLAUDE.md`** (188 lines) | Every session | Only Ben manually | Fleet primitives | Structural + behavioral + operational MIXED |
| **Per-project `CLAUDE.md`** | Every session in project | Any agent (drift risk) | Project domain | Variable — thin to rule-heavy |
| **`.specify/memory/constitution.md`** | Only when spec-kit runs | `/speckit.constitution` | Non-negotiable design principles | 7 real, ~14 dupes, several templates |
| **Memo corpus** | Only when explicitly `/recall`'d | Any agent | Everything else | 19 `constitution`, 2 `behavioral-rule`, 2 `operator-coaching`, 1 `hard-rule`, 1 `pinned`, 1 `anti-pattern` — **discoverability THIN** |

**Redundancies observed:**
- "Search memo first" — quadruple-stated (global CLAUDE.md + 6+ per-project CLAUDE.md boilerplate)
- Quantum-feed principles appear in BOTH constitution.md AND CLAUDE.md ("summary is a pointer, not a substitute" — self-aware of drift)
- "Enforce, don't document" — constitution.md Governance + CLAUDE.md + memos
- Global CLAUDE.md line 39 (MCP tool respawn) stated 3 different ways in same file

**Gaps observed:**
- No global governance rule for HOW an agent discovers a project constitution outside spec-kit flow
- No cross-channel version/amendment tracking (only quantum-feed has it)
- **Canonical behavioral memos tagged INCONSISTENTLY**: `hard-rule` vs `ben-hard-rule` vs `behavioral-rule` vs `constitution` — `memo_search` for one tag misses the others
- The "no-resting" rule (`5d43c4a0`, tagged `ben-hard-rule` + `no-resting-rule`) → global CLAUDE.md restates it in its own words rather than citing the memo — DRIFT

**Recommended dividing line:**
- **Memo constitutional layer OWNS**: (a) rules that must survive cross-session AND be discoverable without knowing to `/recall`, (b) project-agnostic rules currently trapped in one project's CLAUDE.md (e.g. `command grep`, `no-resting`, `pkill-hazard`), (c) canonical operator rulings with dated ratification.
- **Keep in `~/.claude/CLAUDE.md`**: fleet primitives (ATC tools, SSH ports, memo tool syntax) — the TRANSPORT layer for the constitutional layer.
- **Keep in per-project `CLAUDE.md`**: project-specific workflow rules + POINTER to the memo constitutional query for behavioral rules.
- **Keep in `.specify/memory/constitution.md`**: correctness invariants specific to THIS project's problem domain (e.g. quantum-feed's No-lookahead, mind's Descriptive-never-prescriptive) — NEVER behavioral/fleet rules.

**Concrete spec asks:**
1. **Single canonical tag vocabulary** — retire `hard-rule`/`ben-hard-rule`/`behavioral-rule` fragmentation; enforce one tag per class.
2. **Version + amendment metadata on every constitutional memo** (borrow quantum-feed's `Version | Ratified | Amended` footer).
3. **Incident-anchoring requirement** — no constitutional memo without a named ratifying event.
4. **"Loaded into every session" delivery path** — currently only CLAUDE.md files auto-load; memos require `/recall`. This is the forcible-injection gap.
5. **First-drink-of-champagne action**: replace memo's own template-only constitution.md with a real one.

**Spec constraints derived from Agent F:**
- **C44**: Canonical tag vocabulary is spec-locked. Constitutional-class = single tag (`constitutional`), not the current 4-way fragmentation. Retag on migration.
- **C45**: Every constitutional memo carries `Version | Ratified_at | Amended_at | Incident_ref` metadata. Borrowed from quantum-feed's exemplar.
- **C46**: Incident-anchoring requirement — no constitutional memo may be created without a named ratifying event (session UUID + timestamp OR git commit OR ATC event id).
- **C47**: The "loaded into every session" delivery path is the FORCIBLE-INJECTION mechanism (R4/C26). CLAUDE.md auto-loads; memos don't; the spec must add the memo path.
- **C48**: Worktree constitution duplication is DRIFT-RISK — memo's constitutional layer must be single-source (one memo, all worktrees reference by id, not copy).
- **C49**: **First-drink-of-champagne acceptance criterion**: memo project's own `.specify/memory/constitution.md` moves from template-only to a real constitution as part of this renovation.

---

### Agent E findings (assistant parking-miss deep dive) — key extracts

**Fact-corrected timeline** (contradicts the recovery memo's root-cause narrative):
- 2026-07-22 11:10:56Z (07:10 EDT): Ben posts Slack from Logan with photo `20260722_071030.jpg`. Session `e7f89519` (office assistant).
- 11:19:46Z: Session dequeues + starts processing (9-min stall lag)
- 11:21:11Z: Assistant replies: *"Locked in — Logan Central Parking Garage → 'Cape Cod' section → Level 6 → Row R"*
- 11:21:20Z: Assistant **captures datum into state JSON** at `/home/ben/.claude/state/assistant-mexico-trip-prep-2026-07-22.json` — **NOT INTO MEMO** (only 4 memo_store calls that day for that session; none for parking)
- Handoff to next session `aa9d2ec0` (7/23 16:17Z) had NO in-context handoff of the state JSON — orphaned
- **First memo write of parking**: 2026-07-26T02:08:54Z — **3d 14h 47m after Ben's message.** Ben himself unblocked it (found phone photo of level sign).

**Failed recall (7/26 01:57Z):** Ben DMs assistant `/recall parking`. Assistant:
- `memo_search`: top-3 results = **3 duplicate copies** of Matt-Sack Delta-5677 memo (migration duplicates `0c55a9a3/c664f4a1/98efbda5`, all 0.5463 score). 4th = `da610d73` (May SF memo with parking buried in body).
- 4 additional recall attempts + 4 `memo_list` window scans — nothing tagged parking
- Assistant admits: *"I've searched everything I have... I do not find a saved note of the level/row"*
- Ben provides answer himself. Recovery memo `58aff069` written 12 min later.

**Assistant vs. code-project memo pattern:**
- Ratio: **41 memo_store vs 16 memo_search past 3 days** (store-heavy, ~2.5:1)
- Content = operator logistics: appointments, receipts, refunds, family texts, medical reminders, package tracking
- **Everything stored equally durably** — no trip-lifecycle, no expiry, no auto-pin-when-relevant
- rewarm-pin / beacon usage minimal (1-2 per session)
- Assistant NOT proactively pinning short-term operator facts even when it says *"I'll surface this proactively when you land"* — no primitive backs the promise

**Root cause is HYBRID (not what recovery memo said):**
1. Datum went to state JSON, not memo (write-path failure — never made it to the searchable substrate)
2. Even if it had been in memo, no trip-scope pin would have surfaced it proactively
3. Even if it had been surfaced, the ranking would have lost to migration duplicates + a stale SF memo
4. No self-healing: "recall failure" wasn't fed back into ranking

**Spec constraints derived from Agent E:**
- **C50**: **NEW MEMO CLASS — `trip-scoped` / `time-scoped`**: `trip_scope = {trip_id, start, end}` block. Auto-pin at trip-start, depin at trip-end. Anchor on gcal event ids. Would have solved the parking incident.
- **C51**: **Ranking model needs recency + tag-class boost for logistics families.** Formula candidate: `semantic × 0.5 + recency_decay(created_at) × 0.3 + tag_class_match × 0.2`. Prevents stale-facts winning over fresh-facts in operator-logistics recall.
- **C52**: **Migration-duplicate reaper mandatory.** Corpus has confirmed clusters like `0c55a9a3/c664f4a1/98efbda5` (three identical Matt-Sack memos) that pollute recall. Merge to canonical single, keep other IDs as redirects.
- **C53**: **Storage-class auditor** — watch for writes to `~/.claude/state/*.json` or `assistant-*-prep-*.json` containing trigger phrases (parking, PNRs, codes). Auto-promote to memo OR flag as new `BURIED_HIT_STATE_JSON` bucket. Extends existing `A.1.4` capture-miss detection.
- **C54**: **Answer-loop audit** — every `/recall X` logs `(query, top-k, user-next-turn)`. If user next turn is a correction ("no that's wrong") or self-answer, mark memos that WOULD have been correct (via post-hoc match against subsequent `memo_store` calls) and re-rank in a "recall corrections" index. Would have auto-caught this incident.
- **C55**: `assistant` (non-code) is a distinct use-profile from code projects — **operator-logistics-heavy, store-heavy, short-lifecycle**. Spec must recognize this profile explicitly and design primitives for it (trip-scope, expiry, auto-pin from calendar events, receipt/appointment classes).

---

## Interview state at 2026-07-29 14:10 EDT
All 6 background agents done, all findings banked. Interview complete through R7.
Ready to move to spec-kit drafting phase — constitution.md first, then baseline spec.

Cross-cut themes emerging:
- **Storage classes (brain-inspired taxonomy)** — behavioral, goal, fact, verbatim-critical, decision-in-progress, ephemeral-flush; each with distinct lifecycle + injection mode
- **Injection mode is a first-class field** — forcible (behavioral, goal, verbatim-critical) vs. on-recall (fact) vs. on-procedure-match (procedural)
- **Provenance / source-linking** — memo → Claude Code log line / git SHA / Gmail msg / ATC event; steal Graphiti bi-temporal edges
- **Discovery anchor = GUIDE + BEACON, not agent initiative** — deterministic guide-named tags + beacon pointers; never "hope agent thinks to search"
- **Compaction target = LOOP agents** (300-450M cache-read/day), not campaign agents (self-manage). Repurpose the atc-precompact.sh no-op as real flush trigger.
- **Corpus rot is operational blocker** — reconciliation must be aggressive; supersede/dedupe with bi-temporal validity
- **INDEX LAG is first-class invariant** — full-UUID + settle window mandatory
- **Memo↔ATC coupling** — flush-and-forget only works if paired with beacon pointer; auto-pin-on-memo_store is ~50 LOC change

---
