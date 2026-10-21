#!/usr/bin/env python3
"""Mine the Slack archive for operator decisions and check each against memo.

Ben, Slack #agents 2026-10-14 07:21 EDT: "let's use these credits more aggressively
to maintain and improve memo, using the slack conversations as a guide and verifying
memo facts".

⭐ DESIGN CONSTRAINT (token embargo, Ben 2026-10-04: "if a SCRIPT can do it, do not
spend an LLM on it"): this script does ALL the fetching, filtering and the first-pass
corpus lookup with no LLM. memo's /search is dense vector search over the live corpus,
reachable by plain HTTP, so "is this fact already recorded?" gets a RANKED SHORTLIST
for free. An LLM is spent only on the shortlist this prints, and only to judge the
cases a score cannot settle.

⛔ WHAT A LOW SCORE IS AND IS NOT. A low best-score means "nothing in the corpus embeds
near this text". That is a REASON TO LOOK, never a finding. Dense similarity over a
Slack utterance and a memo write-up of the same decision can be low because the wording
differs, and high because two unrelated texts share jargon. ⇒ This script reports
candidates; it never concludes a fact is missing. Treat its output as a work queue.

⛔ AND AN OPERATOR MESSAGE IS NOT AUTOMATICALLY A DURABLE FACT. "thanks", "yes", "ok"
carry no content on their own -- their meaning is in the message they answer. The script
therefore attaches the PRECEDING message in the same channel as context for any short
operator reply, because a bare "yes" is an authorization whose subject lives above it.
"""
import json, os, sys, time, glob, argparse, urllib.request, re

ARCHIVE = "/mnt/nas/data/slack-archive"
MEMO = "http://server4:8000"
ENV_PATH = "/home/ben/.config/atc-slack-bridge/env"

# ⛔ BOTH OF THESE WERE TOO LOOSE ON THE FIRST ATTEMPT AND THE LOOSE VERSION LOOKED
# LIKE A RESULT. Measured 2026-10-14 over a 30-day / 160-channel archive:
#   - walking back up to 3 messages for an antecedent, with a permissive ask-id
#     regex, matched 3,186 of 6,423 operator messages -- half of everything, including
#     Ben asking his OWN questions ("is s5 responsive"). A detector that fires on half
#     the population is not a detector.
#   - tightening to (ask hoisted on the IMMEDIATELY preceding message) AND (a decisive
#     reply) gives 1,089, and spot-checking the first 10 shows all 10 are real
#     authorizations with their ask text attached.
# ⇒ Require adjacency AND a decisive answer. Report the two populations separately.
ASK_RE = re.compile(r'^\*?`([a-z]{1,2}\d{0,3}[a-z]?)`\s+\S')
DECISIVE_RE = re.compile(
    r'^(yes|no|yep|yeah|ok|okay|sure|do it|go ahead|approved|approve|fix it|'
    r'proceed|please do|sounds good|agreed|confirmed|\d+\s*yes|yes\s*\d+|'
    r'not? (now|yet)|don\'?t|hold off|skip it|leave it)\b', re.I)
DIRECTIVE_RE = re.compile(
    r'\b(never|always|don\'?t|do not|stop|must|from now on|no longer|'
    r'instead of|only ever|prohibit|ban)\b', re.I)
# ⛔ FALSE-POSITIVE CLASS, measured on the first run: "don't"/"do not" followed by a
# PERCEPTION verb is reportage, not a directive -- "where is it? I do not see if going
# to me" scored lowest of 44 and is not an instruction at all. ⚠️ Note "don't WANT" is
# NOT in this list and must not be: "I don't want all this privacy stuff, memo is fully
# trusted" (Ben, 2026-10-01) is one of the strongest directives in the corpus.
NOT_DIRECTIVE_RE = re.compile(
    r"\b(do ?n[o']?t|do not)\s+(see|know|think|understand|remember|recall|get why)\b", re.I)
MIN_DIRECTIVE_LEN = 40


def owner_id():
    for line in open(ENV_PATH):
        line = line.strip()
        if line.startswith("SLACK_OWNER_USER_ID="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def load_archive(channels=None, since=0.0):
    """Return {channel: [msgs sorted by ts]}. Reports what it read."""
    out = {}
    for d in sorted(glob.glob(os.path.join(ARCHIVE, "*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        if channels and name not in channels:
            continue
        rows = []
        for f in sorted(glob.glob(os.path.join(d, "*.ndjson"))):
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        m = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if float(m.get("ts", 0)) >= since:
                        rows.append(m)
        if rows:
            # the archive is append-only and a channel can be re-archived, so dedup
            seen, uniq = set(), []
            for m in sorted(rows, key=lambda x: float(x["ts"])):
                if m["ts"] in seen:
                    continue
                seen.add(m["ts"])
                uniq.append(m)
            out[name] = uniq
    return out


def memo_search(query, limit=3):
    body = json.dumps({"query": query[:2000], "limit": limit}).encode()
    req = urllib.request.Request(f"{MEMO}/search", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except Exception as e:
        return {"_err": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=30.0)
    ap.add_argument("--channels", default="")
    # ⛔ NO MAGIC THRESHOLD. MEASURED 2026-10-14: querying /search with text almost
    # verbatim from a memo written 9 hours earlier scored 0.6281. So a "looks low"
    # cutoff of ~0.6 would flag an EXACT match as unrecorded and bury the real gaps
    # in noise. Dense scores on this corpus are not calibrated to "is this recorded",
    # and the absolute number means nothing without a same-corpus reference point.
    # ⇒ Rank ascending and review the bottom --top. The distribution is the instrument.
    ap.add_argument("--top", type=int, default=25,
                    help="review the N operator messages with the LOWEST best-match score")
    ap.add_argument("--max-score", type=float, default=None,
                    help="optional hard cutoff; omit and use --top instead")
    ap.add_argument("--all", action="store_true", help="print every candidate with its score")
    ap.add_argument("--throttle", type=float, default=0.25,
                    help="seconds between /search calls (default 0.25)")
    ap.add_argument("--kind", default="", help="restrict to AUTHORIZATION or DIRECTIVE")
    ap.add_argument("--state", default="/mnt/nas/data/slack-archive/_verify-state.ndjson",
                    help="append-only per-candidate scores; a re-run resumes from it "
                         "instead of re-spending the GPU embeds")
    ap.add_argument("--no-resume", action="store_true",
                    help="ignore the state file (re-score everything)")
    a = ap.parse_args()

    oid = owner_id()
    if not oid:
        sys.exit("no SLACK_OWNER_USER_ID in env file")
    chans = {c.strip() for c in a.channels.split(",") if c.strip()} or None
    since = time.time() - a.days * 86400
    data = load_archive(chans, since)
    if not data:
        sys.exit(f"ARCHIVE EMPTY for that scope -- NOT a finding. Run archive.py first. "
                 f"(looked in {ARCHIVE})")

    print(f"read {len(data)} channels, "
          f"{sum(len(v) for v in data.values())} messages since "
          f"{time.strftime('%Y-%m-%d', time.localtime(since))}")

    cands = []
    seen_ops = 0
    for name, msgs in data.items():
        for i, m in enumerate(msgs):
            if m.get("user") != oid:
                continue
            text = (m.get("text") or "").strip()
            if not text:
                continue
            seen_ops += 1
            askid, antecedent = None, ""
            if i > 0:
                for x in (msgs[i - 1].get("attachments") or []):
                    mm = ASK_RE.match((x.get("text") or "").strip())
                    if mm:
                        askid = mm.group(1)
                        antecedent = (x.get("text") or "").strip()
                        break
            if askid and DECISIVE_RE.match(text):
                kind = "AUTHORIZATION"
            elif (DIRECTIVE_RE.search(text) and len(text) >= MIN_DIRECTIVE_LEN
                  and not NOT_DIRECTIVE_RE.search(text)):
                kind = "DIRECTIVE"
            else:
                continue
            cands.append({"kind": kind, "ask": askid, "channel": name, "ts": m["ts"],
                          "when": time.strftime("%Y-%m-%d %H:%M",
                                                time.localtime(float(m["ts"]))),
                          "text": text, "antecedent": antecedent})

    from collections import Counter
    kinds = Counter(c["kind"] for c in cands)
    print(f"operator messages seen: {seen_ops}  ->  candidates: {len(cands)} "
          f"({dict(kinds)})")
    if a.kind:
        cands = [c for c in cands if c["kind"] == a.kind.upper()]
        print(f"restricted to {a.kind.upper()}: {len(cands)}")
    # ⛔⛔ CHECKPOINT EVERY SEARCH, AND FLUSH. MEASURED 2026-10-14: the first full
    # 30-day pass ran ~40 min, was killed by `timeout 2400`, and produced ZERO
    # bytes -- every GPU embed it had already spent was lost, because results were
    # accumulated in memory and printed only at the end. A long job whose output
    # exists only at the end has no partial result AND no progress signal, so a
    # kill is indistinguishable from "found nothing".
    # ⚠️ AND THE EXIT STATUS LIED: the wrapper was `verify.py > out; echo ...`, a
    # compound that exits as its LAST stage, so the runner reported SUCCESS for a
    # job killed at 124. Read the script's own status, never the wrapper's.
    # ⇒ One NDJSON line per scored candidate, flushed, keyed <channel>@<ts>. A
    #   re-run loads it and skips what is already scored -- each skip is one barn
    #   GPU embed not spent twice.
    done = {}
    if not a.no_resume and os.path.exists(a.state):
        with open(a.state) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue          # a torn last line from a kill: skip, re-score it
                done[rec["key"]] = rec
        print(f"resume: {len(done)} candidates already scored in {a.state}")

    out, searched, t0 = [], 0, time.time()
    sf = open(a.state, "a")
    for n, c in enumerate(cands):
        key = f"{c['channel']}@{c['ts']}"
        if key in done:
            rec = done[key]
            c["best"] = rec["best"]
            c["best_title"] = rec["best_title"]
            c["best_id"] = rec.get("best_id", "")
            out.append(c)
            continue
        if searched and a.throttle:
            time.sleep(a.throttle)   # :8000 is LIVE infra for ~50 seats, and each
                                     # /search costs a barn GPU embed that `models`
                                     # schedules. Do not hammer it.
        q = c["text"] if not c["antecedent"] else c["text"] + "\n\n" + c["antecedent"]
        r = memo_search(q, limit=3)
        searched += 1
        if isinstance(r, dict) and r.get("_err"):
            c["best"], c["best_title"] = None, f"SEARCH ERROR {r['_err']}"
        else:
            hits = r if isinstance(r, list) else (r.get("results") or [])
            if hits:
                top = hits[0]
                c["best"] = round(top.get("score", 0), 4)
                # ⚠️ title can be NULL in this corpus (102 empty-titled docs measured
                # 2026-09-09), so .get("title","") is not enough -- it returns None.
                doc = top.get("document") or {}
                c["best_title"] = ((doc.get("title") or "(untitled)"))[:95]
                c["best_id"] = ((doc.get("id") or ""))[:8]
            else:
                c["best"], c["best_title"], c["best_id"] = 0.0, "(no hits)", ""
        out.append(c)
        sf.write(json.dumps({"key": key, "best": c["best"],
                             "best_title": c["best_title"],
                             "best_id": c.get("best_id", "")}) + "\n")
        sf.flush()               # ⛔ the whole point -- an unflushed checkpoint is no checkpoint
        if searched % 25 == 0:   # progress to STDERR so it survives a redirect of stdout
            el = time.time() - t0
            rate = searched / el if el else 0
            left = (len(cands) - len(out)) / rate if rate else 0
            print(f"  ... {len(out)}/{len(cands)} ({searched} searched this run, "
                  f"{rate:.2f}/s, ~{left/60:.0f} min left)", file=sys.stderr, flush=True)
    sf.close()

    out.sort(key=lambda x: (x["best"] is not None, x["best"] if x["best"] is not None else 9))
    scored = [c["best"] for c in out if c["best"] is not None]
    if scored:
        import statistics
        print(f"score distribution over {len(scored)} searched: "
              f"min {min(scored):.4f} p25 {statistics.quantiles(scored, n=4)[0]:.4f} "
              f"median {statistics.median(scored):.4f} max {max(scored):.4f}"
              if len(scored) > 3 else f"scores: {scored}")
    shown = 0
    print("\n" + "=" * 100)
    for c in out:
        if a.max_score is not None and c["best"] is not None and c["best"] >= a.max_score:
            continue
        if not a.all and a.max_score is None and shown >= a.top:
            break
        shown += 1
        print(f"[{c['best']}] {c['kind']:13s} {c['channel']:12s} {c['when']}"
              f"{'  ask=' + c['ask'] if c.get('ask') else ''}")
        print(f"    BEN: {c['text'][:300]}")
        if c["antecedent"]:
            print(f"    RE : {c['antecedent'][:240]}")
        print(f"    memo top: {c.get('best_id','')} {c['best_title']}")
    print("=" * 100)
    print(f"showed {shown} of {len(out)} operator messages, lowest-scoring first "
          f"-- A WORK QUEUE, NOT A FINDING LIST. A low score means 'go look', "
          f"never 'this is missing'.")
    errs = sum(1 for c in out if c["best"] is None)
    if errs:
        print(f"⛔ {errs} search ERRORS -- those are NOT SEARCHED, not absent.")
    json.dump(out, open("/tmp/slack-verify-candidates.json", "w"), indent=1)
    print("full ranked output: /tmp/slack-verify-candidates.json")


if __name__ == "__main__":
    main()
