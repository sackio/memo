# Consensus and source weighting — what to build, and what not to

**2026-08-06.** Follow-on to `2026-08-06-weighted-retrieval-scout.md` (the scout pass) and
`2026-08-05-knowledge-base-audit-and-proposal.md` (the estate audit).

Written at Ben's request after he agreed sentiment is the weak leg and gave a better reason
than the one I had. Nothing here is started; this is a design write-up.

✅ Status: `mind` answered with measurements (2026-08-06 10:08). **They inverted §3's premise
and I have rewritten it.** Their numbers are load-bearing throughout and are attributed where
used.

⛔ **Correction, recorded because I had it wrong in my own pin: `mind` is NOT stood down.**
Ben revived that seat on 08-03; what is live there is the token drought, same as here.

---

## 1. The organizing principle, which is Ben's

> *"This is an LLM world where sentiment does not need to be black and white — the LLM itself
> can interpret, based on what it retrieves, what it thinks is the best sentiment."*

That is a sharper argument than my stance-vs-sentiment reframe from the scout pass, and it
generalizes past sentiment into a rule that decides the whole design:

⭐ **KEEP THE CHUNK. DON'T PRECOMPUTE CONCLUSIONS THE CHUNK ALREADY SUPPORTS.**

⚠️ **This was stated as "store what the reader cannot reconstruct" in the first two drafts,
and that phrasing is wrong in a way worth recording, because I then acted on the wrong
version twice.** It uses one word — *store* — for two different decisions:

| decision | what it asks | answer |
|---|---|---|
| **What is worth keeping at all?** | is this article/memo/chunk worth the shelf space? | **that is what mind's scan is for** — reconstructibility has nothing to do with it |
| **What do we hang off it once kept?** | does this scalar earn its schema? | **the rule above** |

Read as the first, the rule argues against keeping raw material because a model *could* read
it — which is nonsense, and is the reading Ben corrected. The rule governs **derived
annotations only.** Content whose conclusions are reconstructible is precisely the content
worth storing; that reconstructibility is the entire reason to keep it.

⇒ **Restated: the vectorized chunk, when retrieved, can be read by an LLM to draw the
conclusion. So keep the chunk and skip the scalar.**

A precomputed scalar is only worth its storage, its staleness risk, and its schema if the
model reading the retrieved text could not have worked it out itself. Sentiment fails that
test — an LLM handed five articles is *better* at judging their tone than any score we could
have frozen at index time, and unlike our score it can judge tone *relative to the question
being asked*. A stored sentiment scalar is a worse answer computed earlier.

### The worked example, and what it is actually an example of

`mind` has a sentiment field, and I used it here as the case against precomputed scalars.
**Two rounds of correction from them established it is not that case at all.** The corrected
facts, measured by mind on 2026-08-06 (trailing 30d):

| vendor | rows | with sentiment |
|---|--:|--:|
| `benzinga_archive` | 14,719 | **0** |
| `polygon` | 13,791 | **4,107 (29.8%)** |

**It is vendor-supplied, by polygon only — mind does not compute it.** And it is **not a
scalar**: a live row is per-ticker with reasoning prose (*"The company is the subject of a
securities class action lawsuit alleging…"*).

⇒ **That puts it on the *derive* side of the line, not the store side.** It is text an LLM
can read, which happens to have arrived pre-written. Filing it as "a stored sentiment scalar"
was filing it under the wrong case, and both recommendations I drew from it were wrong:
*backfill it* was not merely expensive but **impossible** (mind would have to author
judgements the vendor never shipped), and *retire it* means discarding free vendor data on
evidence that does not support the call.

⇒ ⭐ **What it IS an example of — and this is more useful than the point I was trying to
make: a partially-populated field whose presence correlates with a hidden variable.**

> **Partial coverage is bad. Partial coverage correlated with source is worse, because the
> surviving set is systematically non-representative rather than merely small.**

Filtering on `sentiment IS NOT NULL` does not sample 30% of the tape — it **silently selects
polygon-sourced articles only**. `NULL` still reads as neutral (the `alpaca` hazard, which
stands), but underneath that there is a selection bias that would survive even if someone
"fixed" the neutral-reading bug. mind has warned `alpaca` on both.

⇒ **The supportable recommendation is consumer-side and stops there:** `unscored` must be
distinguishable from `neutral`, **and** presence must not be used as a filter, because
presence encodes vendor. Dropping the column is a separate call this evidence does not make.

⚠️ **Recorded because the error is instructive on both sides.** mind's first account —
a flat ~140–160/day against varying volume — read as a capped producer. It was vendor mix,
and one `GROUP BY` away. In their words: *"I measured the shape of the output when the
question was about the world that produced it"* — the same error I had named in myself an
hour earlier, in §3. **Two independent seats committed it on the same afternoon, on
different data, and neither caught it without being asked a question that forced the
disaggregation.**

**Applied to annotations we might hang off a stored chunk — never to whether the chunk itself
is worth keeping:**

| Candidate annotation | Reconstructible from the retrieved chunk? | Verdict |
|---|---|---|
| Sentiment / tone | **Yes** — and better, and question-relative | **derive** |
| Stance on a specific claim | **Yes**, given the text | **derive** |
| Recency of a document | **Yes**, trivially, from the timestamp | **derive** — and see §2 |
| *Which* sources exist at all | **No** — everything not retrieved is invisible | **store** |
| How many sources corroborate | **No** — depends on what was *not* returned | **compute at index** |
| Whether those sources are **independent** | **No** — needs cross-corpus comparison | **store** (§3, the hard one) |
| A source's track record | **No** — needs history *and outcomes* | **store, if an oracle exists** (§4) |

⇒ **The dividing line is not "hard vs easy" but "inside vs outside the retrieved set."**
Everything a retrieval hands the model, the model can judge. Everything about the *shape of
the corpus the retrieval was drawn from* is structurally invisible to it — and that is
exactly where consensus lives. Ben is right that consensus is the important half; this is
why.

## 2. The corollary that saves us work: annotate, don't weight

⭐ **The consequence of §1 that I did not expect and think is the most useful thing here:
if the model can do the weighing, we should stop trying to weight and start trying to
inform.**

The instinctive design is a scoring knob — `score = sim(q,d) × λ(source) × decay(age) × …` —
and then a tuning problem. Three independent pieces of evidence say don't:

1. **The recency ablation** (scout pass §1): the one project in the agent-memory genre that
   measured its decay weighting found **0pp across 500 questions** and excluded decay from
   ranking in the shipping product.
2. **`groton`'s RRF weights are unvalidated by their own account** — a working hybrid ranker
   whose constants nobody can defend.
3. **Our own R-25** (−8.8pt): the intuitive knob made things worse.

Every one of those is a case of a scalar multiplier that was reasoned about but not measured.
Meanwhile the alternative costs almost nothing:

> Put the fact **in the context** instead of in the score. Not `λ=0.7`, but a line the model
> reads: *"3 independent sources; 7 documents total; 5 of the 7 trace to one wire story."*

This has properties the multiplier does not. It is **inspectable** — you can read why an
answer was confident. It **degrades honestly** — a wrong independence estimate produces a
visibly wrong annotation rather than a silently wrong rank. It needs **no tuning constants**,
so there is nothing to overfit and no weight to defend. And it composes with the model's own
judgement instead of fighting it: the model already knows that five rewrites of one wire
story are one opinion, *if you tell it that's what they are*.

⇒ **Recommendation: build the signals, expose them as text, and do not touch the ranker
until something measures that a rank change is needed.**

## 3. Source independence — I had the right worry and the wrong problem

I predicted syndication: many outlets reprinting one wire story, inflating corroboration
counts. I asked `mind` to check. **The measurement inverted it, and the inversion is the most
important finding in this document.**

**mind's corpus, measured 2026-08-06:**

| | |
|---|---|
| Last 24h by publisher | Benzinga **2,187** · GlobeNewswire 86 · Motley Fool 64 → **93.6% one publisher** |
| Distinct publishers, *entire* 2.49M-article corpus | **4** |
| Exact-title duplication, same 24h | **1 duplicated title — 2 rows out of 2,337** |

⛔ **SCOPE CORRECTION — Ben, 2026-08-06: *"mind has tons of publishers, and youtube channels
to boot — i'm not asking you to assess its universe, i'm asking to build what i want."***

**He is right and the error is mine twice over.** The table above is *one news table over 30
days*. I reported it as mind's source universe, which it is not — there are more publishers
and the YouTube creators besides. And I then used that over-broad reading to declare his
request **blocked**, which is feasibility assessment he did not ask for.

⇒ **What survives is the trap (below), which is a real and general finding. What does not
survive is the conclusion I drew from it.** A narrow measurement generalised into a verdict
on a whole system is the same error in a new coat: I measured what was in front of me and
reported it as a property of the world.

### ⛔ The trap, which is the part worth carrying to every other project

**The near-duplicate detector I proposed in the first draft returns CLEAN on mind's corpus —
2,336 distinct titles out of 2,337 — and clean reads as independence.**

It is not independence. It is *evidence there is nothing to syndicate from.* In mind's
words: **a dedup check measures duplication and gets read as provenance, and those two come
apart exactly when concentration is highest.**

⭐ **A duplication check cannot distinguish "many independent sources agree" from "one
source, said once." Both return zero duplicates. Identical metric, opposite meaning — and
the healthy-looking reading is the wrong one precisely when the corpus is most degenerate.**

⚠️ **My proposed test would have fired correctly and taught me the wrong lesson.** §5 step 1
originally said: *"if under ~10% of documents collapse, my premise is wrong and steps 2–3
should not be built."* On mind's corpus that returns ~0%. I would have stopped — for the
right *action* and the wrong *reason*, concluding "no syndication problem here" when the
truth is "no source diversity here," which is far worse and demands the opposite response.
A stopping rule that is right about what to do and wrong about why will mislead the next
person who reads it. **This is my characteristic error in its purest form: I measured the
process (are there duplicates?) when the discriminating question was about the world (how
many independent sources exist at all?).**

⇒ ⛔ **THE RULE: count distinct SOURCES before trusting any agreement metric.** Source
diversity is the precondition that makes corroboration meaningful; without it, every
consensus number downstream is arithmetic on a single opinion.

### And the count itself is optimistic

⚠️ mind flags something they cannot yet quantify and which I would not have thought to ask:
**Benzinga carries PR-wire reprints (GlobeNewswire, Business Wire) under its own label.
Provenance collapses at ingest.** Real syndication therefore survives *inside* a single
publisher string, invisible to any publisher-level count — including the 93.6% above.

⇒ **That figure is a floor on concentration, not a ceiling.** And it means the near-dup
primitive is not useless here after all — it is simply *not a source-diversity measure*. Its
real job would be recovering the provenance that ingest destroyed, **within** a publisher.
That is a different tool for a different purpose, and it must not be reported as an
independence metric.

### What survives of the classical literature

The copy-detection work (Dong, Berti-Equille & Srivastava, `arXiv 1503.00310`; *Scaling up
Copy Detection*, `arXiv 1503.00309`) contains one genuinely elegant idea, still worth
keeping:

> **Agreement on true values is uninformative — everyone gets the easy things right.
> Agreement on *errors and unusual values* is strong evidence of copying.**

But note its precondition, which mind's data denies: it needs **many sources** to compare.
`1503.00309`'s own framing — *"copying is prevalent for Deep-Web data"* — describes a
many-source web corpus, not a four-publisher feed. ⇒ **The literature does not apply to mind
as it stands**, and that is a fact about mind's ingest, not about the literature.

⇒ **This also withdraws my proposed shared-package candidate.** Near-duplicate clustering is
still a real primitive that memo wants for dedupe, but it is *not* the shared consensus core
I claimed one paragraph earlier — mind's use for it would be intra-publisher provenance
recovery, which is a different function with a different contract. Two projects wanting
"some kind of similarity clustering" is not a shared component; it is a coincidence of
vocabulary. That is exactly the trap audit §7 warned about, and I walked into it.

## 4. Where memo and mind genuinely diverge — track record needs an oracle

✅ **Confirmed by mind's reply, with numbers.** They persist a **per-creator hit rate** scored
against resolved calls — corpus median **0.23**, best creator **0.49**, worst **0.21** — and
call it *"the most useful field in the brief."* Their reason is verbatim the §1 criterion:
no amount of reading today's text tells you this creator has been wrong before.

⇒ **The store-vs-derive line is now empirically anchored at both ends by the same project:**
mind persists track record and finds it their best signal; mind persists sentiment and
regrets it. Same system, same data, opposite outcomes, exactly where §1 predicts.

Source reliability — "has this source been right before" — is the most attractive signal in
the truth-discovery literature and the one with the sharpest precondition: **you must
eventually find out who was right.** Classical truth discovery escapes this by estimating
reliability and truth jointly from mutual agreement, which requires many sources asserting
values for the *same object* (the data-shape blocker from scout pass §7).

That precondition splits the two projects cleanly:

- **`mind` has an oracle, and it is free.** A claim about an earnings reaction or a price
  move is adjudicated by the market within days. Source reliability is not only buildable
  there, it is buildable *automatically*, with no human labeling — score each source's past
  calls against what actually happened. ⭐ **mind can measure its retrieval in a way memo
  structurally cannot**, because its domain hands it ground truth for free. That is a real
  asset and I do not think it has been recognised as one.
- **`memo` has no oracle.** "server4's IP is X" is adjudicated by a human noticing, or never.
  We can record *provenance* (who asserted it, when, on which host) but not *accuracy*, and
  a reliability prior we cannot validate is a number that will drift into fiction while
  looking authoritative.

⇒ **Do not build a shared source-reliability component.** Share the independence primitive
(§3), which needs no ground truth; keep reliability estimation inside mind, where it can be
checked. This is a sharper version of the audit's §7 rule and it cuts *against* the
convenient answer.

⇒ For memo, the honest substitute for reliability is **provenance**, including the
transmission-chain idea from the scout pass (`arXiv 2607.24117`): record *who told us*, not
just where the file was. That is checkable, needs no oracle, and addresses a failure we
demonstrably have — agents relaying facts from other agents, with the 08-05 pin-protocol
cascade as the worked example.

## 4b. Contradiction — the buildable half

Added after Ben asked for "consensus / contradiction weighting" together. **They are not two
names for one feature, and separating them is most of the answer:**

> **Consensus needs *many independent sources*. Contradiction needs only *two facts about the
> same thing*.** Both projects have both. They differ in what is cheap to reach first.

⛔ **This section previously said "neither project has the first" and called consensus
blocked. Ben corrected both halves** — *"memo has ingest from MANY agents, all different
sources"*, and mind has publishers and YouTube creators well beyond the one table I sampled.
See §3's scope correction and §4c.

Contradiction itself splits into two cases that want different machinery:

**(a) Synchronic — sources disagree right now.** Handled in the literature by surfacing the
disagreement rather than silently resolving it (CARE-RAG, `arXiv 2507.01281`, conflict-driven
summarization).

**(b) Diachronic — new information supersedes old.** This is memo's real case and it needs no
consensus machinery at all. One agent stores *"the IP is X"*; another later stores *"the IP is
Y."* Nothing needs to vote — the later assertion wins, **if we know the two are about the same
thing.** ⭐ **That "if" is the entire problem, and it is claim identity, not consensus.** It is
also the same gate that blocked truth discovery in the scout pass: we store narrative notes,
not claims, so nothing knows those two memos are about one fact.

⛔ **The standing constraint, restated because this is exactly where it gets violated: do not
implement (b) as recency weighting.** A dated record stays true *as a record* and becomes
wrong *as an answer*; ageing it down breaks "when did we decide X" in order to fix "what is
the rule now." The correct shape is additive — `valid_from` / `valid_to` / `supersedes`,
invalidate-never-delete — and `getzep/graphiti` (29.6k★, Apache-2.0) already implements it.

⇒ **(b) needs no oracle and no source diversity, and addresses a failure memo demonstrably
has.** Its first milestone is claim identity, not weighting.

## 4c. What memo actually records about its sources — measured

Ben: *"memo has ingest from MANY agents, all different sources."* Correct, and it exposed a
claim of mine that was false. I told him memo tags every document with the seat and host that
wrote it. **It does not.** Measured against the live corpus, 8,914 documents:

| field | docs | what it actually means |
|---|--:|---|
| **no metadata at all** | **4,259** | ~48% of the corpus |
| `source_host` | 4,441 | host of a *migrated file* — not an author |
| `memdir_store` | 816 | 31 distinct; closest thing to per-seat identity |
| `migrated_by` | 816 | 3 values, and it names the migration tool |
| `author` / `agent` | 5 / 1 | ad hoc, wherever a writer happened to type it |

⇒ ⭐ **memo is genuinely multi-source and the ingest path discards which source.** The
corpus is not the limitation; the write path is. Nothing records that these three memos about
one IP came from three different seats rather than one seat writing three times — and that
distinction is the whole of corroboration.

⭐ **The parallel is the day's real finding.** `mind` reported independently that *provenance
collapses at ingest* on their side — PR-wire reprints arriving under a publisher's own label.
**Two unrelated systems, both genuinely multi-source, both throwing source identity away at
write time, and both therefore unable to say whether agreement means anything.** Neither
problem is a retrieval problem and neither is fixed by a better ranker.

📌 **Scope: v2, not v1** — Ben, 2026-08-06: *"no defer for v2."* v1 on `:8000` is live fleet
infrastructure written to by ~50 seats and stays untouched. v2 re-ingests everything anyway,
so identity gets captured on the way in rather than bolted onto a live path. ⚠️ Backfilling
the historical half is mostly unanswerable and should not be attempted.

## 4d. The scoring algorithms — solved, and Apache-2.0

Ben: *"i am wondering if you can find algorithmic ways of scoring based on project scouting."*
Scouted. **The scoring is a solved problem with a maintained library. The algorithm is not the
work.**

**`Toloka/crowd-kit`** — 252★, **Apache-2.0**, last push 2025-12. ⚠️ GitHub's API reports
`NOASSERTION`; that is a parse failure, and reading `LICENSE` shows plain Apache 2.0. (Same
class of error as the 08-05 "all five repos unlicensed" mistake — read the file.)

It ships, under `crowdkit/aggregation/classification/`: **Dawid–Skene · GLAD · MACE · M-MSR ·
KOS · Wawa · ZeroBasedSkill · MajorityVote · GoldMajorityVote**, plus text and embedding
aggregators (RASA, HRRASA, ROVER) for free-text answers.

⭐ **Every one of them does exactly the thing Ben asked for: given who-said-what, jointly
estimate the true value *and* each source's reliability.** Consensus and source-consistency
are not two features — they are one computation, and it is `pip install`-able.

| algorithm | what it buys over the simpler ones |
|---|---|
| **Dawid–Skene** (1979) | the canonical baseline: per-source confusion matrix, EM between "what's true" and "who's reliable" |
| **GLAD** | separates **source ability from item difficulty** — a fact every seat got wrong means the fact is hard, not that every seat is bad |
| **MACE** | models a source that isn't *trying* separately from one that is *mistaken* |
| **Wawa / ZeroBasedSkill** | one-pass approximations, weight by agreement with the majority — where I would start |
| **GoldMajorityVote** | calibrates against items whose answer is already known; we could seed with infrastructure facts we are certain of |

**Adjacent, different jobs:**
- **Dempster–Shafer** (`reineking/pyds`, BSD-3, ⚠️ **archived 2021**) — combines evidence
  while carrying an explicit *unknown* mass. Interesting precisely because it distinguishes
  "no evidence" from "evidence for neutral" — the exact hazard in §1.
- **EigenTrust** (several small impls) — transitive reputation over a graph. The one that
  fits agent-to-agent relay, where trust should flow along who-told-whom.
- **RRF** (`Raudaschl/rag-fusion`, 948★, MIT) — rank fusion; every implementation found has
  unvalidated weights, groton's included.
- **Classical truth discovery** (`joesingo/truthdiscovery`, 7★, GPL-3.0, dead since 2023-03) —
  Sums, Average.Log, Investment, PooledInvestment, TruthFinder. Algorithms don't rot, but
  crowd-kit is the maintained path.

⇒ ⭐ **THE WHOLE FAMILY TAKES ONE INPUT SHAPE: a table of `(source, item, value)`.** So the
build reduces to *producing that table* — which is §4c (who wrote it) plus claim identity
(what is it about). **Neither is an algorithm problem, and no library call fixes either.**

⚠️ **Two honest caveats, both structural.**

1. These assume multiple sources labelling **the same item**. That is the input contract, not
   a blocker — but it is precisely what our data does not currently emit.
2. They estimate reliability **from mutual agreement, with no ground truth.** So they will
   conclude that a confident majority is right. ⛔ Where a wrong belief propagated *between*
   agents — the 08-05 pin-protocol cascade — **agreement is exactly what the error looks
   like**, and these algorithms would ratify it. mind's oracle (resolved calls vs outcomes) is
   the only check in the family against that failure.

## 5. What I would actually do, in order

**Rewritten after Ben's redirect** — *"i'm not asking you to assess its universe, i'm asking
to build what i want."* The earlier version ranked things by whether they were feasible. This
one is a build order. Each step is testable on its own.

1. ⭐ **Capture source identity at write time — v2** (§4c). Nothing downstream works without
   it, and 48% of the corpus currently carries no metadata at all. Ben's call: **v2, not a v1
   patch** (`:8000` stays untouched). v2 re-ingests everything, so this is capture-on-the-way-in
   rather than a migration. ⚠️ Do not attempt to backfill the historical half.
2. ⭐ **Claim identity** — knowing two records are about the same fact. This is the real
   engineering in the whole programme and the input contract for every algorithm in §4d.
   Similar text is not the same claim, and the same claim can be worded nothing alike.
   *Test it early and on its own:* if it does not work, that is the finding, and nothing above
   it should be built.
3. **Emit the `(source, item, value)` table** and run an off-the-shelf aggregator (§4d). Start
   with **Wawa** — one pass, no EM, no tuning — and only reach for Dawid–Skene or GLAD if the
   simple version shows signal. This step is a library call once 1 and 2 exist.
4. **Annotate, don't weight** (§2). Surface "3 seats, independently, one disagrees" in the
   context and leave the ranker alone until something measures that a rank change is needed.
5. **Supersession** (§4b(b)) — additive `valid_from`/`valid_to`/`supersedes`,
   invalidate-never-delete. Falls out of 2 almost for free, since both need claim identity.
   Read `getzep/graphiti` first.
6. **Source track record** — which seats' records later get superseded. Falls out of 5 with no
   new instrumentation. ⚠️ Read §4d caveat 2 before trusting it.
7. **mind's consumer-side sentinel fix** — `unscored` distinguishable from `neutral`, and
   presence never used as a filter (presence encodes vendor). mind owns it; listed because it
   is live and another seat nearly traded on it.

**Not now:** decay/recency weighting (scout §1; mind reached the same conclusion
independently) · a from-scratch truth-discovery implementation (crowd-kit is Apache-2.0 and
maintained) · any shared component that owns a data model · touching v1's write path.

## 6. The measurement question, which is the same one as yesterday

Ben asked yesterday how we'd tell whether retrieval got better. Every step above is subject
to it, and step 2 is the one where it bites: *"the model saw the annotation"* is not
*"the answer improved."*

§3 is the cautionary case and it cost nothing precisely *because* it was measured before it
was built: a plausible feature, a cheap check, and an answer that reversed the design in
under an hour. The check that mattered was not "does this work" but **"is the precondition
even present"** — and that question is almost never the one a feature proposal asks about
itself.

⇒ Same conclusion as the audit, from the other end: **the project that can measure should go
first.** mind has a free oracle (resolved calls against market outcomes) and has already used
it to rank its own stored fields honestly — track record earns its place, sentiment does not.
memo has no oracle and should adopt consensus machinery, if ever, **on mind's evidence rather
than in parallel on hope.**

## 7. Open questions

1. **Claim identity is the gate** (§5 step 2) and I have no design for it yet. Facts in memo
   are narrative prose, not triples. Whether we extract claims at write time, at read time, or
   only for a narrow high-value class (IPs, ports, paths, versions) is the first real design
   decision and I would want to make it deliberately rather than by drift.
2. **How much of the value needs the full aggregator at all?** §5 step 4 (annotate) may
   capture most of it without any of §4d, since the model does the weighing. Worth knowing
   before building 3.
3. **§4d caveat 2 has no answer yet.** Agreement-based reliability ratifies a confident
   majority, and agent-to-agent propagation manufactures exactly that. mind's oracle is the
   only check in the family; memo has none. If we build reliability scoring on memo, we should
   know in advance what would tell us it had gone wrong.

---

## Provenance of this document

Written across ~90 minutes on 2026-08-06 with Ben correcting live and `mind` supplying
measurements. **Six positions in it were wrong and were changed on evidence**, which is worth
recording because the document reads as though it were reasoned out in one pass and it was
not:

1. "Store what the reader cannot reconstruct" — conflated *what to keep* with *what to
   annotate*. (Ben)
2. Then overcorrected to "store sentiment at scan time." (Ben again — I inverted rather than
   located the error.)
3. "Backfill mind's sentiment column" — impossible; it is vendor-supplied. (`mind`)
4. "Retire mind's sentiment column" — unsupported; it is free vendor data, and not a scalar.
   (`mind`)
5. Near-duplicate clustering as the shared independence primitive — the detector returns clean
   on a degenerate corpus and clean reads as independence. (`mind`)
6. "Consensus is blocked; memo is single-source" — memo has ~50 agent seats writing to it, and
   mind's universe is far wider than the table I sampled. (Ben)

⭐ **The one error underneath most of them: I kept measuring what was in front of me and
reporting it as a property of the world** — one table read as mind's universe, one project's
coverage bug read as a design principle, a duplication count read as provenance. `mind` made
the same error on the same afternoon and named it in the same words. **It appears to be the
default failure of a system that can measure things quickly.**
