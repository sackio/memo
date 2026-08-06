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

⭐ **`mind` has run this experiment and supplies a sharper reason than mine — partial
coverage.** They persist a sentiment scalar and regret it: it covers **~7% of weekdays'
articles**, a flat ~140–160/day whether the day carries 900 or 2,100. So `NULL` means
*not scored* and **reads as neutral**. A consumer filtering `sentiment <> 'negative'` passes
93% of the tape as though it had been cleared. This is not hypothetical — mind warned the
`alpaca` seat off it the same morning, before they traded on it.

> **A derived judgement is absent when the text is absent. A stored scalar is absent while
> the column still exists — which reads as a value.** (`mind`, 2026-08-06)

That is the general form of the argument and it is better than "the model can do it itself."
Deriving fails loudly; a partially-populated column fails silently, in the safe-looking
direction.

⇒ **And the resolution is to drop the column, not to backfill it.** The scalar is *redundant
with the chunk it hangs off* — the article is stored, and an LLM reading it can draw the same
conclusion better and in light of the actual question. That redundancy is also why 7%
coverage survived so long unnoticed: **nothing depended on the column until a consumer did,**
and by then it read as authoritative. ⚠️ I argued the opposite (backfill to 100%) for one
message; that was wrong, and it is recorded here because "make the broken signal complete"
is the more natural instinct than "delete the signal you didn't need."

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

⇒ **The problem is not that corroboration is being inflated by copies. It is that
cross-source corroboration was never available to be counted.** There is effectively one
source. Nothing is being double-counted because there is nothing to double-count.

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

## 5. What I would actually do, in order

**Reordered after mind's measurement.** The first draft led with near-duplicate clustering;
§3 shows that would have measured the wrong thing and returned a reassuring number.

1. ⭐ **Report source concentration wherever agreement is claimed** — distinct sources, not
   distinct documents. Cheapest item here and it is a *precondition*, not an improvement:
   without it every downstream consensus figure is arithmetic on one opinion. On mind's
   corpus today this correctly reports "1 effective source" and stops the rest of the
   programme before it starts. **A consensus feature built on a 4-publisher feed would have
   looked like it worked.**
2. **Retire mind's sentiment scalar, or make its absence explicit** — not a consensus feature
   at all, but it is live, it is silent, and another seat nearly traded on it. The column is
   redundant with the stored chunk (§1), so removing it loses nothing; if it stays,
   `unscored` must be distinguishable from `neutral` at the consumer. mind owns this;
   flagged here because it is the sharpest instance of §1 in the fleet.
3. **Provenance / transmission chains for memo** (§4). Record who asserted a claim and from
   whom. Additive, no oracle, no ranking impact, and it addresses a failure memo
   demonstrably has.
4. **Intra-publisher provenance recovery** (§3, mind only, *if* they want it) — near-dup
   clustering aimed at the PR-wire reprints that ingest flattened into "Benzinga". ⚠️ Must
   never be reported as an independence metric; it measures what ingest destroyed.
5. **Source reliability — mind only**, and largely already built (per-creator hit rate). This
   is the one needing a harness, and mind is the one project with a free oracle.

**Not now, and possibly not ever:** stored sentiment scalars (§1) · decay/recency weighting
(scout §1, and mind independently reached the same conclusion by a different route) · a
from-scratch truth-discovery implementation (needs many sources; mind has four) · any shared
component that owns a data model · **a cross-source consensus feature for mind, until the
feed has more than one real source** — that is an ingest problem wearing a retrieval
problem's clothes.

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

1. ⭐ **For Ben, and it is now the main one:** consensus weighting was the half you wanted
   kept, and the measurement says **mind cannot do it — not because the technique is wrong
   but because the feed has four publishers and 93.6% of a day is one of them.** That makes
   it an **ingest question before it is a retrieval question**: is adding real source
   diversity to mind's feed something you want to pursue? Every consensus idea in this
   document is blocked on that and on nothing else.
2. **For Ben:** §3 withdrew my own shared-package candidate — the two projects want
   similarity clustering for genuinely different contracts. Combined with §4 arguing against
   a shared reliability component, **there is currently no shared consensus core worth
   packaging.** Worth knowing before more design effort goes into KB-in-a-box.
3. Does memo want provenance chains at all (§5 step 3)? It is the only item here that
   changes memo's schema, and the audit's P4 already has a claim on that territory.
4. **For mind, if cheap:** how far back does the 4-publisher figure hold? If the corpus was
   more diverse historically, the concentration is a regression with a cause rather than a
   standing property — and that changes whether §7.1 is a purchase or a repair.
