import json, os, re, glob
CUTOFF = os.environ.get("CAPMISS_CUTOFF", "2026-07-28")
HOST = os.uname().nodename

# Designators are CASE-SENSITIVE: re.I made `row ?[A-Z]` match "rows".
PARK_DESIG = re.compile(r"\b(?:[Ll]evel ?\d{1,2}|[Rr]ow ([A-Z])(?![A-Za-z])|[Ss]pot ?([0-9]{1,4}|[A-Z]\d{0,3})(?![A-Za-z])|[Ss]pace ?\d{1,4}(?![A-Za-z])|[Aa]isle ?\d{1,3}(?![A-Za-z]))")
# Unconditional: these phrases are only ever about a vehicle.
#
# `parked (at|in) (the )?[A-Z\d]` USED to live here and does not belong: it is
# not unconditional, and being here let it skip the domain check entirely.
# Measured 2026-07-31 — it fired on "parked at Ben's request" and "still parked
# at 64df05d1" (a git sha). The skill text has listed this as a known defect
# since 2026-07-30; the list was corrected but this rule kept its exemption.
# Demoted to PARK_WEAK, where a parking-domain word within ±120 chars decides.
PARK_STRONG = re.compile(r"(where I parked|where i left the car|left the car (at|in)|car is (at|in|parked))")
# Weak: needs a parking-domain word nearby. NOTE: no "parked on" — that's metaphorical
# ("the session is parked on X") far more often than literal.
PARK_WEAK = re.compile(r"(parked|valet)", re.I)
# Two parts, because the airport codes are DESIGNATORS and the rest are words.
# Measured 2026-07-31: running the codes unanchored under re.I made `ORD` match
# rec*ord*s / co*ord*inator / w*ord* and `EWR` match r*ewr*itten — between them
# they licensed 14 of that run's 24 hits, all noise. Every one of those hits
# also contained the word "parked" in its agent sense ("exec still parked",
# "POSTURE = PARKED"), so the pair read as a real parking datum. This is the
# same defect the comment on line 5 fixes for `row ?[A-Z]`; the code list was
# simply never given the same treatment. A detector whose output is ~90% noise
# is one nobody reads, which is how the next real datum gets missed.
PARK_DOMAIN_WORD = re.compile(
    r"(\bcar\b|garage|parking|\block\b|\blot\b(?! of\b)|terminal"
    r"|airport|shuttle|\bdeck\b|logan)", re.I)
PARK_DOMAIN_CODE = re.compile(r"\b(BOS|JFK|LGA|MHT|EWR|SFO|LAX|ORD|DCA|IAD|DFW)\b")


def _park_domain(window: str):
    return PARK_DOMAIN_WORD.search(window) or PARK_DOMAIN_CODE.search(window)

BOOKING = re.compile(r"(confirmation:?\s*[A-Z0-9]{5,7}\b|\bPNR\b|record locator|conf ?#\s*[A-Z0-9]{4,}|booking\s*[A-Z0-9]{5,8}|voucher ?#)")
CODE    = re.compile(r"(lock ?box|gate code|door code|garage code|keypad|wifi password|combo ?\d{3,}|combination ?\d{3,})", re.I)
CASH    = re.compile(r"(paid \$?\d+ (?:cash )?to [A-Z]\w+|owes? \$?\d+ to [A-Z]\w+|Venmo(?:ed|d)? [A-Z]\w+|Zelle(?:d)? [A-Z]\w+)")
SEAT    = re.compile(r"(\bseat ?\d{1,2}[A-F]\b|\bgate ?[A-Z]\d{1,2}\b)")

# Engineering-chatter reject, applied to a WINDOW around the match only.
ENG = re.compile(r"(```|gate=|\bPASS\b|\bFAIL\b|commit |\bsha\b|FR-\d|\bT\d{3}\b|attest|beacon|kubectl|docker|pytest|stderr|traceback|@[0-9a-f]{8}\b|halt-trigger|memo_store|\.jsonl)", re.I)
NOISE = re.compile(r"^(<system-reminder|<local-command|<command-name|Caveat:|\[SYSTEM)")
STORE = re.compile(r"(memo_store|POST /documents)", re.I)

def find_hit(s):
    m = PARK_STRONG.search(s)
    if m: return ("parking", m)
    m = PARK_DESIG.search(s) or PARK_WEAK.search(s)
    if m:
        lo, hi = max(0, m.start()-120), m.end()+120
        if _park_domain(s[lo:hi]): return ("parking", m)
    for name, rx in (("booking",BOOKING),("access-code",CODE),("cash",CASH),("seat-gate",SEAT)):
        m = rx.search(s)
        if m: return (name, m)
    return None

out, seen = [], set()
for path in glob.glob("/home/ben/.claude/projects/*/*.jsonl"):
    try:
        if os.path.getsize(path) < 20480: continue
    except OSError: continue
    proj = path.split("/projects/")[1].split("/")[0]
    uuid = os.path.basename(path)[:-6]
    pend = []
    try: fh = open(path, errors="replace")
    except OSError: continue
    with fh:
        for line in fh:
            try: e = json.loads(line)
            except Exception: continue
            if (e.get("timestamp") or "")[:10] < CUTOFF: continue
            if e.get("isSidechain") or e.get("agentId"): continue
            msg = e.get("message") or {}
            role, c = msg.get("role"), msg.get("content")
            if role == "user" and isinstance(c, str):
                s = c.strip()
                if NOISE.match(s) or len(s) < 15: continue     # no upper bound: Slack
                hit = find_hit(s)                              # relays carry long briefs
                if not hit: continue
                name, m = hit
                if ENG.search(s[max(0,m.start()-200):m.end()+200]): continue
                i = max(0, m.start()-90)
                ctx = re.sub(r"\s+", " ", s[i:m.end()+150])
                k = (proj, name, m.group(0), ctx[:70])
                if k in seen: continue                          # collapse retry loops
                seen.add(k)
                pend.append({"host":HOST,"proj":proj,"uuid":uuid[:8],"ts":e.get("timestamp",""),
                             "trigger":name,"match":m.group(0)[:60],"ctx":ctx,"turns":0,"stored":None})
            elif role == "assistant":
                blob = json.dumps(c)[:20000]
                for p in pend:
                    if p["stored"] is None:
                        p["turns"] += 1
                        if STORE.search(blob): p["stored"] = "store-call"
                        elif p["turns"] >= 5: p["stored"] = "none"
    for p in pend:
        if p["stored"] is None: p["stored"] = "none"
    out.extend(pend)
print(json.dumps(out))
