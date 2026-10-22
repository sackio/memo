# slack-archive

Archives Slack channel history to `/mnt/nas/data/slack-archive/<channel>/<YYYY-MM>.ndjson`
so a **script** can mine it instead of an LLM pulling one channel at a time through
the API.

## Why Slack and not just transcripts

Ben, #agents 2026-10-14 07:21: *"use these credits more aggressively to maintain and
improve memo, using the slack conversations as a guide and verifying memo facts."*
Restated #memo 2026-10-22 11:55: *"slack is likely to be a very primary source for
understanding what I want and what issues have arisen and where truth needs to lie."*

⭐ **Session transcripts record what agents DID; Slack records what the operator
DECIDED, and those diverge.** Measured 2026-10-22, three relayed instructions differed
from their source and reading the channel is what caught each one: Console credit
figures relayed end-for-end inverted (they were *remaining*, not used), a pause start
date that was a seat's conditional plan rather than a ruling, and a mechanism this seat
refuted that the source had right all along.

## Schedule

Hourly at `:23` in server4's crontab, under `cronguard` + `flock -n`, `--days 2` so a
missed hour self-heals on the next run. Added 2026-10-22 — **the script existed from
10-14 and was never scheduled**, so the archive sat stale for days with nothing to say so.

## ⛔ Coverage limit — cannot be inferred from the output

The bot token sees only channels the bot was **invited to**. A channel absent from the
archive is **NOT SEARCHED**, not empty. 141 channels covered as of 2026-10-22.

## Credentials

Read at runtime from `/home/ben/.config/atc-slack-bridge/env` (mode 0600, host-local to
server4). Never printed, never logged, never written into the archive. `SLACK_BOT_TOKEN`
is the only one with `channels:read` + `channels:history`; `SLACK_USER_TOKEN` returns
`missing_scope` for both (measured 2026-10-14).
