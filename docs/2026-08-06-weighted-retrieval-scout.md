# Weighted vector retrieval — sentiment, recency, consensus, source consistency

**Scout pass, 2026-08-06.** Ben's ask: *"projects or algorithms or papers that deal with
vector storage algorithms weighted by source sentiment recency and consensus among multiple
sources or consistency among a source — we also probably want to look for sentiment
derivation in vector form."*

Companion to `2026-08-05-knowledge-base-audit-and-proposal.md`. Everything here is a
**finding**, not a commitment; the rationing hold from 08-05 still stands.

---

## 0. Scope — and what I did NOT check

**Searched:** arXiv (field-scoped `abs:` queries, ~20 of them) · GitHub repo search (10
queries, ~10 of 30/min budget used) · HN live top-100 and Show-30 · HN Algolia historical
(8 queries, `points>30`).

**Not checked:** ACM/IEEE paywalled venues (KDD and VLDB are where the truth-discovery
literature actually lives — arXiv has the survey but not most of the primary papers) ·
non-English work · anything below 30 HN points · code quality of any repo beyond its README
and file listing. I read one benchmark file in full (YourMemory); every other performance
claim here is the project's own, unverified by me.

⚠️ **Two of my arXiv queries returned 0 hits** (`"sentiment-aware" AND "vector"`,
`"source consistency" AND "reliability estimation"`). Those zeros mean *my query ANDed too
many terms*, not that the field is empty — same trap as GitHub's ANDing. I re-ran broader
and found material both times. Do not read a 0 in this document as absence.

⚠️ **The arXiv MCP tool is unreliable for this.** Two unrelated queries — credibility-aware
RAG and temporal decay ranking — returned near-identical top-8 lists dominated by generic
"retrieval augmented generation" matches. A uniform result across different subjects is a
signal about the instrument. Everything below came from the arXiv API directly with
`abs:` field scoping.

---

## 1. The headline finding

**Someone already ran the experiment Ben asked about yesterday, on exactly the feature he is
asking about today, and got a null result.**

`sachitrafa/YourMemory` (262★, CC BY-NC-4.0) is the best-benchmarked project in the
"agent memory with biological decay" genre. Its `BENCHMARKS.md` contains a **Temporal Boost
Ablation** on LongMemEval-S (n=500), scoring `cosine(q,m) × ebbinghaus_strength` with and
without a +0.25 recency boost:

| Question type | Base | + temporal boost | Δ | n |
|---|:--:|:--:|:--:|:--:|
| knowledge-update | 96.2% | 96.2% | **0pp** | 78 |
| multi-session | 95.5% | 95.5% | **0pp** | 133 |
| **temporal-reasoning** | **84.2%** | **84.2%** | **0pp** | 133 |
| single-session-user | 72.9% | 72.9% | **0pp** | 70 |
| **OVERALL** | **89.4%** | **89.4%** | **0pp** | **500** |

The boost fired on **28/500 queries (6%)**. Their own reading: LongMemEval's temporal
questions are *event*-anchored ("when did X happen") not *window*-anchored ("what happened
last week"), and the boost only addresses the latter.

Two more things from the same file, both from the author, both against their own marketing:

- Their production scoring formula is `0.4×bm25 + 0.6×cosine` with a comment reading
  **"decay excluded from ranking."** The Ebbinghaus curve — the project's headline feature —
  is used only for *pruning* and graph scoring, not for ranking.
- The decay-pruning benefit measured **4.1% token reduction** on 15 synthetic memories.
- Where they *did* see a temporal gain (LoCoMo, 66% vs 54%), they attribute it to **BM25
  keyword overlap, not the time window**.

**Caveats, stated plainly:** the 0pp result is specific to a benchmark whose question mix
made the feature inapplicable to 94% of queries — that is partly a statement about the
benchmark. The pruning test is n=15 and synthetic. Neither is a proof that recency
weighting is worthless in general.

**But the direction matters for us**, because it is the same shape as our own R-25 result
(trimming inside memos cost −8.8pt): *the intuitive weighting knob did nothing, and the
lexical signal was doing the work we credited to it.* Our corpus is if anything more
literal-heavy than theirs, which is the case where BM25 wins hardest.

⭐ **The transferable point, and the reason this is the headline: the only project in the
genre that built a harness is the only one that found out its flagship feature doesn't
rank.** Everyone else ships the same feature list with no ablation. That is the argument for
P3 (eval harness) before P1/P2, made by someone else's data instead of my assertion.

---

## 2. Consensus across sources + consistency within a source — a mature field, wrongly forgotten

This is the strongest cluster by far, and it has a name Ben's phrasing almost exactly
reproduces: **truth discovery**.

**The core idea** (survey: [arXiv 1505.02463](https://arxiv.org/abs/1505.02463), Li et al.):
given many sources making conflicting claims about the same objects, *jointly* estimate
(a) which claim is true and (b) how reliable each source is — each from the other, iterated
to convergence. A source that agrees with the emerging consensus gains weight; a
high-weight source's claims gain credence. This is precisely "consensus among multiple
sources **and** consistency of a source" as a single estimation problem rather than two
features.

It is a pre-LLM literature (roughly 2007–2018, mostly KDD/VLDB — TruthFinder, AccuSim, CRH,
Latent Truth Model, Knowledge Vault) and it is **mathematically the same machinery as
crowdsourced label aggregation** (Dawid–Skene, 1979; `arXiv 1803.02781` for a fast variant).
That equivalence is useful: the crowdsourcing side has far better tooling.

**Implementations** (all small — this is the gap, not the theory):
- `joesingo/truthdiscovery` — 7★, GPL-3.0, **last pushed 2023-03**. Python 3 library, several
  classical algorithms. Effectively unmaintained but the algorithms don't rot.
- `kesavsivakumar/…CRH_Framework` — 4★, CRH specifically.
- `qcri/DAFNA-EA` — 11★, 2018, a model *collection*.

**The modern descendants**, which are where the live work is:

- **`arXiv 2607.22584` — Source-Aware Reranking, "A Reliability Prior Approach."** The
  simplest possible version of what Ben is describing: `score(q,d) = sim(q,d) × λ(s)` where
  λ is a per-source-type prior. Precision@5 **0.48 → 0.72** on a 120-doc health corpus, and
  it reduces adversarial-document retrieval. ⚠️ 120 docs, one domain, priors assigned by
  hand from metadata — a demonstration, not a result. But it is one multiply, and it is
  directly portable to memo's ranker.
- **`arXiv 2404.06809` — Credibility-Aware Generation (CAG)** and **`arXiv 2406.11497` —
  CrAM**, which modifies *attention heads* by document credibility. Both act at generation
  time, not storage time; CrAM needs white-box model access, so it's out for us.
- **`arXiv 2507.01281` — CARE-RAG**, conflict-driven summarization: detect that retrieved
  evidence disagrees and summarize the disagreement rather than silently picking a winner.
- ⭐ **`arXiv 2607.24117` — "Grading the Narrators: An Isnad-Rijal Framework for Claim-Level
  Provenance in Multi-Agent Knowledge Systems."** The most interesting paper in the sweep for
  *our* situation. It borrows classical hadith methodology — every claim carries its complete
  chain of transmitters (*isnad*), each transmitter graded per-domain (*rijal*), content
  criticism kept separate from chain criticism — and adds serve/review/quarantine routing.
  It explicitly frames the gap as: provenance systems record *what happened*, source-reliability
  systems grade *sources*, but nothing attaches **graded per-domain reliability to
  claim-level transmission chains**.

  This is memo's exact situation and I have not seen it named this well anywhere else. Our
  corpus is full of facts one agent learned from another agent who learned it from a third —
  and the 08-05 rewarm-pin cascade is a live example of a *false* claim propagating through
  a chain of confident retransmitters, each of which looked authoritative. We currently store
  `source_host` and `source_path`; we store nothing about *who told whom*.

---

## 3. Recency — real work exists, but see §1

- **`arXiv 2502.21024` — TempRetriever**: extends DPR by embedding the **query date and the
  document timestamp into the retrieval representation itself**, rather than post-hoc
  reweighting. Evaluated on ArchivalQA and ChroniclingAmericaQA. This is the architecturally
  serious version of the idea and it is *not* the same thing as a decay multiplier.
- **`getzep/graphiti`** — 29,622★, **Apache-2.0**, pushed 2026-08-05, 142pts on HN. Bi-temporal
  knowledge graph for agent memory: edges carry validity intervals and are **invalidated
  rather than deleted** when superseded. This is my P4 proposal, already built, permissively
  licensed, and by far the most popular thing in this whole sweep.
- **`xtdb/xtdb`** (3,038★, MPL-2.0) and `sirixdb/sirix` (1,211★) — bitemporal *databases*, if
  we ever wanted the storage layer rather than the retrieval layer. Almost certainly overkill.
- The **forgetting-curve papers** (`2601.03938` FOREVER, `2604.20300` FSFM, `2305.10250`
  MemoryBank) are about *continual learning and memory eviction*, not ranking. Relevant to a
  pruning/maintenance tool; not to retrieval quality.

⛔ **Standing constraint, unchanged and now externally corroborated:** do not build
supersession as recency weighting. A dated record stays true *as a record* and becomes wrong
*as an answer*; ageing it down breaks "when did they decide" in order to fix "what's the
rule". Graphiti's valid-interval + invalidation model is the right shape, and §1 says the
decay-multiplier shape doesn't even buy ranking accuracy.

---

## 4. Sentiment — the weakest leg, and I think partly the wrong frame

**Nothing exists for "sentiment-weighted vector storage."** The closest GitHub hits
(`jerbarnes/blse` 31★ 2021, `cemrifki/sentiment-embeddings` 10★) are sentiment
*classification* embeddings — representations tuned so that sentiment is linearly separable,
for the purpose of predicting sentiment. Nobody is weighting a knowledge base by it.

**"Sentiment derivation in vector form" does have a real, well-developed answer, but it is in
interpretability, not IR:** sentiment is recoverable as a **linear direction** in
representation space. `arXiv 2308.10248` (activation engineering), `2205.05124` (extracting
latent steering vectors), `2502.17420` (the geometry of refusal — concept cones). You can
derive a sentiment/affect direction from contrastive pairs and then project any document
embedding onto it to get a scalar. That is genuinely "sentiment in vector form" and it is
cheap — but it yields a *feature*, and nothing in the literature says what a knowledge base
should then do with it.

⚠️ **The framing concern, which I think is the useful contribution here.** For a knowledge
base, I suspect **sentiment is the wrong axis and stance is the right one.** Sentiment is
tone — is this text positive or negative. Stance is position — *does this source affirm or
deny the claim in question*. Truth discovery (§2) needs stance: two sources "agree" when they
assert the same value, and tone is irrelevant to that. There is an active stance literature
(`2112.13288` stance *quantification* — aggregate stance over a population; `2502.19954`
stance detection via small/large model **consistency verification**) that plugs straight into
the consensus machinery, where sentiment does not.

**Where sentiment *is* the right axis: `mind`.** Ben's 08-05 framing for mind was "up to the
minute market temperature… what is going to be factored into an upcoming earnings report."
That is a genuine sentiment-aggregation problem and it has its own literature — `2111.00526`
FinEAS, `2512.13749` (comparative evaluation of embedding representations for financial news
sentiment), `2512.03464` (cross-modal opinion integration). ⛔ mind is stood down; noting the
pointers only.

⇒ **My read: "sentiment" in the original ask is doing two different jobs.** For memo it means
"how much do I trust/weight this," which is credibility and stance. For mind it means
"what is the market feeling," which is sentiment proper. They want different machinery and I
would not build one feature for both.

---

## 5. The 2026 "cognitive memory database" genre — convergent and mostly unvalidated

A distinct crop of projects, nearly all first pushed in 2026, all with the same feature list
(temporal decay + contradiction detection + consolidation + often Hebbian reinforcement):

| repo | ★ | license | pushed | note |
|---|--:|---|---|---|
| `mem0ai/mem0` | 62,676 | Apache-2.0 | 2026-08-06 | the incumbent; not decay-focused |
| `getzep/graphiti` | 29,622 | Apache-2.0 | 2026-08-05 | bi-temporal, the serious one |
| `RedPlanetHQ/core` | 1,940 | NOASSERTION | 2026-08-03 | "personal AI OS", memory graph |
| `sachitrafa/YourMemory` | 262 | **CC BY-NC-4.0** | 2026-08-01 | **has real benchmarks — see §1** |
| `yantrikos/yantrikdb` | 171 | AGPL-3.0 | 2026-08-05 | consolidation + contradiction detection |
| `sanonone/kektordb` | 81 | NOASSERTION | 2026-08-06 | vector + temporal KG + "cognitive engine" |
| `eeshsaxena/Conflict-Aware-Graph-RAG` | 16 | **none** | 2026-07-16 | temporal contradiction detection, entropy path filtering |

⚠️ **Read this table skeptically.** Seven projects converging on an identical feature list
within months, mostly single-author, mostly with marketing sites — this is a trend being
chased, not a technique being validated. Only YourMemory published an ablation, and its
ablation says the flagship feature doesn't rank (§1). **Licensing bites too:** CC BY-NC-4.0
and AGPL-3.0 are both awkward for us, and `NOASSERTION` means GitHub could not parse a
license file — it is *not* a permissive default.

**Graphiti is the exception on every axis** — 29k★, Apache-2.0, actively pushed, and the one
design (bi-temporal invalidation) that is defensible on first principles rather than on
biological analogy.

---

## 6. What I would actually take into memo

In priority order, and all still parked pending Ben lifting rationing:

1. ⭐ **P3 eval harness first — now with external evidence.** §1 is the argument: the only
   project here that measured found its flagship feature does nothing. We have the same
   exposure on P1/P2.
2. **Source-reliability prior as a ranker multiplier** (`2607.22584`, `score = sim × λ(s)`).
   One multiply, interpretable, testable in an afternoon against the harness. Our λ would key
   on `source_host` + `migrated_by` + whether a memo was human-authored or agent-authored.
   ⚠️ Their result is 120 docs in one domain — treat as a hypothesis to test, not a result to
   port.
3. **Read Graphiti's bi-temporal model before writing P4.** Apache-2.0, 29k★. Same reason I
   said read groton's RRF before writing P1: someone has already made the mistakes.
4. **Claim-level transmission chains** (`2607.24117`). Speculative and the most work, but it
   names a failure mode we demonstrably have — agent-to-agent fact relay with no record of
   the chain. Even the cheap version (record *who told us*, not just *where it came from*)
   would have made the 08-05 pin-protocol cascade traceable.
5. **Stance, not sentiment, if we do any of this at all** (§4).

## 7. Recommend against

- **Ebbinghaus/biological decay as a ranking signal.** §1, and by the strongest author in
  the genre's own measurement.
- **Building sentiment weighting for memo.** No prior art, wrong axis (§4), and no story for
  what the score would *do*.
- **Adopting any of the small cognitive-memory DBs.** Licensing, single-author risk, and no
  validation. Read their ideas; don't take their code.
- **A truth-discovery implementation from scratch.** The classical algorithms assume many
  sources asserting values for the *same object* — memo's corpus is mostly single-source
  narrative notes. The machinery needs claims to compare, and we don't extract claims.
  ⇒ This is the honest blocker on §2, and it should be said out loud: **the best-developed
  body of theory in this sweep does not fit our data shape without a claim-extraction layer
  we do not have.**

## 8. Open questions for Ben

1. Is the target memo, or a shared core for memo/groton/mind/dojo? §4 splits differently
   depending — memo wants credibility, mind wants sentiment.
2. Truth discovery needs *claims*, not documents. Is claim extraction something we want at
   all? It is the gate on the whole §2 line of work.
3. Does "consistency among a source" mean self-consistency over time (does this source
   contradict its own past claims) or agreement with consensus? The literature does the
   second; the first is closer to what our corpus can actually support today.
