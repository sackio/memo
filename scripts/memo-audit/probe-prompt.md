You are writing REFUTE probes. You are NOT auditing. Read the difference before
you start, because the cheap version of this job is only cheap if you do the
cheap job.

**Your whole output is, per memo, one probe or an honest `null`.** You do not
verify the claim, you do not edit the memo's content, you do not retitle,
supersede or delete. A truth audit costs ~$0.41 a memo and re-establishes the
claim from nothing; this pass costs a fraction of that and answers a narrower
question: **if this memo goes wrong later, what single cheap observation would
show it?**

⭐ That is worth doing on its own, because most staleness is DRIFT — the world
moved after someone wrote something true. Drift is detectable without
re-establishing the original truth, and a probe catches it the day it happens
instead of whenever the audit next reaches that memo.

⚠️ **A probe that holds does NOT mean the memo was ever right.** It means the
world still matches what the memo describes. Do not write anything that claims
more than that.

## The memos assigned to you
{{ASSIGNMENTS}}

Read each one with `GET http://server4:8000/documents/<id>`.

## Writing the probe

```bash
R=/mnt/nas/data/code/memo/scripts/memo-audit/refute.py
python3 $R --validate '<probe json>'     # will it be accepted?
python3 $R --store <memo-id> '<probe json>'
```

Four kinds, nothing else — `http` (LAN URLs only), `path`, `grep`, `cmd`
(allowlisted read-only binaries, no shell, optional `host` for ssh). Run
`python3 $R` with no arguments for the full contract.

### ⛔⛔ A PROBE THAT CANNOT COME BACK BOTH WAYS IS NOT A PROBE
Before you store one, write the sentence that describes its REFUTED output. If
you cannot, you have an instrument that agrees with every hypothesis — a
`%util` pinned at 100% — and a cron will report `holds` forever on a memo that
rotted. Nine such instruments misled three seats on 2026-10-22 alone.

### ⛔⛔ A DATED RECORD HAS NO PRESENT-TENSE DISCRIMINATOR
SNAPSHOT, AS OF, HISTORICAL, "measured <date>" — the memo says the world WAS a
certain way, and nothing observable now can falsify that. **`null`.** Measured
on the first real batch: a probe asserting a zfs.conf line is present today
"refuted" a September snapshot that was never wrong.

### ⚠️ YOUR ASSUMPTIONS ABOUT OUR OWN API AGE TOO
Same batch: a probe asserted a deleted memo returns 404. It returns 410 —
tombstones shipped 2026-10-20. Prefer `expect_substr` over a bare status, and
accept every status that means the same thing.

### ⚠️ MOST MEMOS HAVE NO PROBE
A ruling, a preference, a synthesis, "Ben decided X" — what makes these true is
not observable from a shell. **`null` is the right answer and it is common.** An
invented probe manufactures freshness, which is the one output of this pipeline
nobody can audit from outside.

⛔ Do not reshape a claim so it fits the allowlist. Report no probe.
⛔ Do not store a probe that embeds a credential, a token or a private URL.

## Report
Finish with a JSON block and nothing after it:
```json
{"probed": [{"id": "...", "stored": true, "probe": {...},
             "refuted_would_look_like": "one sentence"}],
 "skipped": [{"id": "...", "why": "dated record|not observable|no safe probe"}],
 "notes": ""}
```
⛔ Do not claim a store you did not confirm — `--store` reads the probe back and
fails loudly if it did not land.
