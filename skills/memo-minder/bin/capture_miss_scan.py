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
# ⛔ AGENT-SENSE OBJECTS EXCLUDED 2026-09-10 (Ben: "fix all"). This fleet parks
# WORK, not cars: "both parked warnings cleared", "the question is parked",
# "exec still parked". The domain-word check within +/-120 chars does not save
# you here, because a long agent report almost always contains one somewhere.
# ⇒ Decide on the OBJECT of the verb, which is unambiguous: a vehicle is never
# a warning, a job or a thread. Measured: this was the last surviving false
# positive after the keypad/confirmation fixes, from agentkit session f52d31d0.
PARK_WEAK = re.compile(
    r"(parked(?!\s+(?:warning|job|session|seat|task|work|thread|question|topic"
    r"|item|process|run|issue|decision|ticket|branch|PR|change|review)s?\b)|valet)", re.I)
# Two parts, because the airport codes are DESIGNATORS and the rest are words.
# Measured 2026-07-31: running the codes unanchored under re.I made `ORD` match
# rec*ord*s / co*ord*inator / w*ord* and `EWR` match r*ewr*itten — between them
# they licensed 14 of that run's 24 hits, all noise. Every one of those hits
# also contained the word "parked" in its agent sense ("exec still parked",
# "POSTURE = PARKED"), so the pair read as a real parking datum. This is the
# same defect the comment on line 5 fixes for `row ?[A-Z]`; the code list was
# simply never given the same treatment. A detector whose output is ~90% noise
# is one nobody reads, which is how the next real datum gets missed.
# `terminal` USED to sit here bare and does not belong bare: it is an airport word
# only when a designator follows it. Measured 2026-08-27 — bare `terminal` licensed
# BOTH of that cycle's false positives, each an agent-sense "parked" within 120 chars
# of an unrelated terminal: "the work is parked on the armed timer" (electric seat,
# an EV-charger terminal) and "the zoning question is parked" (ha seat, likewise).
# This is the same defect as the unanchored ORD/EWR codes fixed on 2026-07-31 and the
# case-sensitivity fix on line 5: a token that is a domain word in one sense and
# ordinary English in another must carry the thing that disambiguates it.
# ⇒ Airport terminals are written "Terminal B" / "Terminal 4"; shell and charger
# terminals are not. Requiring the designator keeps every real hit and drops both.
PARK_DOMAIN_WORD = re.compile(
    r"(\bcar\b|garage|parking|\block\b|\blot\b(?! of\b)|terminal ?[A-E0-9]\b"
    r"|airport|shuttle|\bdeck\b|logan)", re.I)
PARK_DOMAIN_CODE = re.compile(r"\b(BOS|JFK|LGA|MHT|EWR|SFO|LAX|ORD|DCA|IAD|DFW)\b")


def _park_domain(window: str):
    return PARK_DOMAIN_WORD.search(window) or PARK_DOMAIN_CODE.search(window)

# ⛔ EVERY TOKEN HERE MUST CONTAIN A DIGIT. Corrected 2026-09-10 (Ben: "fix all").
# `confirmation:?\s*[A-Z0-9]{5,7}` accepted a run of pure uppercase LETTERS, so
# "confirmation: PASSED" and "confirmation: FAILED" matched — and this seat writes
# both constantly in its own watch reports. A real record locator always carries a
# digit; an English word in caps never does. Same fix applied to conf# and booking.
BOOKING = re.compile(r"(confirmation:?\s*(?=[A-Z0-9]{5,7}\b)[A-Z0-9]*\d[A-Z0-9]*\b|\bPNR\b|record locator|conf ?#\s*(?=[A-Z0-9]{4,}\b)[A-Z0-9]*\d[A-Z0-9]*\b|booking\s*(?=[A-Z0-9]{5,8}\b)[A-Z0-9]*\d[A-Z0-9]*\b|voucher ?#)")
# ⛔ BARE `keypad` REMOVED 2026-09-10 (Ben: "fix all"). It was the single largest
# false-positive source in twelve consecutive 100%-FP cycles, and the reason is
# self-reference: this seat's own standing pin and watch reports contain the word
# `keypad` while DESCRIBING this detector, so the instrument matched its own
# documentation and reported itself as a captured datum. ⭐ An instrument whose
# trigger vocabulary appears in its own description will measure itself.
# ⇒ `keypad` now requires an actual code within ~40 chars, which is the thing worth
# capturing anyway. A keypad with no number in sight is prose, not a datum.
CODE    = re.compile(r"(lock ?box|gate code|door code|garage code"
                     r"|keypad[^.\n]{0,40}?\b\d{3,}"
                     r"|wifi password|combo ?\d{3,}|combination ?\d{3,})", re.I)
CASH    = re.compile(r"(paid \$?\d+ (?:cash )?to [A-Z]\w+|owes? \$?\d+ to [A-Z]\w+|Venmo(?:ed|d)? [A-Z]\w+|Zelle(?:d)? [A-Z]\w+)")
# ⚠️ `gate ?[A-Z]\d` tightened 2026-09-10: it must not be preceded by a word that
# makes it a different kind of gate. This fleet says "push-gate A1", "gate B2 check"
# about CI gates constantly, and an airport gate is never qualified that way.
SEAT    = re.compile(r"(\bseat ?\d{1,2}[A-F]\b"
                     r"|(?<![-\w])(?<!push.)(?<!ci.)gate ?[A-Z]\d{1,2}\b)")

# Engineering-chatter reject, applied to a WINDOW around the match only.
# ⛔ ADDED 2026-09-10: the detector's OWN vocabulary. Four of five hits on the
# 2026-09-10 cycle were this seat's prose ABOUT the detector — its standing pin
# naming `capture_miss_scan.py`, and the positive control quoting its own anchor
# ("Level 6"). Nothing in the old reject list covered self-description, so the
# instrument counted its own documentation as findings and the count only grew.
ENG = re.compile(r"(```|gate=|\bPASS\b|\bFAIL\b|commit |\bsha\b|FR-\d|\bT\d{3}\b|attest|beacon|kubectl|docker|pytest|stderr|traceback|@[0-9a-f]{8}\b|halt-trigger|memo_store|\.jsonl"
                 r"|capture.?miss|false.positive|\bFP\b|detector|positive control|PARK_|CAPMISS|memo-minder|minder cycle)", re.I)
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
