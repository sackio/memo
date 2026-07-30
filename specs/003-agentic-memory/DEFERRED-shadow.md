# DEFERRED: session shadow / auto-memorization

**Status:** parked 2026-07-30 pending the agent coordinator. Operator decision.

## The idea

A per-session shadow that fires periodically — every N turns, or on context pressure — and:
memorizes durable facts, corrects things it previously stored, revises the rewarm pin, and
triggers a compaction. The point is that a session which continuously distills can be compacted
**earlier and more often**, because compaction stops being lossy. That is how agents stop
accumulating crust.

## Most of it already exists and is unwired

| piece | where | state |
|---|---|---|
| session observer (transcript bytes, turns, cache reads, idle) | `auditor/shadow.py` | built, **called by nothing** |
| distillation into 6 slots — active-threads · in-flight-work · pending-dms · open-tasks · key-decisions · follow-ups-owed | `flush.py` | built, 24h TTL |
| rewarm from the previous generation's slots | `POST /hooks/post-compact` | built, working |
| compaction with an idle gate | `agents --compact` | live today |
| durable-fact promotion | — | **the real gap** (memo-judge's job; never ran) |

## Why it is parked, not abandoned

Researched against the Claude Code docs 2026-07-30. Every in-session route is blocked:

- **Hooks cannot see context usage.** No token count, no % filled. Only the **status line**
  receives `context_window.used_percentage` — hooks do not.
- **No turn-count trigger**, and hooks are not even given a turn number.
- **A hook cannot trigger `/compact`.** Documented explicitly: *"Command hooks … can't trigger
  `/` commands or tool calls."*
- **`PreCompact` can only block**, not shape what the summary keeps. Steering compaction is out.
- **`type: agent` hooks are verdict-only** — the subagent's work returns `{ok, reason}` and never
  becomes context.

A workaround exists (status-line script as the pressure sensor, writing to a state file a `Stop`
hook reads) but it is construction, not configuration: a data source used as a sensor it was not
meant to be, a hook that can nudge but not act, and an external script to do the actual work.

**The agent coordinator makes all of it unnecessary.** With tmux access it can send `/compact`,
`/memorize` or anything else into any session — the platform will not let a session act on
itself, but an outside process typing into its pane can. It can also carry the pressure signal
directly, so the status-line smuggling is not needed either.

## When it is picked up

Build it on the coordinator, not on hooks. The four actions map onto existing parts —
`memo-memorize`, `memo-prune`, `/rewarm-pin`, `agents --compact` — so this is a **scheduler over
existing pieces**, not new machinery.

The one genuinely new judgment, and the reason it belongs to an agent rather than a threshold:
**is this ephemeral working state, or a durable fact?** Working state → flush slot, 24h,
rewarmed after compaction. Durable fact → memo, permanent. Wrong in one direction and memo fills
with session noise; wrong in the other and real knowledge evaporates at every compaction.

If the platform ever exposes context usage to hooks directly, revisit — most of the workaround
should then be thrown away.
