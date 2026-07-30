# Seam decisions with ATC and the agent coordinator

Worked out 2026-07-30 with the `agents` supervisor during agentkit's research
interview. Recorded because these are decisions, not accidents — and because
each is justified by a *property*, so if the property stops holding, the
decision should be revisited rather than defended.

## 1. Who decides a session action is safe

**memo decides whether an action is WORTH doing** — bloat, context pressure,
staleness. **The coordinator decides whether it is SAFE**, and owns the single
definition of "idle".

It has the process-level view (tmux pane state, real last-activity, whether a
turn is in flight); memo only ever had a proxy handed to it in an observation.
Two definitions of idle is worse than the usual duplicated-constant trap,
because the failure mode is **destroying a session's in-flight work** rather
than returning a bad result.

Consequence: `shadow.py` drops `is_idle`. memo asks; the coordinator may veto.

## 2. Re-warm: two paths, split by TIMING — not one merged path

The coordinator initially proposed unifying re-warm under its `inject()`, with
memo and ATC as content providers. The diagnosis was right — two actors on one
session is a place "what was this session doing" can disagree — but the fix
would have cost a property that matters.

**The property:**

| path | mechanism | when content arrives |
|---|---|---|
| memo's `POST /hooks/post-compact` | session startup hook | **before the model's first turn** |
| coordinator `inject()` | tmux send-keys | **as a turn**, after the session is running |

For session working state that difference is irrelevant. For **constitutional
and behavioral rules it is the whole point**: a standing rule that arrives one
turn late is a correction, not a guardrail — the session may already have acted.

Secondary: routing all re-warm through the coordinator makes re-warm fail when
the coordinator is down. The hook is self-contained.

**Resolution — fix the SOURCES so they never overlap, rather than merging two
actors:**

| content | source | actor | timing |
|---|---|---|---|
| constitutional + behavioral rules | memo | memo hook | before first turn |
| session working state, operator pins | ATC | coordinator `inject()` | as a turn |

No overlap → no merge → **nothing to drift.** Structural elimination beats
detection, which has been the right answer three separate times in memo today.

`inject()` is explicitly NOT the vehicle for constitutional rules.

**This rests on ONE property and is not memo defending a job.** If Ben decides
rules-one-turn-late is acceptable, the coordinator's unification is the better
design and this split should be removed.

**Open, pending Ben:** deleting memo's flush slots. If flush stays, the
coordinator's merge-point argument is much stronger and memo should probably
concede.

## 3. Residual risk memo cannot close for itself

memo's hook being coordinator-independent is the robustness win — but it also
means **if memo is down, standing rules silently do not arrive**, and nothing
detects it. A session running unconstrained because a hook 500'd looks entirely
normal.

Asked the coordinator to observe "did this session receive its injection at
startup" as part of its session telemetry. memo cannot self-report an absence.

## 4. Naming

"Conductor" is memo's internal name for the **ATC** seam — not a fourth system.
memo has two provider seams: `Conductor` → ATC, `AgentController` → `agents`.

## 5. Interface churn is a non-issue

Both seams are protocols with swappable implementations. When ATC and agentkit
ship their v2 shapes, memo adapts with a new adapter file and a config change;
nothing in memo's core moves. **They should design what is right for their
systems and not bend to memo's current method names.**

## 6. Redundant DELIVERY is fine; redundant STATE is not

Named by ATC 2026-07-30, and it resolves an apparent contradiction someone will
eventually raise: memo's `flush.py` was deleted for duplicating ATC's job, yet
the re-warm design deliberately keeps a beacon backstop alongside
event-triggered delivery. Those look like the same thing and are not.

- **Two paths DELIVERING the same content cannot disagree.** They are
  idempotent, and now that `message_id` reaches receivers a duplicate is
  detectable and discardable.
- **Two mechanisms STORING session state can silently diverge**, with nothing
  reconciling them and no way to tell which is right.

`flush.py` was the second kind. The beacon backstop is the first.

The backstop earns its place for a specific reason: **it does not share the
primary path's dependencies.** Event-triggered re-warm needs the coordinator up
and the telemetry fresh at the moment of compaction; a redelivering beacon
eventually reaches its target even when the coordinator is the thing that
broke. That is the one failure event-triggered delivery cannot cover, which is
exactly what a backstop is for.

**Correction on the cost that prompted this:** memo reported a rewarm beacon
costing ~1.5k tokens per redelivery at 85% context, and concluded the cost
curve was inverted — insurance billed hardest when it is least affordable. The
principle stands, but **the magnitude was inflated by a bug**: ATC's scheduler
held a snapshot across delivery and wrote it back afterwards, so an ack landing
mid-iteration was overwritten and the beacon kept redelivering with no ack able
to stop it. Fixed 2026-07-30. Do not calibrate anything against that number.
