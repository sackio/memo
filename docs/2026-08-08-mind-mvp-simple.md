# mind MVP — ingest everything, then a UI to query and see it

**2026-08-08 20:27.** Ben: *"I just want all this ingested and a UI where I can query the
knowledge base, visualize it. Let's keep it simple and useful."*

⛔ **This supersedes the build order in `2026-08-08-mind-store-model.md`.** That document stays
as the reasoning — the schema, the traps, and what not to build are all still right. **But it
ranks work by architectural depth, and Ben has asked for it ranked by what he can look at.**

⭐ **The three things this changes.** Consensus and trends are **views, not algorithms** — every
one of them is a `GROUP BY` over rows we already have. There is **no scoring layer** in the
MVP. And the UI **ships on what exists** rather than waiting for the hard part.

---

## 1. The split that makes this shippable

⭐ **Most of the UI does not need the unbuilt piece.** Thesis clustering is the one genuinely
hard capability (§7 of the model doc — embeddings measure topic, not claim), and it gates
exactly two of five screens.

| screen | needs clustering? | ships |
|---|---|---|
| **Search** | ⛔ no | **now** |
| **Ticker** | ⛔ no | **now** |
| **Source / creator** | ⛔ no | **now** |
| **Filings** | ⛔ no | **now** (⚠️ 3 days deep) |
| **Themes / trends** | ✅ **yes** | after clustering |

⇒ **Build the four, ship them, add the fifth when clustering lands.** That is the whole
sequencing decision and it is why this can be useful in days rather than weeks.

---

## 2. The screens

### Search — the spine
One box, plus filters: **ticker · date range · source type · creator**. Results are mixed —
claims, articles, filings — each row showing source, timestamp, and the matching text.

Hybrid: structured filters first, then vector recall over the survivors, then keyword. ⛔ Ticker
is always a hard filter, never a vector match.

### Ticker — "what is being said about NVDA"
The one Ben described first. For an entity and a window:

- ⭐ **claim count AND distinct-creator count, side by side** — different numbers, and the gap
  is the story
- **direction split** — stacked bar, with the minority always visible, never averaged
- **mention volume over time** — sparkline against the ticker's own trailing baseline, raw
  series, ⛔ no threshold line
- **timeline** — claims, articles and filings on one axis, so a filing and the commentary
  around it are visually adjacent
- **recent filings** and **top evidence chunks**

### Source / creator — "who is this person and what do they say"
Their claims over time, the tickers they cover, ⭐ **their self-reversals** (same thesis,
direction flipped — nearly free once claims carry creator + cluster + timestamp), and their hit
rate shown beside their claims as decoration. ⚠️ Labelled as measured over the ~12% of claims
that resolve, so nobody reads it as an accuracy score.

### Filings — the EDGAR view
Recent filings, filterable by form and entity, linked to the commentary around them.

✅ **Historical backfill authorised by Ben 20:28 and in flight** (`mind` — migration 077 +
`edgar/backfill.py`). ⭐ Far cheaper than I estimated: SEC's `full-index/{year}/QTR{q}/master.idx`
is one file listing every filing accepted that quarter ⇒ **4 requests per year, 80 for two
decades.** ⚠️ Document text and XBRL facts remain unbuilt — that half is unchanged.

⚠️ **Show the coverage window on the screen regardless.** ⭐ **A forward-only poller, a
mid-backfill table, and a complete archive all present the same interface.**

### Themes / trends — the one worth waiting for
Clusters ranked by **distinct-creator growth** in the window. Per cluster: the adherent curve
over time, who is in it, when it started, the nearest **opposing** cluster, and evidence.

⭐ **Counted in distinct creators, never claims** — one creator posting nine times is not a
trend, and this is precisely where repetition masquerades as momentum.

---

## 3. What it runs on

**Nothing new.** Postgres + pgvector (162 GB, already there), the existing long-lived uvicorn
API, a small read-only frontend.

⚑ **Serve through the long-lived API, never by shelling out to the CLI.** Measured: the read
path is **~320ms warm** in-process, and 3.3–5.5s of the CLI's 5–7s is interpreter startup.
**Same fact from both ends — process lifetime is the latency lever.**

---

## 4. What is deliberately not in it

⛔ No scoring layer · no consensus aggregator (crowd-kit stays on the shelf) · no blended
confidence number · no baked thresholds — raw series, and **a threshold is a hypothesis wearing
a number's clothes** · no recency decay in ranking · no EDGAR historical backfill · no
resolution or outcome scoring (Ben, explicitly out of scope).

⭐ **The read surface is the product. Judgement stays with the trader** — structure is ours,
conclusions are theirs.

---

## 5. What has to exist first

| | | why |
|---|---|---|
| **1** | ✅ **News source identity — SOLVED, and it's a derived column** | Measured 20:29: **243 distinct URL domains**, ~698k articles (19.7%) originating outside Benzinga — zacks 298k, globenewswire 185k, fool 152k, marketwatch, seekingalpha, investing.com. ⇒ **`domain(url)` is the publisher**; the `publisher` field records the distributor. ⚑ Plus 16,502 distinct `author` values for a finer axis. ⛔ **Count per domain, and probably per `(domain, day)`** — benzinga.com is still 80.3%, so a naive per-article count lets one outlet vote 2.85M times |
| **2** | **Canonicalization at ingest** (local LLM, micro-batched) | the input to clustering |
| **3** | **Clustering** = vector recall top-10 + LLM adjudication | ⛔ **test canonicalized vs raw first** — margin > ~3.3pt or repeat runs. Gates the themes screen only |

⚠️ **Micro-batch the ingest LLM.** ~21 tok/s on the first call after idle against ~70 steady, so
a continuous feed makes one-call-per-document the pathological shape.

**Everything else the four shippable screens need already exists:** claims (61,872 / 6,338
videos), news (3.5M), filings (6,446), bitemporal timestamps on all three, point-in-time ticker
aliases, and the vector indexes.

---

## 6. The two honest caveats to put on the screen, not in a doc

1. ⚠️ **EDGAR coverage is 3 days and grows forward only.** A filings view that doesn't say so
   will be read as an archive. ⭐ **A forward-only poller and a complete archive present the
   same interface.**
2. ⚠️ **`videos.published_at_precision = 'date'` on 3,703 rows means the time of day is
   unknown, not midnight.** Any timeline that orders intraday must either filter these or draw
   them as day-level. Otherwise they silently sort *before* everything else that day — and on a
   trading timeline **that reads as prescience.**
