# Proposal — agent-driven truth verification and pruning for the v2 corpus

Status: **PROPOSAL, nothing built.** Written 2026-08-02 in response to Ben's
directive of the same day, 09:48 EDT:

> "do everything up to the cutover, do not do the cutover, I want to do more
> rigorous tests after, and also see how well we can prune / maintain v2 with
> agents checking for truth verifications"

Not yet numbered as a feature. `003-agentic-memory` is taken (recall/memorize
skill layering) and `004` is reserved for the raw transcript corpus (memo
`59db94a8`), so this would be `005` if it proceeds.

---

## 1. The measurement that has to come first

The obvious build is "point agents at the corpus and have them check things."
The question that decides whether that is worth anything is **what fraction of
the corpus contains a claim a machine can actually settle**, and that is
measurable today rather than after building.

Measured 2026-08-02 against the live v2 corpus, n = 7,336:

| signal | memos | share | what a probe would do |
|---|---:|---:|---|
| `abs_path` (`/mnt/…`, `~/…`) | 1,438 | 19.6% | stat the path |
| `ipv4` | 954 | 13.0% | ping / connect |
| `srcref` (`file.rs:568`) | 884 | 12.1% | check the line still says that — **against git, not the network** |
| `host_port` | 533 | 7.3% | connect |
| `url` | 496 | 6.8% | fetch |
| `kubectl` / `k-barn` | 229 | 3.1% | run the command |
| `ssh user@…` / port 4999 | 48 | 0.7% | connect |

Rolled up: **22.7% carry a network/service-probeable signal, 12.1% a
git-checkable code reference, and 40.7% carry at least one of those or a
filesystem path.**

⚠️ **Read those as an upper bound on probe coverage, not as probe coverage.**
Two corrections are already baked in, and a third is not fixable by regex:

1. **`host_port` was 17.2% on the first pass and is 7.3%.** The pattern was
   matching `runner.rs:568`, `gates.rs:66-73`, `daemon.rs:1870` — source
   references, not services. That contamination was over half the class. It is
   split out as `srcref` above because it is a genuinely different verification
   family (a git repo, not a socket), not because it is noise.
2. **Presence of a token is not a claim about the present.** Of 15 sampled
   spans read by hand, one was `123.56.159.92` in a security-incident memo
   describing beaconing that happened on 2026-07-10. That address SHOULD fail a
   liveness probe. A verifier that "corrects" it has corrupted the record.
3. The same hand sample put genuine service claims at roughly 4/15 and source
   references at roughly 9/15 — **the corpus's mechanically checkable content is
   dominated by code references, not infrastructure**, which is the opposite of
   what the "probe the IPs" framing assumes.

⇒ **Deliverable zero is an honest coverage number**, produced by the extractor
described below rather than by regex, with the historical-claim class labelled
and excluded. Everything downstream is scoped by it. A verification system that
reaches 20% of the corpus while reporting "corpus verified" is the same lie as a
passage bench that scores un-indexed memos as absent — the shape this repo has
already been burned by three times.

---

## 2. Design

### 2.1 Claims, not memos

A memo is not true or false; the individual assertions inside it are. So the
unit of verification is a **claim** extracted from a memo:

```
claim(id, doc_id, kind, subject, asserted_value, probe, extracted_at)
```

`probe` is a concrete, re-runnable observation — a URL to fetch, a socket to
open, a `git show` on a `file:line`, a `stat`. Not a description of one.

⭐ **A claim without an executable probe is not a claim, it is a note.** It gets
`kind = unprobeable` and is counted, never guessed at. The count is a headline
number, not a footnote.

### 2.2 Probes run without an LLM; agents run only on failure

The scheduled sweep is cheap code: run every probe, record PASS / FAIL / ERROR
with the observed value and a timestamp. No model in the loop, so it can run
often and its cost does not scale with corpus size in tokens.

An agent is spawned **only** for a FAIL, and its job is narrow: decide between

- **correct** — the world moved, the memo should now say the observed value;
- **historical** — the memo was true when written and must be left alone
  (tagged so the probe is never re-run against it);
- **retire** — the subject no longer exists; supersede the memo;
- **conflict** — ⭐ the corpus and the operator disagree. Added 2026-08-02 after
  the first sweep produced exactly this and the original four verdicts had
  nowhere to put it. Told the container registry at `192.168.1.42:32000` was
  refusing connections, Ben said server4 was the real registry and should be
  that for every host — a directive a patching agent could act on immediately.
  The corpus already held three mutually consistent memos recording a
  deliberate June migration of the barn cluster registry to `nas:5050`, for HA,
  across 51 nodes and 140 manifest refs. **The probe was right that the address
  was dead and said nothing whatever about which live address replaces it.**
  A failing probe never can: the operator's answer may express intent rather
  than describe the world, and no observation separates those two. Escalate,
  and quote the memos back;
- **unclear** — escalate to Ben rather than guess.

⛔ **Before any repoint, verify the replacement contains the thing being
repointed to.** Here that was literal — 2 of the 26 affected memos cite images
that exist ONLY on the registry being migrated away from, so the "obvious" edit
would have been wrong on its face. A correction that is not itself verified is
just a differently-wrong memo written with more confidence.

### 2.3 Nothing is hard-deleted

v2 already has `valid_until`. Every outcome is a supersede or a patch that
records what was observed and when. Pruning that cannot be read back is not
pruning, it is loss with better branding.

---

## 3. The three failure modes this design exists to prevent

1. **Self-verification.** An agent asked "is this memo true?" will re-read the
   memo and agree with it. Verification must compare against a source the memo
   did not produce — that is the entire reason a `probe` field is mandatory and
   why the sweep is code rather than a prompt.
2. **Correcting history.** Incident reports, post-mortems, and "as of <date>"
   records are supposed to describe a world that no longer exists. Auto-correcting
   them destroys exactly the memos whose value is that they are dated. The
   `historical` verdict is a first-class outcome, not an error case.
3. **Coverage illusion.** See §1. Report `verified / probeable / total` as three
   numbers, always, and never collapse them into one percentage.

---

## 4. Smallest useful first slice

Not the whole system. One family, end to end, so the shape is testable:

**Network/service claims only** — `ipv4`, `host_port`, `url`, `ssh` — which is
the 22.7% band and the one whose probes are unambiguous and cheap.

1. Extractor over that band, producing claims with probes.
2. Sweep script that runs them and writes results (no LLM).
3. Report: how many claims, how many probeable, how many currently failing.
4. **Then stop and look at the failures with Ben before wiring any automatic
   patching.** The first sweep's failure list is the real design input — it will
   contain historical claims, deliberately-dead hosts, and genuinely stale memos,
   and guessing that ratio in advance is what §1 already shows I would get wrong.

---

## 5. Open questions for Ben

1. **Blast radius.** Should the first sweep be allowed to patch anything at all,
   or report only? (My recommendation: report only, until the failure list has
   been read once.)
2. **Where do code-reference claims (12.1%) point?** Verifying `runner.rs:568`
   needs the repo checked out at a known commit. Worth it, or out of scope?
3. **Cadence.** Nightly is affordable since the sweep has no model cost. Is
   nightly what he wants, or on-demand?
4. Does this become feature `005`, or fold into `003-agentic-memory` as its
   third agent alongside recall and memorize?
