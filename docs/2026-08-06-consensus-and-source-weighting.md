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

- **`mind` has an oracle — real, but far thinner than this document first claimed.** A claim
  about a price move is adjudicated by the market, with no human labeling. **But mind
  measured the funnel and corrected their own earlier framing**, which I had already written
  up as load-bearing:

  | verdict, across 52,902 claim verifications | |
  |---|--:|
  | **untestable** | **45,869 (86.7%)** |
  | refuted | 4,809 |
  | verified | 1,698 |
  | mixed | 526 |

  ⇒ **Only 12.3% of extracted claims ever resolve against a market outcome, and among those
  that do, refuted outruns verified nearly 3:1.** The oracle therefore measures *a
  biased-toward-resolvable slice of creator calls* — not retrieval quality in general.

  ⛔ **I wrote "mind can measure its retrieval in a way memo structurally cannot" and
  "the only project in this family that can actually measure whether its retrieval is good."
  Both overstate it and are withdrawn.** mind's words: *"I gave you the clean version of a
  messy asset."* ⚠️ The asset is still real and still the best in the family — it is just an
  oracle over a subset with known selection bias, and any number derived from it inherits
  that bias.
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

## 4e. mind MVP — what Ben actually asked to be spec'd

Ben, 2026-08-06 10:23, narrowing scope: *"I want to speak solely about the mind project…
spec a basic algorithm and data store… a knowledge base then used by day traders to assess
trade opportunities… we need to be able to know when many different sources are talking about
the same thing, when sources disagree or provide counterfactuals to a thesis."*

**Scouted first, and the scouting result is decisive: there is nothing to adopt.** Every
"financial news RAG" repo found is a 0–3★ demo. Topic-detection-and-tracking has no live
implementations. The only reusable pieces anywhere near this are Kleinberg burst detection
(`nmarinsek/burst_detection`, 77★) and crowd-kit (§4d). ⇒ **This gets built.**

### The proposal, and what mind's measurements did to it

I proposed storing claims as `(source, ticker, direction, horizon, thesis, ts)` with chunks
kept underneath as evidence, and two vector indexes rather than one.

⛔ **mind then measured, and the centre of gravity moved.** Corrections, all theirs:

1. ⭐ **The claim layer already exists at scale.** `claims`: **61,333 rows off 6,309 videos**,
   ticker present on 54,430 (88.8%), plus `claim_verifications` (52,902), `creator_credibility`
   (40 creators), `recommendations`/`recommendation_outcomes`. The live schema is very close
   to what I proposed. **This is not a rework — it is built and running.**
2. ⛔ **"Test extraction alone before building on it" was aimed at the wrong stage.** 61k
   claims is extraction already demonstrated at scale. ⚠️ And I cannot cost it: mind's
   `ingest_cost_log` has the right columns but **0 `llm_calls` / 0 `llm_tokens`** — it is not
   wired to the extraction path. Their instruction, which is correct: *"Don't replace your
   guess with a number I don't have."*
3. ⭐⭐ **THE HARD PART IS RESOLUTION, NOT EXTRACTION.** 86.7% untestable (§4). The
   `resolutions` design is right and **will run at roughly 1-in-8 yield** — spec it knowing
   that. ⇒ **Testability, not extraction, is the lever**: a prediction with no window and no
   threshold cannot be scored, so the value is in claim *formulation*.
   ⚠️ **And that 86.7% is what survives an existing filter, not what a new one would catch** —
   `testable = false` already rejects 8,424 claims (13.7%) up front, so verification runs on
   the pre-filtered set. ⭐ **The unexploited lever is `test_window`: empty on 30,534 claims
   (49.8%) that pass as testable anyway.** Requiring a stated window at extraction would gate
   ~half the corpus, against 13.7% for the gate already running. **A claim that cannot state
   what would falsify it is not a weak claim — it is not a claim.**
4. ✅ **Point the thesis index at CREATORS, not publishers.** `claims` is keyed on `video_id`
   — the layer is YouTube-only, and there are no news claims. With the news side dominated by
   one publisher and **40 creators carrying credibility scores**, "many sources on the same
   story" has to come from the creator axis. ⛔ **Applied to news it would cluster one vendor
   talking to itself.** The chunk-index/thesis-index split survives; the axis changes.

### ⛔ SCOPE CUT — resolution is OUT of the MVP

**Ben, 2026-08-06 10:31:** *"I think we care as much about market resolution — I just want to
know what the market is saying is happening and the traders will do things like resolution.
I don't think we need to get bogged down in that."*

⇒ **Drop `outcomes`, `resolutions`, and everything downstream.** ⭐ **This also drops the
86.7%-untestable funnel — the single hardest problem in the design — because it was never the
MVP's problem.** mind and I spent the preceding exchange characterising a constraint on work
Ben does not want done. The corrected numbers stay recorded above because they are true and
mind may want them, but **they no longer bear on this spec.**

⇒ **The MVP is a READ SURFACE over what is being said right now**, not a scoring system:
*for this ticker, who is saying what, how many distinct sources, is this unusual, and where do
they disagree.* Judgement stays with the trader. That is the same annotate-don't-weight
principle from §2, arrived at independently by the person who has to use the thing.

⚠️ **Source credibility becomes optional decoration rather than core.** mind's per-creator hit
rate can be surfaced beside a claim, but nothing in the MVP depends on it — which is just as
well, given §4's selection bias.

### What stands

⭐ **Two vector spaces, not one.** Chunk embeddings are dominated by article style and
boilerplate; thesis embeddings are dominated by the assertion. **Clustering chunks finds
documents that read alike; clustering theses finds people saying the same thing** — and only
the second answers Ben's question. This is the interesting build, now aimed at creators.

⛔ **Ticker is a structured filter, NEVER a vector match.** Dense embeddings are worst exactly
on literals — measured here (R-25, hybrid work) and confirmed by mind, whose own entity path
scored **`NYT` at z=4.1 above a real +17% earnings move.** Every query is `ticker + time
window` as hard filters *first*, then ANN over the survivors. That also keeps the working
index small, which is what makes it fast enough to trade on.

**What a consumer gets back for a ticker, in one call:** claim count *and* distinct-source
count (different numbers, and the gap is the story) · burst score against that ticker's own
trailing baseline — "unusual", not "high" · the direction split with **the minority surfaced,
not averaged away** · top evidence chunks · each source's hit rate beside its claim.
⛔ **No blended confidence number** — that hides precisely the counterfactual Ben asked to be
able to see.

**Deliberately out of MVP:** reliability modelling via crowd-kit (plain hit rate suffices
until it demonstrably does not) · cross-source copy detection · anything touching ranker
weights.

### ⛔ DON'T PRESUME THE SIGNAL — the governing constraint

**Ben, 2026-08-06 10:43:** *"I don't want to necessarily presume what the signal is. I want to
give a trader a searchable and analyzable knowledge base that structures data in ways that
they will find useful so that they can attempt to find the signal."*

⛔ **This corrects the section below, which was written before he said it.** I called thesis
spread *"the thing a trader cannot get anywhere else"* and dismissed ticker volume as *"cheap,
and largely visible already."* **Neither was mine to decide.** The section's *content* stands —
thesis spread and ticker volume genuinely are different objects — but its **ranking of them
does not.**

⇒ ⭐ **STRUCTURE IS OURS; CONCLUSIONS ARE THEIRS.**

Claim extraction, thesis clustering, creator identity and timestamps are not opinions about
the signal — they are what makes any question askable. Raw transcripts cannot be grouped by
anything. **The structures create the axes; the trader picks along them.**

**What that forbids:**

| ⛔ don't | ✅ do |
|---|---|
| a fixed API of computed signals | a queryable store where arbitrary group-bys are possible |
| baked thresholds (`burst > 2σ`) | the raw series — **a threshold is a hypothesis wearing a number's clothes** |
| dropping an axis as uninteresting | keep ticker volume, horizon presence, claim type, source kind, time-of-day, creator |
| pre-aggregating | **full grain retained** — aggregates are cheap to recompute and impossible to un-collapse |
| a blended confidence score | compositional counts, reported separately and never merged |

⭐ **This is the same principle as §2 (annotate, don't weight), one level up — and Ben has now
applied it to me twice: first to sentiment scalars, then to trend metrics.** In both cases I
moved to precompute a conclusion the consumer was better placed to draw. ⚠️ **The tell is the
same both times: I found the interesting answer and started building toward it, rather than
building the thing that lets someone else find an answer I had not thought of.**

⇒ **Architectural consequence:** this wants a well-modelled analytical store with search on
top — structured filters, full-text, and vector similarity over both chunks and theses — much
closer to a warehouse table a trader can query than an application with endpoints.

### Thesis and trend are two objects, not one

Ben, 10:41, on what the read surface should carry: *"thesis and trends things like that."*

**Thesis** = the assertion: direction, horizon, the ticker(s) it touches. This is mind's
`claims` and it exists.

**Trend splits in two, and only one of them is worth building:**

| | what it says | value |
|---|---|---|
| **ticker volume** | "NVDA is loud today" | burst detection, cheap, largely visible already |
| ⭐ **thesis spread** | "'hyperscaler capex is peaking' went from 3 creators to 19 in four days" | **the thing a trader cannot get anywhere else** |

⇒ **Volume tells you something is loud; spread tells you which *argument* is winning.** Thesis
spread is the entire reason the thesis index exists, and it is only reachable once claims are
clustered by assertion rather than by document similarity.

⛔ **Schema consequence — and it corrects my own earlier sketch, which keyed clusters on
ticker.** Cluster **theses first, then attach tickers.** A macro thesis ("rates stay higher
through Q3", "capex is peaking") spans many tickers; keying clusters on ticker shreds it into
fragments that each look too small to notice. **Ticker remains a hard filter for retrieval; it
must not be the partition key for clustering.**

```
thesis_clusters   id, centroid, first_seen, label
cluster_members   cluster_id, claim_id, creator_id, ts
cluster_tickers   cluster_id, ticker, weight        ← many-to-many, derived
```

**Trend is a time series over `cluster_members`, counted in DISTINCT CREATORS, not claims.**
One creator posting the same thesis nine times is not a trend. ⭐ This is the distinct-source
point again, and it bites hardest here: **trend is precisely where repetition masquerades as
momentum.**

**Baselines are per-cluster, not global** — 3→19 creators is a move; a perennial thesis going
40→45 is noise. Same logic as measuring burst against a ticker's own history.

⭐ **The counter-thesis falls out for free**, which answers Ben's "counterfactuals to a
thesis": the opposing argument is just the nearest cluster with opposite direction over the
same tickers. Nothing extra to build once theses are clustered — and it is surfaced as a
neighbour, not folded into a score.

⚠️ **The part I would prototype before believing any of this: clustering theses is harder than
clustering documents.** *"Capex is peaking"* and *"hyperscalers are pulling back on datacenter
spend"* are one thesis in no shared vocabulary. Judge that by eye on real data before building
a trend layer on top of it.

### Pluggability — "can different vectorizations, scorings and searches be deployed"

Ben, 10:44. Yes, and the split that makes it tractable:

⭐ **EMBEDDINGS ARE STORED. SCORES ARE COMPUTED.** Be *generous* with scoring variants and
*deliberate* with embedding variants — the cost profiles are nothing alike.

**Scoring and search cost nothing to keep plural.** They are query-time functions over the
same rows: `vector_only`, `bm25_only`, `rrf`, `hybrid_weighted`, whatever gets tried. Nothing
is persisted, so a bad ranker costs an afternoon rather than a reindex. This is also the only
honest way to hold "don't presume the signal" — an unvalidated weight (groton's RRF, our R-25)
should be *one selectable option*, never the baked default.

**Vectorization is the expensive side, and the asymmetry is large enough to decide the
design** (float32, computed 2026-08-06):

| layer | rows | dim 1536 | dim 3072 |
|---|--:|--:|--:|
| **claims / theses** | 61,333 | **0.38 GB** | 0.75 GB |
| article chunks (1/article) | ~2.49M | 15.3 GB | 30.6 GB |
| article chunks (3/article) | ~7.5M | 45.9 GB | 91.8 GB |

⇒ ⭐ **40–240× cheaper to carry N models on the thesis layer than on the chunk layer — and the
thesis layer is exactly where the interesting clustering happens.** So: **several models on
claims/theses, one model on article chunks, chosen deliberately.** (int8 quantization divides
each figure by 4 if the chunk layer ever needs a second model.)

```
embeddings   chunk_id|claim_id, model_name, vec     ← model_name is part of the KEY
```
**Model name is part of the key, not a global setting.** Every query names the model it wants;
nothing is implicitly *the* embedding. That single choice is what makes side-by-side possible
instead of a migration each time.

⭐ **Plurality is worthless without a compare view** — same query, two or three
configurations, results side by side. Otherwise five deployed methods are five times the
surface area and no more knowledge: options with no way to prefer one. ⚠️ **Deliberately NOT
an eval harness** — no ground truth, nothing resolved (Ben put resolution out of scope). The
trader is the judge; the tool's job is making the comparison cheap. Given "don't presume the
signal," by-eye comparison is the honest instrument rather than a weaker substitute for one.

⛔ **Footgun, named because it has bitten this fleet: encoding asymmetry fails SILENTLY.**
Some models want an instruction prefix on the query and bare text on the document (qwen3);
symmetric models want neither. Mixing them up does not error — it returns plausible,
slightly-wrong neighbours indefinitely. ⇒ **Each registered model needs its own encode-query
and encode-document path, plus a fixed sanity check that a document retrieves itself.**

### Lightweight LLM reasoning at ingest — and it de-risks the load-bearing piece

Ben, 10:52: *"we can also likely incorporate very lightweight LLM reasoning during ingestion —
we recently deployed an open source LLM… they also handle the embedding model."* (`embeddings`
seat owns both; queried 2026-08-06, reply pending.)

⭐ **This is the fix for the risk flagged as load-bearing.** Thesis clustering fails because
*"capex is peaking"* and *"hyperscalers are pulling back on datacenter spend"* are one thesis
with **no shared vocabulary** — embeddings bridge that unreliably. **A light LLM pass that
rewrites each claim into a canonical assertion makes them cluster trivially**, and it is a
short per-claim call, well within a local model.

⇒ **It converts "prototype this before building on it" from an open risk into a tractable
prompt-engineering problem.**

**Ingest passes worth a local model, in value order:**

1. ⭐ **Thesis normalization** — canonical rewrite. Decides whether the thesis axis exists.
2. **Horizon extraction** — "by Friday" / "this quarter" / "eventually" → a structured window,
   **or explicitly none**. Currently absent on 49.8% and blocking nothing.
3. **Ticker / entity extraction** — mind's entity path scored **`NYT` at z=4.1 above a real
   +17% earnings move**. Distinguishing a company from a publication fixes a live wrong answer.
4. **Claim segmentation** on transcripts — one video makes many assertions and the boundaries
   determine everything downstream.

⛔ **What NOT to use it for at ingest: anything deciding how *important* something is.**
Normalizing an assertion is structure; scoring it is presuming the signal. Same line as above.

⚠️ **The real risk: canonical rewriting is lossy, and the normalizer becomes a dependency.**
Change the prompt or the model and every cluster shifts underneath — old and new claims stop
clustering with each other, silently. ⇒ **Always keep the raw claim text. Treat the canonical
form as derived and regenerable, stamped with the model + prompt version that produced it.**
Then a normalizer change is a reindex rather than an invisible drift. (This is the same
discipline as `model_name` in the embeddings key: **whatever produced a derived value belongs
in the row beside it.**)

✅ **And it is cheap in the way that currently matters: local inference does not consume the
API budget**, so this is the one part of the design that can be prototyped *during* rationing
rather than after it.

### The GPU constraint I got wrong, and the test that settles the thesis axis

`embeddings` answered with measurements (2026-08-06, labelled MEASURED / ASSEMBLED / UNKNOWN).

**1. Local LLM throughput — good news.** MEASURED on gpu4: `qwen3:30b-a3b` ~70 tok/s,
`llama3.2:3b` 97 tok/s. ⇒ **~12–16h for the 60k claim backfill, minutes/day steady state.
Overnight, not days.**

⚠️ **A warm-up penalty distinct from model loading**: first request after idle decodes at
**~21 tok/s against ~70 steady**, model already resident. ⇒ **Amortises to nothing in a batch;
DOMINATES for sporadic per-claim calls as articles arrive.** ⭐ **Design consequence: ingest
must micro-batch, not fire one call per document** — mind's news feed is continuous, so the
naive shape is the pathological one. (`embeddings` nearly reported the approach collapsed off
a single 3.75 tok/s reading while `cluster` measured 70 the same minute. **n=1 cannot separate
warm-up from steady state — they are different quantities in the same units.**)

**2. ⛔ I NAMED THE WRONG CONSTRAINT.** Above, I argued 3–4 models on the thesis layer was
affordable because the vectors are small (0.38 GB each). **Storage was never binding.**
MEASURED: **`migStrategy: none`, no time-slicing, no MPS ⇒ GPU allocation is WHOLE-CARD
EXCLUSIVE.** Carrying 3–4 models costs **3–4 cards**, not 1.5 GB. Fleet has 8; one serves
embeddings, one ollama, two ASR. **Exactly one embedding model is served today
(`qwen3-embedding-4b` @2560), and expansion is tabled by Ben until rationing lifts.**

⇒ ⭐ **`embeddings`' formulation, which is the transferable part: *"VRAM is free" and "capacity
is available" are different claims, and `nvidia-smi` only answers the first — five cards idling
at ~1 MiB are still unschedulable.*** My storage arithmetic was correct and measured the wrong
resource.

⇒ **The design survives; plurality is DEFERRED, not free.** Keeping `model_name` in the
embeddings key still costs nothing today and is what makes a second model a comparison rather
than a migration — but **run with one and say so.**

**3. ⛔ THE ASYMMETRY CLAIM IS UNVERIFIED — including in my own trap list.** I carried *"qwen3
wants an instruction prefix on queries, bare text on documents; reversing it fails silently"*
as established. `embeddings` grepped their files: it exists only as **prose**, in two places,
and they had been propagating it unmeasured too. They then tested it — **all four conventions
retrieved correctly** — and correctly refused to call that a refutation, because their
distractors were a finance passage, a k8s passage **and a sourdough passage**.

⭐⭐ **Their statement of the flaw is the sharpest epistemics of the day:** *"I asked myself
'could this check fail?' and answered yes. I did not ask 'is the task hard enough that a real
defect would show?' Those are different questions and only the second one mattered."*

⇒ **That is the third instrument this afternoon that could not have detected what it was
pointed at** — with the near-duplicate check that returns clean on a corpus with nothing to
duplicate, and `claims.testable` reading as a judgement while defaulting true. ⭐ **A test that
cannot fail in the relevant way reports success identically to one that passed.**

**4. Short/abstract/low-overlap performance: UNKNOWN, and they declined to guess.** ✅ **So it
is being measured.** Built `bench/thesis_pairs.json`: **30 paraphrase pairs with deliberately
disjoint vocabulary + 30 targeted hard negatives**, seven of which are **single-word polarity
flips** — *losing share* / *taking share*, *insiders dumping* / *insiders buying*, *that
**weakness** is just seasonality* / *that **strength** is…*, *missed their targets* / *beat
their targets*.

⇒ ⭐ **One corpus answers BOTH open questions** — whether the thesis axis exists at all, and
which encode convention each model needs. `embeddings` will run all four conventions over it
and populate the per-model field **by measurement rather than from documentation**.

⇒ ⛔ **The discriminating subset must be reported SEPARATELY, not averaged in: if minimal-edit
reversals do not separate, embedding similarity is measuring TOPIC, not CLAIM** — the
difference between *"these people are discussing the same subject"* and *"these people are
making the same argument."* **The thesis store needs the second.**

⚠️ **What a pass would and would not mean.** The pairs are hand-written, not drawn from mind's
corpus; real transcript claims are longer, hedged and messier. **A pass is necessary, not
sufficient** — it earns permission to test 50 real claim pairs, not a conclusion. **A failure
is close to decisive**, since a model that cannot do the clean version will not do the messy
one.

### ⛔ RESULT: the thesis axis FAILED the test — and what that licenses instead

`embeddings` ran `bench/thesis_pairs.json` against `qwen3-embedding-4b` @2560, to spec: all
four conventions, both directions, hard subset held out (2026-08-06 11:00).

| convention / direction | rec@1 | rec@5 | MRR | decoy outranked truth |
|---|--:|--:|--:|--:|
| bare / bare · a→b | **0.233** ⚠️ | 0.667 | 0.445 | 23/30 |
| bare / bare · b→a | 0.467 | 0.700 | 0.585 | 14/30 |
| PREFIX Q / bare D · a→b | 0.200 | 0.667 | 0.443 | 24 |
| PREFIX / PREFIX · a→b | **0.067** | 0.733 | 0.378 | 28 |

⛔ **PRIMARY: recall@1 ≈ 0.23 — the `< 0.5` branch of my own interpretation table:
"thesis clustering by embedding does not work on this text."** The best cell anywhere is
0.467, still under. ⚠️ **Third decimals below are not real — see the precision correction.**

**The hard subset — 6 of 7 failed:**

| true pair | beaten by |
|---|---|
| losing share to a cheaper competitor | **taking** share from a cheaper competitor |
| that **weakness** is just seasonality | that **strength** is just seasonality |
| **strong** dollar **hurting** results | **weak** dollar **flattering** results |
| TAM **smaller** than the **bulls** claim | TAM **larger** than the **bears** claim |
| **missed** their targets three times | **beat** their targets three times |
| somebody's going to **buy** them | somebody's going to **sue** them |
| insiders **dumping** | *held* vs insiders **buying** ✅ |

⭐ **The one that held is the one whose TRUE partner also shares no vocabulary**
(*"executives are unloading shares"*) — so it is consistent with the failure, not a
counterexample. ⇒ **Where the paraphrase is lexically distant AND the decoy lexically near,
the decoy wins six times in seven.** `buy`/`sue` losing on one consonant is the starkest.

⇒ ⛔⛔ **CONFIRMED: EMBEDDING SIMILARITY MEASURES TOPIC, NOT CLAIM.** For a thesis store that
is the difference between *"these people are discussing the same subject"* and *"these people
are making the same argument"* — and the store needs the second.

**⚠️ The finding that would have bitten hardest in production:** `b→a` scores **2× `a→b`**
(≈0.45 vs ≈0.23). The `a` texts are terse (*"Capex is peaking."*); `b` texts are longer.
**Short probes retrieve worse — and claims arrive terse.** ⇒ **The harder direction is the one
ingest actually runs**, so a benchmark testing only the easy direction would have read twice
as good as reality.

**⚠️ On the encode convention — refuted here, and correctly NOT generalized.** `PREFIX Q /
bare D` (the convention both of us had been propagating) is **worse than bare/bare in both
directions**; `PREFIX/PREFIX` is worst at 0.067. ⛔ **But `embeddings` scoped it themselves:
this corpus is SYMMETRIC paraphrase matching, while the instruction prefix exists for
ASYMMETRIC short-query → long-passage retrieval.** ⇒ **Verified `bare/bare` for
thesis↔thesis; STILL UNVERIFIED for query→chunk**, which is the other half of the store. Do
not populate a blanket "no prefix". *(Second time in one afternoon they declined to generalize
a result past what it measured — the discipline that made both runs worth having.)*

### ⛔ PRECISION CORRECTION — the instrument has a resolution floor of ~one pair

`embeddings` re-ran the benchmark while dumping per-pair ranks and **one cell moved: `bare/bare
b→a` was `0.467` first run, `0.433` second.** Same corpus, same model, same code. They then
measured the endpoint:

```
same text, 4 separate calls, alone in batch:  3/4 bit-identical; 1/4 differed, max|Δ| 9.6e-4, cos 0.999914
same text batched with 20 others:             bit-identical   (batch composition is NOT the cause)
```

⇒ **`qwen3-embedding-4b` on vLLM is NOT bit-reproducible across repeat calls.** The deviation
is minute — **and on a 30-pair benchmark it is enough to flip one pair's rank, which is 3.3
points of recall@1.**

⇒ ⛔ **QUOTE `recall@1 ≈ 0.23` (`a→b`) and `≈ 0.45` (`b→a`). NOT `0.233` / `0.467`.** The table
above is retained as the raw run, but **its third decimal is not real.**

✅ **THE CONCLUSION IS UNAFFECTED, and that is a separate statement from the correction.** Every
cell across both runs lands in `0.07–0.47` against a decision boundary of `0.5`, and **the
6-of-7 polarity failures were identical in both runs.** Those are not close calls; no jitter
moves them. **The finding survives; only the precision doesn't.**

⭐ **Two consequences that outlive this run:**

1. ⛔ **ANY FUTURE COMPARISON ON THIS ENDPOINT NEEDS A MARGIN LARGER THAN ~3.3 POINTS, OR
   REPEAT RUNS, OR BOTH.** A 2-point gap between two models — or between canonicalized and raw
   forms, which is the very next test — **would be noise.**
2. ⭐ **They found it only because they happened to run it twice for an unrelated reason.**
   **A single run reports a clean-looking number with no hint that a second would differ.**
   ⇒ Same shape as the sourdough distractors and the near-duplicate check: **the instrument
   said nothing about its own reliability, and nothing in the output would have prompted the
   question.**

⚠️ **And this one is mine to own.** memo's own QA suite has a measured **~2.4pt noise floor**,
and I carry a standing rule — *never quote a sub-2.5pt delta.* **I applied that rule to the
instrument I already knew and did not think to ask for it on a new one.** A noise floor is not
a property of a benchmark I happen to have characterised; **it is a question to ask of every
instrument before quoting it.**

### ✅ What the failure licenses

⭐ **recall@5 is ≈0.67–0.87 while recall@1 is ≈0.23. The SIGNAL EXISTS; THE RANKING DOESN'T.**
The true partner is usually in the top 5 and usually not top 1.

⇒ ⭐⭐ **VECTOR SEARCH IS A RECALL STAGE, NOT A DECISION STAGE.** Retrieve top-5/10 by
embedding, then let the local LLM pass adjudicate which candidates are genuinely the same
thesis. **This is the canonicalization remedy arrived at from the other side, it was already
in the design, and it costs nothing extra architecturally** — it is now *mandatory* rather
than optional.

⇒ **Not doing the 50-real-claim confirmation run.** `embeddings` recommended against and I
agree: a failure this clean does not need confirming, and my own stated rule was that a
failure is close to decisive. **That budget goes to testing the CANONICALIZED forms**, where
the question is live and unanswered.

### ⭐ The cross-project consequence, which may be the bigger finding

**memo's supersession work has the same gate** — §5 step 2, knowing two records are about the
same fact. **If embedding similarity measures topic rather than claim, that cannot be done by
vector similarity in memo either.** Same result, different corpus.

⇒ **Claim identity moves from "the hard part" to "a part that requires an LLM in the loop by
construction."** That is worth knowing before memo v2 is specced, and it was bought by a test
built for a different project. ⚠️ Strictly, this is a *transfer* of a result measured on
market theses to a corpus of infrastructure notes — plausible and cheap to check later, not
established. **Recorded as a strong expectation, not a measurement.**

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
first.** mind has an oracle — ⚠️ over a 12.3% resolvable slice with known selection bias
(§4), not the clean instrument I first described — and has already used it to rank its own
stored fields honestly. memo has none and should adopt consensus machinery, if ever, **on
mind's evidence rather than in parallel on hope.**

⭐ **And the strongest recurring finding of the day is not about consensus at all.** Stated
carefully, because the first two attempts at stating it were both too narrow:

> ⭐ **FIELDS WHOSE ABSENCE OR DEFAULT IS NEVER CHECKED BY THE THING THAT DEPENDS ON THEM.**

Three independent instances, all failing silently and all in the reassuring direction:

| field | reads as | actually is |
|---|---|---|
| mind's `sentiment` = `NULL` | neutral | never scored — and presence encodes *vendor* |
| a clean near-duplicate check | sources are independent | nothing to syndicate from |
| mind's `claims.test_window` empty | the claim has a horizon and is falsifiable | **absent on 30,534 claims (49.8%) that are marked testable anyway — nothing consults it** |

⛔ **This table shipped with a wrong third row, and the wrong row is the finding.** I first
listed `claims.testable` as "a boolean defaulting to `true`, so the extractor never decides."
**mind checked it and it is false:** `testable = false` on **8,424 rows (13.7%)** — the
extractor does decide and does reject. That row came from reading the DDL default and
inferring behaviour, an inference mind handed me and I built on without counting.

⇒ ⭐ **A table about absences that are never checked contained an entry that was itself never
checked.** It was caught by one `GROUP BY`. **The instrument for this class is counting, not
reading the schema** — and by mind's own tally that is twice in one afternoon they inferred
producer behaviour from a shape and were wrong (the sentiment "cap" was the other).

⇒ **The real instance is one column over and is a bigger lever:** `test_window` is empty on
**49.8%** of claims that pass as testable. The falsification criterion is absent on half the
corpus and **blocks nothing**. Requiring a stated window at extraction time would gate roughly
half the corpus — against the 13.7% the existing gate already catches. ⚠️ **Note this also
corrects my advice to mind:** I proposed an up-front testability filter as though none
existed. One exists and fires; the mechanism is right but it has to beat a running gate, and
it should be aimed at `test_window`, not `testable`.

⇒ **Whatever gets built should treat "not assessed" as a first-class value wherever a
judgement can be absent** — and should verify that claim by counting, not by reading the
column definition.

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
