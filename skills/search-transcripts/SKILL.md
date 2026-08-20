---
name: search-transcripts
description: >-
  Run a search over Claude Code session transcripts and return raw results. This
  is the mechanism layer — it searches and prints, it does not reason about what
  it found. Used by the `transcript-recall` subagent, and by `memo-writer` when a
  claim needs evidence the caller did not hand over. A session that wants an
  ANSWER about past sessions should use /recall-transcripts instead, which spawns
  the subagent and gets back a cited finding.
argument-hint: "<query> [--role-aware | --passages] [scope flags]"
disable-model-invocation: false
---

Two engines, both in `/mnt/nas/data/code/memo/scripts/`. They answer different
questions and **neither can do the other's job** — pick by what you need.

| you need | use | because |
|---|---|---|
| what someone SAID — operator vs agent vs tool output | `transcript-search` | it filters by role |
| the relevant discussion, ranked, within a token budget | `transcript-passages` | it scores passages |

---

# `transcript-search` — role-aware

```bash
S=/mnt/nas/data/code/memo/scripts/transcript-search

python3 $S "free embeddings" --instructions-only -i      # what the OPERATOR said
python3 $S "cutover" --responses-only --project memo     # what an AGENT said back
python3 $S "rm -rf" --role tool_use --all-hosts          # what was actually RUN
python3 $S --list-sessions --project memo                # enumerate, no search
python3 $S "qwen3" --session ea469f16                    # ONE session, the safe selector
```

⭐ **Why this cannot be grep.** A `tool_result` carries `type: "user"`, exactly as a
human instruction does. So *"what did Ben actually ask?"* is not a text search —
measured on one project, the same query returned **71 instruction matches, 160
tool_result matches, 31 response matches.** A grep hands you all 262 in one pile.

**Roles:** `instruction` · `response` · `thinking` · `tool_use` · `tool_result` ·
`system` · `any`. Default `instruction response`.
**Scope:** `--project <substr>` · `--session <id-or-prefix>` · `--host <h>` ·
`--all-hosts` · `--since YYYY-MM-DD` · `--no-subagents`.
**Bounds:** `--max-matches` (50) · `--max-chars` (400) · `-C` (120) · `-i` · `-F` ·
`--json`.

# `transcript-passages` — passage-ranked

```bash
P=/mnt/nas/data/code/memo/scripts/transcript-passages

python3 $P "<question>" --path <dir> --since 24h --hosts all
```

Enumerates transcripts, drops hook transcripts, strips noise and tool dumps,
dedups replayed cron prompts, ranks passages, returns them with host, project,
session id and timestamp.

**Options:** `--path <dir>` (default cwd) · `--all-projects` · `--since`/`--until`
(`7d` `36h` `2026-08-01`) · `--hosts all|local` · `--budget N` (chars, 24000) ·
`--min-score N` (4.0) · `--json`.

⭐ **Ranking is lexical** — it rewards the vocabulary in the transcript, not the
vocabulary of the question. Re-query with the error string, the flag name, the
filename, the person's own phrasing. Quote distinctive phrases (`"AUTH_FAILED
loop"`) for a large exact-match bonus. Two or three cheap queries beat one broad
one.

---

## ⛔ Reading the output honestly

⛔ **SELECT BY SESSION ID, NEVER BY A SEAT'S NAME.** Several seats share one project
directory (`memo` and `memo-llm` both live in `-mnt-nas-data-code-memo`). Searching
a seat's *name* finds whoever **mentioned** it — `agents` did that and got a
different seat's 92MB transcript, **913 confident-looking hits about the wrong
session.** ⭐ A too-broad search returns *false abundance*, not a null, and a large
number invites belief where a zero would invite suspicion.

⛔ **THE LAST LINE IS THE DENOMINATOR.** Every run prints `N match(es) across M
session(s)`. **"0 matches" is meaningless without M.** If M is 0 that is a coverage
failure, not a quiet corpus. ⭐ A coverage bug looks exactly like a quiet day —
memo-minder once scanned 17% of sessions and reported clean, because `find` does
not follow symlinks and these project dirs are symlinks into `/fast4`.

⚠️ **An ssh failure is a HOST PROBLEM, never an empty result.** `⚠️ office: ssh
rc=…` means nothing was looked at there. Do not read it as "nothing found".

⚠️ **`OUTPUT TRUNCATED` means the first N, not the best N.** There is no ranking in
the role-aware engine — raise `--max-matches` or narrow the query before concluding
anything about frequency.

⚠️ **Every result names its host, and that matters.** `~/.claude` is HOST-LOCAL, so
the same project slug holds **different content** on office / server4 / server5 /
server3. A finding without a host is unattributable, and a seat that migrated hosts
has its history split across two of them.

⛔ **Do not mine hook transcripts.** A `claude -p` hook transcript's first user turn
is the hook's own prompt — the instrument describing itself. Dropped by default;
leave them dropped.
