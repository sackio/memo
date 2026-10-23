#!/usr/bin/env python3
"""DM each owning seat the findings that are THEIRS, once.

⛔⛔ WITHOUT THIS, THE NIGHTLY PHASES ARE NOT MAINTENANCE. scan.py and pins.py
   print to stdout, and in cron nobody reads stdout. A detector wired to a job
   whose output no one reads is indistinguishable from no detector, and it is
   WORSE than none, because the cycle log shows a phase running green.

⛔ ONLY NEW FINDINGS ARE SENT. A nightly repeat of the same finding is noise,
   and noise is how a real finding gets filtered out by the recipient. State
   lives in seen.json keyed on (owner, kind, ref); anything already sent is
   silent until it changes. ⚠️ That means a finding someone IGNORED also goes
   quiet -- so the digest to `memo` always carries the FULL current set, not
   just the new ones, and that is the copy that notices a finding rotting.

⛔ NOT A BROADCAST. One DM per seat that actually has a finding -- 4 seats on
   the first real run, not 135. Ben authorised routing to owning seats in
   #memo 2026-10-23 ("owner makes the edit, I detect and reverify nightly").
   MAX_SEATS caps a runaway: if a corpus incident ever dangles hundreds of
   refs at once, this stops and tells `memo` instead of DMing the fleet.
"""
import json, re, subprocess, sys, time, urllib.request
from datetime import datetime
from pathlib import Path

BROKER = "http://192.168.1.168:3030"


def get(path):
    with urllib.request.urlopen(BROKER + path, timeout=25) as r:
        return json.loads(r.read())

STATE = Path.home() / ".memo-instruction-audit" / "seen.json"
MAX_SEATS = 12
# ⛔⛔ THE UNANCHORED LANE IS A BACKLOG, NOT AN INCIDENT, AND IT MUST DRIP.
#    Measured fleet-wide 2026-10-23: 175 findings across 65 owners, `server_admin`
#    alone holding 64. Pushing that through the dangling-ref path would trip
#    MAX_SEATS and route NOTHING, and raising MAX_SEATS to fit it would DM 65
#    seats in one night -- the fleet-wide broadcast Ben prohibits without
#    authorization, and noise that teaches every recipient to filter this sender.
# ⇒ A few seats a night, OLDEST CLAIM FIRST, never repeated (seen.json). 65
#   owners drains in ~22 nights and the rottenest claims leave first.
# Same directive, 2026-10-23 10:06. Raised from 3; the backlog is 6 owners.
UNANCHORED_SEATS_PER_NIGHT = 10000
UNANCHORED_MAX_LINES = 12          # per seat; the rest are counted, not listed
UNANCHORED_MIN_AGE_DAYS = "30"     # a claim dated last week is not yet rot

# ⭐⭐ THE PIN LANE IS A DAILY NAG, NOT A ONE-SHOT NOTICE. Ben, Slack #memo
#    2026-10-23 09:55, answering ask a15 (he had my proposal of a 7-day
#    re-check): "I think that's too long I think it should be daily and yes I
#    agree that you shouldn't be editing the pen but I think it really needs to
#    be brought up to the holder and they're told that it needs to be revised".
# ⇒ Three things follow, and the first two are a REVERSAL of how every other
#   lane in this file behaves:
#     1. REPEATS. Every other lane sends once and records it in seen.json. This
#        one re-asks DAILY while the line is still in the pin.
#     2. THE DM SAYS "REVISE THIS", not "FYI". A notice a seat may ignore is the
#        same as no notice; the holder is the only one who can edit a pin, so
#        the ask has to be an ask.
#     3. VERIFICATION IS MECHANICAL. "Fixed" means the line is GONE from the
#        live pin on the next run -- never a seat's own say-so.
# ⛔⛔ NO DRIP. Ben, Slack #memo 2026-10-23 10:06: "go ahead and get everyone
#    there notices now I think that probably something should happen early in
#    the morning and something should happen at night time basically multiple
#    sweeps". My 3-a-night drip was built for the FILE lane, where one seat can
#    own 64 findings; the pin lane averages under two lines a seat, so dripping
#    it bought nothing and delayed everything by six weeks.
PIN_FIRST_CONTACT_SEATS = 10000    # effectively unlimited -- every holder, now
PIN_NAG_SEATS_MAX = 10000
PIN_MAX_LINES = 12                 # per DM; the rest are counted, not listed
PIN_ESCALATE_DAYS = 3              # asked this many times, still there -> Ben
PIN_STATE = Path.home() / ".memo-instruction-audit" / "pin-nag.json"
# ⛔⛔ IN THE REPO, NOT IN $HOME -- UNLIKE THE OTHER TWO LEDGERS, AND DELIBERATELY.
# seen.json and pin-nag.json record what I DID; losing them costs a duplicate
# notice. This file records what ANOTHER SEAT DECIDED -- nine holders have told
# me a specific line is not a claim and I promised to stop asking. Losing it
# means breaking that promise to nine seats with no way to know I had made it,
# and $HOME is HOST-LOCAL, so a run from another host starts with none of them.
# ⇒ Shared NFS, in git, reviewable, and it travels with the detector that reads it.
PIN_EXEMPT = Path(__file__).resolve().parent / "pin-exempt.json"


def claim_key(rest: str) -> str:
    """A pin claim's identity, STABLE ACROSS DAYS.

    ⛔⛔ THE FIRST VERSION KEYED ON THE WHOLE FINDING LINE, WHICH STARTS
       `<pin-id8>  <age>d  <text>` -- AND THE AGE CHANGES EVERY DAY. So tomorrow
       every line would have looked like a brand-new claim: the ask counter would
       never pass 1, nothing would ever reach the 3-ask escalation Ben asked for,
       and the "no longer flagged" count would have reported the entire fleet as
       fixed and re-broken every 24 hours. Caught before the first repeat sweep,
       by asking what the key would be tomorrow.
    ⚠️ The PIN ID is dropped too: a seat that rewrites its pin gets a new id, and
       keying on it would re-introduce every surviving line as first contact.
       The text is the claim; the carrier is not.
    """
    t = re.sub(r"^\S+\s+(?:\d+|\?)d\s+", "", rest).strip()
    return t


def load_exempt() -> dict:
    """Lines a holder has DECLARED not-a-claim. Option 3 in the DM, and the DM
    promises "I will stop flagging that line".

    ⭐⭐ WITHOUT THIS THE LANE BREAKS ITS OWN WORD ON DAY TWO, which is the
       cry-wolf failure arriving by the politest possible route. Within 20
       minutes of the first fleet-wide send, four seats had replied "option 3,
       please stop flagging it" -- `assistant` (a method rule), `apis` (a project
       done-definition), `cloud` (a standing lesson), `tradedesk`/fw (each line
       already cites a run id). All four were RIGHT, and a detector that re-asks
       after being correctly told no is one the recipient starts filtering.
    ⚠️ An exemption is the HOLDER's judgement, recorded with who said it and
       when. It is not a silence I grant myself.
    """
    try:
        return json.loads(PIN_EXEMPT.read_text())
    except Exception:
        return {}
# ⚠️ HOST-LOCAL ($HOME), like seen.json. The cycle runs on server4 only (Ben /
#    system, 10-22), so one ledger is correct -- but a run from another host
#    would re-introduce every seat as first contact.

# ⛔ SEVERITY SPLIT. Only a DEFECT earns a DM. Sitting at 86% of the session-start
#    budget is a fact about headroom, not something broken, and DMing 12 seats
#    about headroom every night is exactly how the 2 seats that are genuinely
#    TRUNCATED get filtered out as noise. Advisories go to the digest only.
ACTIONABLE = {"OVER-CLIFF", "dangling-memo-ref", "unreadable", "budget-unmeasured"}
# ⚠️ `no-ttl-never-retires` is DELIBERATELY NOT HERE. It is true of 82 of the 94
#    live statuses, so routing it would hand one seat a 47-line DM about rows it
#    posted in July -- bulk hygiene, not an incident. It goes to the digest, where
#    it is one number someone can act on in one pass. The two that ARE routed are
#    the ones where a reader can be actively misled TODAY.
STATUS_ACTIONABLE = {"contradicted-by-later", "dangling-memo-ref",
                     "churn-posting-not-replacing"}
# ⭐ STANDING ARRANGEMENT WITH `atc`, 2026-10-23. Ben, Slack #atc ts 1792762352,
#    verbatim: "yes and this is good you and memo should continuously monitor for
#    that stuff" — confirmed by me in #atc directly, not taken on atc's relay,
#    because it authorises recurring DELETION.
#    Division of labour atc asked for: a status row whose agent_id does not
#    resolve to a live subscriber AND is older than this many days goes to `atc`,
#    who deletes it without asking Ben again. Anything owned by a LIVE seat goes
#    to that seat instead — I never ask atc to touch a live seat's row.
# ⚠️ BOTH SIDES RECORD THIS. atc holds its half; mine is here and in memo. An
#    agreement recorded on one side is invisible to the side that forgot, and the
#    side that forgot has no reason to know an agreement existed.
ATC_SEAT = "atc"
ATC_DEAD_ROW_MIN_AGE_DAYS = 30
HERE = Path(__file__).resolve().parent

# skill directory -> the seat that has DEMONSTRATED ownership by acting on a
# finding in it. ⛔ Earn an entry; do not add one from a plausible name match.
HOSTLOCAL_OWNERS = {
    "docker-heal": "system",      # fixed 3 dead ids 2026-10-23, all four hosts
    # ⭐ CLAIMED BY `cluster`, DM 2026-10-23 10:49, naming each skill itself:
    #   "Mine. Route all findings for server_admin/skills/* to `cluster`."
    #   They also corrected me that mount-presence-gate and volume2-93-trigger
    #   are theirs, not `system`'s -- I had read that off a crontab comment.
    "allocation-gatekeeper": "cluster",
    "cluster-capacity": "cluster",
    "cluster-heal": "cluster",
    "k8s-gpu": "cluster",
    "k8s-job": "cluster",
    "k8s-longrun": "cluster",
    "resource-priorities": "cluster",
}

# Repo-path owners ESTABLISHED by the seat itself or ruled by the operator --
# never inferred from the directory name, which is what produced three
# unaddressable phantoms this morning.
REPO_OWNERS = {
    # Ben, Slack #memo 2026-10-23 10:49: "network will own omada". Operator
    # ruling, so it needs no confirmation from `network` -- but they are told,
    # because an ownership they have not heard about is one they cannot act on.
    "omada": "network",
    # `massive`, DM 2026-10-23 11:31, asked directly: "Yes, I own
    # code/forks/mcp_polygon; route its findings to me." ⚠️ `tradedesk` NAMED
    # them first and that was NOT enough -- a third party's say-so is not
    # ownership, so they were asked and they answered. The repo holds the 22
    # massive-* skills, 18 of them symlinked into ~/.claude/skills.
    "forks/mcp_polygon": "massive",
    # ⚠️ `server_admin` IS SHARED, AND THIS ENTRY IS ONLY TRUE OF THE FILES I
    #    CURRENTLY FIND THERE. Ben, same message: "a couple different folks are
    #    going to own things cluster models and servers will probably own things
    #    there and you can sort it out."
    #    Established so far, each by the seat itself:
    #      · `cluster` (DM 10:49) owns server_admin/skills/* and named all seven:
    #        allocation-gatekeeper, cluster-capacity, cluster-heal, k8s-gpu,
    #        k8s-job, k8s-longrun, resource-priorities. Also corrected me that
    #        mount-presence-gate and volume2-93-trigger are theirs, not
    #        `system`'s -- I had read that off a crontab comment.
    #      · `models` (DM 10:48) owns ONLY server_admin/k8s model manifests
    #        (embeddings, ollm, asr, tsfm) and explicitly disclaimed the skill
    #        files.
    #      · `servers` -- not yet asked. Ansible/host-install content is theirs
    #        by the roster, and nothing I have flagged lands there yet.
    # ⛔⛔ THIS ENTRY IS A LIST OF (PATH RULE, SEAT), NOT A BARE SEAT, AND THAT IS
    #    THE WHOLE POINT. It used to be `"server_admin": "cluster"` with a
    #    comment saying a non-SKILL.md finding "must re-open the question rather
    #    than inherit this mapping" -- and NOTHING ENFORCED IT. Every finding
    #    under the repo today is a SKILL.md, so the map was right by accident;
    #    the first Ansible or host-install finding would have been DMd to
    #    `cluster`, who would correctly say "not mine", while `servers` never
    #    heard. ⭐ A RULE THAT LIVES ONLY IN A COMMENT IS NOT A GUARD -- it is a
    #    note to whoever reads the file, and the code is what routes.
    #    An unmatched path returns None ON PURPOSE and lands in the advisory as
    #    an unassigned subtree, which is the question being re-opened.
    "server_admin": [
        # `cluster`, DM 2026-10-23 10:49, naming all seven skills explicitly.
        (re.compile(r"(^|/)(skills/|SKILL\.md\b)"), "cluster"),
        # `models`, DM 2026-10-23 10:48: the k8s model manifests (embeddings,
        # ollm, asr, tsfm) and NOTHING else -- they explicitly disclaimed the
        # skill files, so this rule sits BELOW cluster's and must stay there.
        (re.compile(r"(^|/)k8s/"), "models"),
    ],
}

# ⛔⛔ A SKILL FILE OR CLAUDE.md CANNOT BE EDITED BY THE SEAT THAT OWNS IT.
#    `/home/ben/.claude/CLAUDE.md`: "Agents are strictly prohibited from editing
#    CLAUDE.md or skill files without explicit operator authorization. Propose
#    the change to the operator; do not make it."
# ⇒ SO THE FILE LANE'S THREE OPTIONS WERE NEVER AVAILABLE TO THE HOLDER, and a
#   daily re-ask would be nagging a seat about something it is forbidden to do --
#   the alarm-that-fires-on-correct-behaviour failure, with the seat correctly
#   refusing every time. Found by `cluster` 2026-10-23 10:49, who claimed the
#   findings and then said: "skill-file edits need Ben's explicit authorization,
#   so I can't anchor or delete the lines unilaterally. I'll fold your list into
#   one proposal to Ben. A repeat ask before he answers will get no new action."
# ⇒ For these files the deliverable is a PROPOSAL TO BEN, and this lane's real
#   output is a queue for HIM, not a task for the seat. Pins are different and
#   that is why the pin lane works: a pin is not a skill file, and 35 holders
#   edited theirs within the hour.
SKILLFILE = re.compile(r"(SKILL\.md|CLAUDE\.md|/\.claude/)", re.I)


_seats_cache = None


def seat_exists(name: str) -> bool:
    """Is this an addressable seat? ⛔ ASKED, NEVER ASSUMED -- the broker answers.

    ⭐ The point of resolving `projects/guide` -> `guide` is that the SECOND half
       is often a real seat name, and this is the check that makes that a fact
       rather than a hopeful string operation. `guide` is live; `alpaca` is live;
       `bb_sat` is not, and must land in the digest instead of being DM'd into
       `no_resolution`.
    """
    global _seats_cache
    if _seats_cache is None:
        names = set()
        try:
            sess = get("/sessions")
            rows = sess if isinstance(sess, list) else sess.get("sessions", [])
            for r in rows:
                for k in ("subscriber_id", "id", "name", "seat"):
                    if r.get(k):
                        names.add(r[k])
        except Exception:
            pass
        try:
            for o in get("/reminders-owners")["owners"]:
                if o.get("owner"):
                    names.add(o["owner"])
        except Exception:
            pass
        _seats_cache = names
    return name in _seats_cache


def repo_owner_seat(owner: str, path: str = ""):
    """`projects/guide` -> `guide` when that seat is addressable, else None.

    ⛔ A CONTAINER PATH IS NOT A SEAT NAME. scan.py now resolves a finding under
       `code/forks` or `code/projects` to `<container>/<project>` instead of the
       container -- correct for naming the FILE's owner, still not an address.
       The trailing segment is frequently the seat (`guide`, `alpaca`), and
       `seat_exists` is what turns that from a guess into a route.
    """
    rule = REPO_OWNERS.get(owner)
    if isinstance(rule, str):
        return rule
    if isinstance(rule, list):
        # ⛔ A SHARED REPO HAS NO SINGLE OWNER. Match the FILE, and return None
        #    when nothing matches so the finding goes to the advisory instead of
        #    inheriting whichever seat happens to own the rest of the tree.
        for rx, seat in rule:
            if rx.search(path):
                return seat
        return None
    if "/" not in owner:
        return None
    tail = owner.rsplit("/", 1)[1]
    return tail if seat_exists(tail) else None


def finding_path(f):
    """The file path a finding names -- `rest` always begins `<path>:<line>`."""
    rest = (f.get("rest") or "").split()
    return rest[0].split(":")[0] if rest else ""


def owner_for_hostlocal(owner: str):
    """`host-local:~/.claude/skills/<dir>@<host>` -> (seat, host), or None.

    ⛔ THE HOST IS HALF THE FINDING AND REMAPPING THE OWNER DESTROYS IT. Once the
       group becomes `system`, nothing in the finding line says WHICH machine is
       wrong -- and these files differ per host by definition, which is the whole
       reason they are reported per host. A DM reading "docker-heal:177 cites a
       dead id" sends the seat to look at the copy in front of them, which may be
       the one that is already fixed. Return both and keep the host in the text.
    """
    m = re.search(r"/skills/([^/@]+)", owner)
    if not m:
        return None
    seat = HOSTLOCAL_OWNERS.get(m.group(1))
    if not seat:
        return None
    return seat, (owner.rpartition("@")[2] if "@" in owner else "unknown-host")


def run(script, *args):
    p = subprocess.run([sys.executable, str(HERE / script), *args],
                       capture_output=True, text=True, timeout=1800)
    return p.stdout


def parse(out, source):
    """(owner, kind, ref) from the section-grouped report."""
    found, kind = [], None
    for line in out.splitlines():
        # ⛔ Headers are "--- <group>  (<n>) ---". An earlier version tested
        #    endswith(")") and so NEVER matched, leaving kind None and parsing
        #    ZERO findings out of a report that had 16 -- a silent false all-clear
        #    from the component whose whole job is to stop findings being missed.
        if line.startswith("--- ") and line.rstrip().endswith("---"):
            kind = re.sub(r"\s*\(\d+\)\s*---$", "", line[4:].rstrip()).strip()
            continue
        # ⛔ The trailing caveat block is indented too and was being parsed as a
        #    finding ("invisible: [pin/OVER-CLIFF] here and needs a reader").
        #    Findings are indented exactly 2; the footer starts with ⛔ and wraps
        #    at 3. Stop at the footer and require the finding indent.
        if line.lstrip().startswith("⛔"):
            kind = None
            continue
        if kind and line.startswith("  ") and not line.startswith("   ") and line.strip():
            parts = line.split()
            if len(parts) >= 2:
                found.append({"source": source, "group": kind,
                              "a": parts[0], "rest": " ".join(parts[1:])})
    return found


def cliff_advice(owner):
    """The BROKER's own pruning text for this seat, fetched live -- never copied.

    ⛔ A DM that says "SHED 2469" and nothing else tells a seat the size of its
       problem and not one thing about the fix, so the seat invents one. The
       endpoint already carries authoritative guidance under `how_to_prune`
       ("DELETE, DO NOT REWRITE -- spent pointers first; rewording nets ~zero").
       Forward it instead of paraphrasing: a paraphrase in this file goes stale
       the day the broker's advice changes, and nothing would catch it.

    ⭐ TRIM `retire_when` FIRST -- it is WRAPPER, not body, so it raises your
       CEILING instead of lowering your content, and costs nothing. Measured by
       `unibrowse` 2026-10-23: three retire_when strings, wrapper 1358 -> 1087,
       max_body_chars 7925 -> 8196.
    ⚠️ It is FREE, not BIG. Same measurement: 271 chars from retire_when versus
       1,704 that still had to come out of real standing constraints. A seat
       over by thousands cannot get there this way, and saying otherwise sends
       it looking for budget that is not there.
    """
    try:
        d = get(f"/session-payload/{owner}")
    except Exception:
        return ""
    howto = (d.get("how_to_prune") or "").strip()
    if not howto:
        return ""
    return ("\n\nOVER/AT-RISK ON THE SESSION-START CLIFF. Two levers, free one first:\n"
            "  1. Trim `retire_when` on your pins -- it is WRAPPER, so it RAISES max_body_chars\n"
            "     and costs no content. Free, but bounded: ~271 chars when `unibrowse` did it.\n"
            "  2. Then the body. The broker's own guidance, verbatim:\n\n     " + howto +
            "\n\n⛔ Re-read your verdict from GET /session-payload/<you> after editing. Do not\n"
            "   compute headroom from a body figure -- the budget is body + a per-item wrapper\n"
            "   against YOUR max_body_chars, with a fleet-state reserve another seat can spend.")


# ⛔⛔ THE BROKER CAPS A SENDER AT 30 info MESSAGES PER 60s AND DROPS THE REST --
#    HTTP 200, `sent: 0`, `drop_reason: "sender_cap"`, AND `durable: true` STILL
#    IN THE RESPONSE. MEASURED 2026-10-23 10:09 on this lane's first fleet-wide
#    run: 83 DMs attempted, 30 delivered, 53 DROPPED, and the ledger recorded all
#    83 as told, so the drip would never have retried them. The dropped ones were
#    absent from the recipients' delivery ledgers
#    (`XREVRANGE atc:ledger:<seat>`), which is how I established they were not
#    merely queued.
# ⭐⭐ `durable: true` DOES NOT SURVIVE A CAP. Its own response text says what it
#    means -- "preserved across a reconnect drain for 24h" -- which is about a
#    message that was ACCEPTED. A message the gate refused never becomes durable,
#    and the field sits there next to `sent: 0` reading like a guarantee.
# ⛔ `sent` IS THE ONLY RECEIPT. A 200 is not one. ATC_SENDER_RPM (rate-limiter.ts:54)
#    is 30/60s; warning and critical bypass it, and reaching for a higher priority
#    to get past it would defeat a guard built to stop exactly this stampede.
SENDER_RPM = 28                 # 30 is the cap; leave room for my own replies
_sent_at: list[float] = []


class Unaddressable(RuntimeError):
    """The broker has no route to this name at all (`drop_reason:
    "no_resolution"`). ⭐ A DIFFERENT FINDING FROM A FAILED SEND, because no
    retry, no pacing and no patience will deliver it: the seat owns pins and
    instruction files that WILL fire when it returns, and there is nobody to
    tell. Measured 2026-10-23: 8 such owners -- `forks`, `projects`,
    `server_admin`, `omada` and four `thread-assistant-*`. All 8 are absent
    from /sessions; `assistant`, `network` and `cluster` are present.
    ⛔ So "unregistered but its pins still fire" has a consequence the pin audit
       did not state: THERE IS NO ADDRESS TO WARN ANYONE AT."""


def parent_seat(name: str) -> str | None:
    """`thread-assistant-6` -> `assistant`, when that parent actually resolves.

    ⭐ STRUCTURAL, NOT INFERRED -- the thread naming convention is the broker's
       own, not a guess about who owns what. ⛔ But it stops there: I do NOT map
       `omada` to `network` because that would be exactly the inferred ownership
       this file refuses elsewhere. An unaddressable seat with no structural
       parent goes to the digest, naming the likely owner as a possibility to
       confirm rather than a route.
    """
    m = re.match(r"^thread-(.+)-\d+$", name)
    return m.group(1) if m else None


def advisory_summary(advisory: list) -> str:
    """Counts by category, with a handful of examples -- NOT all 118 lines.

    ⛔⛔ THE DIGEST WAS SPENDING MORE THAN THE FINDINGS WERE WORTH. It lists every
       advisory line and is DM'd to me on every run; at 118 lines and two sweeps
       a day that is ~2x14k characters of my own context daily, under a
       permanent token embargo, to re-read a list that has not changed since
       yesterday. ⭐ An advisory is BY DEFINITION something nobody is going to
       act on tonight -- so it needs a COUNT and a trend, not an inventory. The
       full list is one command away (`python3 route.py --dry-run`) for anyone
       who wants it.
    ⚠️ Keeps up to 3 examples per bucket, because a count with no exemplar
       cannot be sanity-checked and silently survives a detector going wrong.
    """
    buckets: dict = {}
    for line in advisory:
        if line.startswith("UNOWNED code/"):
            key = "UNOWNED repo path (no seat of that name)"
        elif line.startswith("UNOWNED host-local:"):
            key = "UNOWNED host-local skill file"
        elif line.startswith("UNADDRESSABLE"):
            key = "UNADDRESSABLE owner (no broker route)"
        elif "[status/" in line:
            key = "status row: " + line.split("[status/", 1)[1].split("]", 1)[0]
        elif "[pin/" in line:
            key = "pin: " + line.split("[pin/", 1)[1].split("]", 1)[0]
        else:
            key = "other"
        buckets.setdefault(key, []).append(line)
    out = [f"{len(advisory)} line(s) in {len(buckets)} categor(ies). "
           f"Full list: python3 scripts/instruction-audit/route.py --dry-run"]
    for key in sorted(buckets, key=lambda k: -len(buckets[k])):
        rows = buckets[key]
        out.append(f"\n  {len(rows):>4}  {key}")
        for r in sorted(rows)[:3]:
            out.append(f"          {r[:150]}")
        if len(rows) > 3:
            out.append(f"          ... and {len(rows) - 3} more")
    return "\n".join(out)


def post(to, subject, content, attempts=3):
    """DM a seat, PACED, and verify delivery. Returns the broker's response.

    ⚠️ Raises on exhausted retries so a caller cannot record a send that did not
       happen -- the ledger write must be downstream of a real `sent >= 1`.
    """
    last = None
    for attempt in range(attempts):
        now = time.monotonic()
        _sent_at[:] = [t for t in _sent_at if now - t < 60.0]
        if len(_sent_at) >= SENDER_RPM:
            wait = 60.0 - (now - _sent_at[0]) + 0.5
            print(f"   [pacing] {len(_sent_at)} sends in the last 60s, sleeping {wait:.0f}s")
            time.sleep(max(1.0, wait))
            now = time.monotonic()
            _sent_at[:] = [t for t in _sent_at if now - t < 60.0]
        body = json.dumps({"to": to, "from": "memo", "durable": True,
                           "priority": "info", "subject": subject,
                           "content": content}).encode()
        req = urllib.request.Request(BROKER + "/messages", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            last = json.loads(r.read())
        _sent_at.append(time.monotonic())
        if (last.get("sent") or 0) >= 1:
            return last
        reason = last.get("drop_reason") or "unknown"
        if reason == "dedup":
            # Already in flight with identical text -- not a failure to repair.
            return last
        if reason == "no_resolution":
            # ⛔⛔ A SECOND DROP CAUSE, AND RETRYING IT IS PURE WASTE. The broker
            #    has NO ADDRESS for this name -- it is not in /sessions and has
            #    no route -- so three attempts at 20s each buy a guaranteed
            #    failure a minute slower. MEASURED 2026-10-23: of the 53 notices
            #    the first fleet-wide run lost, 44 were `sender_cap` and went out
            #    on the paced retry; 8 were THIS, and no pacing will ever fix
            #    them. ⚠️ I had reported all 53 as the cap, having measured the
            #    cause on exactly one of them -- one sample, generalised.
            raise Unaddressable(to)
        print(f"   [DROPPED] {to}: {reason} (attempt {attempt + 1}/{attempts})")
        time.sleep(20.0)
    raise RuntimeError(f"broker dropped the message to {to}: "
                       f"{(last or {}).get('drop_reason')} after {attempts} attempts")


def main():
    dry = "--dry-run" in sys.argv
    undelivered = []   # seats the broker refused; NOT recorded as told
    unaddressable = []  # seats with no broker route at all -- a different finding
    pins = parse(run("pins.py"), "pin")
    # ⭐ sweep.py, not scan.py: scan.py sees server4 only, and the first all-host
    #    run proved that matters -- docker-heal's fix had landed on server4 alone
    #    while three hosts still served the pre-fix text. Routing off the local
    #    scan would have reported that as clean.
    files = parse(run("sweep.py"), "file")
    # ⭐ The other half of Ben's 05:57 directive: scan.py finds a memo id that no
    #    longer resolves; this finds a volatile, dated, quantitative claim with NO
    #    memo id at all -- "should reference memo which we can better control".
    unanch = parse(run("sweep.py", "--script", "unanchored",
                       "--min-age-days", UNANCHORED_MIN_AGE_DAYS), "unanchored")
    # ⭐ The third push surface. Ben, #memo 2026-10-23 09:06: "reminders and
    #    statuses in ATC". A status is rendered into other seats' session-start
    #    payload exactly like a pin, and until now nothing watched it.
    stats = parse(run("statuses.py"), "status")

    # pins.py groups by FINDING KIND with owner in col 1; scan.py groups by OWNER.
    by_owner, advisory, dead_rows = {}, [], {}
    for f in pins:
        line = f"[pin/{f['group']}] {f['rest']}"
        # ⛔ BOTH NEW KINDS HAVE THEIR OWN LANE BELOW AND MUST NOT FALL THROUGH
        #    HERE. `unanchored-pin-claim` alone is 930 findings (measured
        #    2026-10-23); appending them to `advisory` would put 930 lines in the
        #    nightly digest, which is how a digest stops being read.
        if f["group"] in ("unanchored-pin-claim", "dm-pin-oversize",
                          "pin-cites-superseded-memo", "pin-cites-refuted-memo"):
            continue
        if f["group"] in ACTIONABLE:
            by_owner.setdefault(f["a"], []).append(line)
        else:
            advisory.append(f"{f['a']}: {line}")
    for f in files:
        owner = f["group"]
        if owner.startswith("host-local:"):
            # ⛔⛔ THESE ARE THE MOST ACTIONABLE FINDINGS AND ROUTING USED TO DROP
            #    ALL OF THEM. A host-local skill file has no owner in its path,
            #    so the only reason `system` heard about docker-heal yesterday is
            #    that I hand-delivered it. Unattended, that finding dies in a
            #    digest nobody is obliged to read.
            # ⛔ BUT AN OWNER MUST BE ESTABLISHED, NEVER INFERRED FROM THE NAME.
            #    A DM to a guessed seat is worse than none: it asks the wrong seat
            #    to edit a file it does not own, and the right seat still never
            #    hears. Only entries proven by that seat ACTING on the finding go
            #    in the map; everything else goes to the digest, where a human
            #    assigns it once and it earns a line here.
            mapped = owner_for_hostlocal(owner)
            if not mapped:
                advisory.append(f"UNOWNED {owner}: {f['a']} {f['rest']}")
                continue
            owner, host = mapped
            by_owner.setdefault(owner, []).append(
                f"[file on {host}] {f['a']} {f['rest']}")
            continue
        mapped = repo_owner_seat(owner, finding_path(f))
        if mapped:
            by_owner.setdefault(mapped, []).append(
                f"[file in code/{owner}] {f['a']} {f['rest']}")
        elif isinstance(REPO_OWNERS.get(owner), list):
            advisory.append(f"UNASSIGNED SUBTREE code/{owner}: {f['a']} {f['rest']} "
                            f"[shared repo; no rule covers this path -- ask who owns it]")
        elif "/" in owner:
            advisory.append(f"UNOWNED code/{owner}: {f['a']} {f['rest']} "
                            f"[no seat named `{owner.rsplit('/', 1)[1]}`]")
        else:
            by_owner.setdefault(owner, []).append(f"[file] {f['a']} {f['rest']}")

    for f in stats:
        owner, kind = f["group"], f["a"]
        line = f"[status/{kind}] {f['rest']}"
        # ⛔ An agent_id that does not resolve to a live subscriber has NO seat to
        #    tell -- statuses.py already refused to guess one. 89 of these belong
        #    to two RETIRED guardian seats whose July attestations are still being
        #    pushed at the fleet; that needs a decision, not a DM into the void.
        # ⛔ `retired:` ONLY. statuses.py marks an agent retired when its NEWEST
        #    row is also past the staleness window; `unresolved:` means merely
        #    unregistered, which on the cleaned board was FOUR LIVE CRON BOTS.
        #    Sending those to atc would ask it to delete a bot's current status.
        if owner.startswith("unresolved:"):
            advisory.append(f"{owner}: {line}  [unregistered but STILL POSTING -- not deletable]")
            continue
        if owner.startswith("retired:"):
            agent_id = owner.split("retired:", 1)[1]
            m = re.search(r"\b(\d+)d\b", f["rest"])
            age = int(m.group(1)) if m else None
            status_id = f["rest"].split()[0] if f["rest"].split() else "?"
            # ⚠️ An age we could not parse is NOT an old row. Unknown goes to the
            #    digest, never into a deletion queue -- the whole point of the
            #    30-day floor is that it is measured.
            if age is not None and age >= ATC_DEAD_ROW_MIN_AGE_DAYS:
                dead_rows.setdefault(status_id, (agent_id, age, kind, f["rest"]))
            else:
                advisory.append(f"{owner}: {line}")
            continue
        if kind not in STATUS_ACTIONABLE:
            advisory.append(f"{owner}: {line}")
            continue
        by_owner.setdefault(owner, []).append(line)

    # ── unanchored claims: own population, own cadence, own message ───────
    unanch_by_owner = {}
    for f in unanch:
        owner = f["group"]
        prefix = "[no memo id]"
        if owner.startswith("host-local:"):
            mapped = owner_for_hostlocal(owner)
            if not mapped:
                advisory.append(f"UNOWNED {owner}: {f['a']} {f['rest']}")
                continue
            owner, host = mapped
            prefix = f"[no memo id, on {host}]"
            # ⛔ `owner` IS NOW A SEAT, NOT A REPO PATH. Re-running the repo
            #    resolver over it would re-interpret a seat name as a directory
            #    and label the line "in code/<seat>". Inert only because no
            #    REPO_OWNERS key is also a seat name -- which is luck, not a
            #    design, and the next entry added could end it.
            unanch_by_owner.setdefault(owner, []).append(f"{prefix} {f['rest']}")
            continue
        mapped = repo_owner_seat(owner, finding_path(f))
        if mapped:
            unanch_by_owner.setdefault(mapped, []).append(
                f"{prefix} in code/{owner}: {f['rest']}")
            continue
        if isinstance(REPO_OWNERS.get(owner), list):
            advisory.append(f"UNASSIGNED SUBTREE code/{owner}: {f['rest']} "
                            f"[shared repo; no rule covers this path -- ask who owns it]")
            continue
        if "/" in owner:
            advisory.append(f"UNOWNED code/{owner}: {f['rest']} "
                            f"[no seat named `{owner.rsplit('/', 1)[1]}`]")
            continue
        unanch_by_owner.setdefault(owner, []).append(f"{prefix} {f['rest']}")

    try:
        seen = set(tuple(x) for x in json.loads(STATE.read_text()))
    except Exception:
        seen = set()

    new = {o: [l for l in ls if (o, l) not in seen] for o, ls in by_owner.items()}
    new = {o: ls for o, ls in new.items() if ls}

    if len(new) > MAX_SEATS:
        print(f"⛔ {len(new)} seats have NEW findings, over MAX_SEATS={MAX_SEATS}. "
              f"Not DMing the fleet. Telling memo instead.")
        if not dry:
            post("memo", "instruction audit: unusual finding fan-out",
                 f"{len(new)} seats have new instruction-surface findings, over the "
                 f"{MAX_SEATS} cap. Not routed automatically. Owners:\n  "
                 + ", ".join(sorted(new)))
        return 0

    for owner, lines in sorted(new.items()):
        msg = ("Instruction-surface audit found claims in files or pins you own that "
               "no longer resolve:\n\n  " + "\n  ".join(lines) +
               "\n\nA 410 keeps its title, so the meaning is recoverable and the line can be "
               "rewritten. A 404 keeps nothing -- the surrounding prose is the last record, so "
               "capture it into a new memo BEFORE editing the line.\n\n"
               "Ben authorised this in Slack #memo 2026-10-23: volatile claims in skill/CLAUDE.md "
               "files and pins cite a memo id, the owning seat makes the edit, memo detects and "
               "reverifies nightly. I do not edit other seats' files.\n\n"
               "Reported once. You will not be re-sent this unless it changes.")
        if any("OVER-CLIFF" in l or "at-risk-cliff" in l for l in lines):
            msg += cliff_advice(owner)
        if dry:
            print(f"--- would DM {owner} ---\n{msg}\n")
        else:
            try:
                r = post(owner, "instruction-surface findings in files/pins you own", msg)
            except Unaddressable:
                unaddressable.append(owner)
                print(f"⛔ UNADDRESSABLE {owner}: no broker route")
                continue
            except RuntimeError as e:
                print(f"⛔ NOT SENT {owner}: {e}")
                undelivered.append(owner)
                continue
            print(f"DM {owner}: sent={r.get('sent')} live={r.get('live')}")
        for l in lines:
            seen.add((owner, l))

    # ── drip the unanchored backlog, oldest claim first ───────────────────
    def oldest(lines):
        """Age of this seat's rottenest claim. ⚠️ 0, not -1, when no age parses:
        a line we cannot date must not sort ABOVE a measured 326-day claim."""
        ages = [int(m.group(1)) for l in lines for m in [re.search(r"\b(\d+)d\b", l)] if m]
        return max(ages) if ages else 0

    unanch_new = {o: [l for l in ls if (o, l) not in seen]
                  for o, ls in unanch_by_owner.items()}
    unanch_new = {o: ls for o, ls in unanch_new.items() if ls}
    picked = sorted(unanch_new, key=lambda o: -oldest(unanch_new[o]))[:UNANCHORED_SEATS_PER_NIGHT]
    print(f"\nunanchored: {sum(len(v) for v in unanch_by_owner.values())} claims across "
          f"{len(unanch_by_owner)} owner(s); {len(unanch_new)} owner(s) not yet told; "
          f"DMing {len(picked)} tonight (oldest first)")

    for owner in picked:
        lines = unanch_new[owner]
        lines.sort(key=lambda l: -(int(m.group(1)) if (m := re.search(r"\b(\d+)d\b", l)) else 0))
        shown, extra = lines[:UNANCHORED_MAX_LINES], max(0, len(lines) - UNANCHORED_MAX_LINES)
        skillfile = any(SKILLFILE.search(l) for l in lines)
        msg = ("Instruction files you own carry dated, quantitative claims with NO memo id "
               "-- so nothing can reverify them and nobody can correct them centrally. "
               "Oldest first:\n\n  " + "\n  ".join(shown)
               + (f"\n  ... and {extra} more in the same files." if extra else "")
               + "\n\nThe fix is not to delete the claim. Put the measurement in a memo and "
               "cite the id in the line, so a future reader can check whether it still holds "
               "-- a DATE alone does not make anyone recheck it. Measured 2026-10-23: the "
               "stale Massive live-feed block carried its date in plain sight and `alpaca` "
               "built a fill pipeline on it anyway, a month after the bug was fixed.\n\n"
               "Ben's directive, Slack #memo 2026-10-23 05:57: files anyone relies on should "
               "reference memo, which we can control and reverify. I do not edit other seats' "
               "files.\n\n"
               + ("⛔ AND THESE ARE SKILL/CLAUDE.md FILES, WHICH YOU MAY NOT EDIT WITHOUT BEN'S "
                  "EXPLICIT AUTHORIZATION (his CLAUDE.md: propose the change, do not make it). "
                  "So the deliverable here is ONE PROPOSAL TO HIM, not an edit -- fold this list "
                  "into it. ⭐ I will NOT re-ask while that is pending: reply "
                  "`awaiting-operator` and I suppress these lines until he answers. Nagging you "
                  "about a file you are forbidden to touch would be an alarm that fires on "
                  "correct behaviour.\n\n" if skillfile else "")
               + "Reported once. Only NEW claims will be sent again.")
        if dry:
            print(f"--- would DM {owner} (unanchored) ---\n{msg}\n")
        else:
            try:
                r = post(owner, "dated claims in your instruction files carry no memo id", msg)
            except Unaddressable:
                unaddressable.append(owner)
                print(f"⛔ UNADDRESSABLE {owner}: no broker route")
                continue
            except RuntimeError as e:
                print(f"⛔ NOT SENT {owner}: {e}")
                undelivered.append(owner)
                continue
            print(f"DM {owner}: sent={r.get('sent')} live={r.get('live')}")
        for l in lines:
            seen.add((owner, l))

    # ── a pin citing a memo that has MOVED ON -> its holder, once ──────────
    # ⭐ BEN, #memo 2026-10-23 10:15: "if those things change we really need them
    #   to reference the new memo and keep the pins healthy." Once, not daily:
    #   the fix is a one-time repoint, and the finding cannot recur for the same
    #   pair -- once the line cites the heir, the old id is gone from the body.
    for f in pins:
        if f["group"] not in ("pin-cites-superseded-memo", "pin-cites-refuted-memo"):
            continue
        owner = f["a"]
        if (owner, f["rest"]) in seen:
            continue
        refuted = f["group"] == "pin-cites-refuted-memo"
        msg = (("A memo your pin cites has been DISPROVED by a probe, so the pin is handing "
                "you a known-wrong fact every session:\n\n  " if refuted else
                "A memo your pin cites has been SUPERSEDED, and the pin still points at the "
                "old one:\n\n  ") + f["rest"] + "\n\n"
               "⭐ The citation still RESOLVES, which is why no other check catches this: a 200 "
               "on the id says nothing about whether what it returns still supports your line.\n\n"
               "⇒ Read the heir and decide. Three outcomes, and CITING BOTH IS OFTEN RIGHT:\n"
               "  · a replacement -- cite the heir instead;\n"
               "  · a CONTINUATION, where the heir is PART 2 because the original filled up -- "
               "cite BOTH, since the original still holds everything it said;\n"
               "  · a narrowing -- your line may need rewording, not just a new id.\n"
               "⚠️ `atc` hit the middle case within the hour (2bf49e8c -> 1e9ff47f PART 2) and "
               "cited both, which is the right answer. The corpus does mark the original "
               "superseded with `valid_until` set, so the flag is reading the corpus correctly "
               "-- it just cannot tell a continuation from a replacement, and does not try.\n\n"
               "⛔ I did not compare the two texts and I am not telling you the line is wrong. "
               "The finding is only that the thing you cite has been retargeted by its own "
               "author.\n\n"
               "Reported once, not nightly -- once the line cites the heir, the old id is no "
               "longer in your pin and this cannot recur for the same pair.")
        if dry:
            print(f"--- would DM {owner} (cited memo moved) ---\n{msg}\n")
        else:
            try:
                r = post(owner, "a memo your pin cites has been superseded", msg)
                print(f"DM {owner} (cited memo moved): sent={r.get('sent')}")
            except Unaddressable:
                unaddressable.append(owner)
                continue
            except RuntimeError as e:
                print(f"⛔ NOT SENT {owner}: {e}")
                undelivered.append(owner)
                continue
        seen.add((owner, f["rest"]))

    # ── oversize pins -> their owner, once (size is a design decision) ──────
    # ⛔ NOT a daily nag. A 712,925-char record is either a mistake the owner
    #    fixes once or a deliberate design they defend once (dojo's pin is
    #    deliberately over the limit, by agreement). Re-asking daily about a
    #    documented exemption is the alarm-on-correct-behaviour failure, and the
    #    recipient learns to filter me.
    for f in pins:
        if f["group"] != "dm-pin-oversize":
            continue
        owner, pid = f["a"], f["rest"].split()[0] if f["rest"].split() else "?"
        if (owner, f["rest"]) in seen:
            continue
        msg = ("One of your ATC pins is large enough that it is a LOG, not standing "
               f"context:\n\n  {f['rest']}\n\n"
               "Two things you may not know, both measured on the broker today:\n"
               "  1. The 10,000-char session-start cliff guard watches `as:\"inline\"` ONLY. "
               "`as:\"dm\"` records are deliberately excluded from that sum "
               "(reminders.ts:5562), so nothing refuses growth on this carrier.\n"
               "  2. Since 2026-10-11 an `as:\"dm\"` record with a compact/startup trigger "
               "FIRES AT SESSION START (reminders.ts:4563) -- before that only a cadence "
               "partner delivered it. Nothing in reminders.ts truncates a body (:1879), so "
               "the whole thing is delivered on every compaction and every start.\n\n"
               "Fleet-wide: `as:\"dm\"` pins now hold 1,157,982 chars across 107 records "
               "against 606,623 across 223 inline ones -- the unguarded carrier is the "
               "bigger one.\n\n"
               "If this size is DELIBERATE, say so and I will record the exemption on both "
               "sides and stop flagging it -- an agreement recorded on one side is invisible "
               "to the side that forgot. If it is not, the content that must survive a "
               "compaction belongs in memo with a pointer in the pin; memo is pulled on "
               "demand and costs nothing until it is read.\n\n"
               "Reported once, not nightly.")
        if dry:
            print(f"--- would DM {owner} (oversize pin {pid}) ---\n{msg}\n")
        else:
            try:
                r = post(owner, "an ATC pin you own is a log, not standing context", msg)
            except Unaddressable:
                unaddressable.append(owner)
                print(f"⛔ UNADDRESSABLE {owner}: no broker route")
                continue
            except RuntimeError as e:
                print(f"⛔ NOT SENT {owner}: {e}")
                undelivered.append(owner)
                continue
            print(f"DM {owner}: sent={r.get('sent')} live={r.get('live')}")
        seen.add((owner, f["rest"]))

    # ── pin owners that never consumed a pin -> `atc`, as CANDIDATES ───────
    # ⛔ A CANDIDATE, NOT A DELETION, AND THE DISTINCTION IS THE WHOLE POINT. The
    #    same measurement found 8 owners with NO subscriber record that are LIVE
    #    (`omada` delivered 0 days ago) -- so the registry cannot decide this and
    #    `last_injected_at` can. Deleting another seat's pins is not mine to do.
    for f in pins:
        if f["group"] != "pin-owner-never-delivered":
            continue
        if (ATC_SEAT, f["a"]) in seen:
            continue
        msg = (f"RETIRED-PIN-OWNER CANDIDATE: `{f['a']}` -- {f['rest']}\n\n"
               "Evidence, and it is not the registry: `last_injected_at` is null and "
               "`inject_count` is 0 on every pin this owner holds, and the oldest was created "
               "well past the window. A pin is only ever delivered when its owner starts a "
               "session or compacts, so a pin that has NEVER been delivered has never had an "
               "owner to read it.\n\n"
               "⛔ This corrects the premise we both recorded this morning -- that the broker "
               "cannot tell a retired seat from a live one under another name. It can, on "
               "DELIVERY rather than registration. Measured 2026-10-23 across the 12 owners "
               "with no subscriber record: 8 of them have been delivered a pin recently "
               "(`omada` 0 days ago, inject_count 5; `vbt` 3d, n=10; `thread-quantum-lab-7` "
               "3d, n=3), so they are live under names /sessions does not carry. Only these "
               "never-delivered ones are candidates.\n\n"
               "⚠️ Per-OWNER, max over their pins -- never per pin. `omada`'s `as:\"dm\"` "
               "record has inject_count 0 because its `context` trigger never fired, while "
               "its inline pin was delivered today. Judged per pin, a live seat condemns "
               "itself.\n\n"
               "Your call and Ben's, not mine: I do not delete another seat's pins.")
        if dry:
            print(f"--- would DM atc (retired candidate {f['a']}) ---\n{msg}\n")
        else:
            try:
                r = post(ATC_SEAT, f"retired-pin-owner candidate: {f['a']}", msg)
            except RuntimeError as e:
                print(f"⛔ NOT SENT atc ({f['a']}): {e}")
                undelivered.append(ATC_SEAT)
                continue
            print(f"DM atc ({f['a']}): sent={r.get('sent')} live={r.get('live')}")
        seen.add((ATC_SEAT, f["a"]))

    # ── pins: claims with no memo id, re-asked DAILY until the line is gone ──
    pin_claims = {}
    for f in pins:
        if f["group"] == "unanchored-pin-claim":
            pin_claims.setdefault(f["a"], []).append(f["rest"])

    try:
        nag = json.loads(PIN_STATE.read_text())
    except Exception:
        nag = {}
    today = datetime.now().date().isoformat()

    # ⭐ CLEARED = THE LINE IS NO LONGER FLAGGED. ⛔ NOT "the seat fixed it":
    #    a line also leaves this set if the seat DELETED it, retired the whole
    #    pin, or if I changed the detector. Two of those three are not progress,
    #    and calling them a fix would manufacture a success rate. Report what was
    #    measured -- it stopped being flagged -- and let the number mean that.
    exempt = load_exempt()
    n_exempt = 0
    for owner in list(pin_claims):
        ex = {e["claim"] for e in exempt.get(owner, [])}
        if not ex:
            continue
        before = len(pin_claims[owner])
        pin_claims[owner] = [l for l in pin_claims[owner] if claim_key(l) not in ex]
        n_exempt += before - len(pin_claims[owner])
        if not pin_claims[owner]:
            del pin_claims[owner]
    if n_exempt:
        print(f"pin claims: {n_exempt} line(s) exempted by their holder (pin-exempt.json)")

    # ⛔⛔ READ "WHEN DID I LAST ASK THIS SEAT" BEFORE ANY LINE IS CLEARED, AND
    #    KEEP IT SEPARATELY FROM THE LINES. The per-line records are deleted as
    #    soon as a holder fixes a line, and an owner whose records all cleared is
    #    dropped from the ledger entirely -- so a seat that fixed one line and
    #    still has another came back as FIRST CONTACT and got a second DM the
    #    same morning, ten minutes after the first. Measured on the dry-run:
    #    `garden`, `network`, `nginx`, `omada`, `pricetracker` and two threads.
    # ⇒ The asked-date belongs to the OWNER, not to the line, because what it
    #   protects is the seat's attention, not the claim's bookkeeping.
    asked = nag.pop("_asked", {}) if isinstance(nag.get("_asked"), dict) else {}
    for owner, lines in nag.items():
        for v in lines.values():
            if v.get("last") and v["last"] > asked.get(owner, ""):
                asked[owner] = v["last"]

    cleared = []
    for owner, lines in list(nag.items()):
        live = {claim_key(l) for l in pin_claims.get(owner, [])}
        for key in list(lines):
            if key not in live:
                cleared.append((owner, lines[key].get("n", 0)))
                del lines[key]
        if not lines:
            del nag[owner]

    # who to ask tonight: REPEATS FIRST -- they are the ones Ben wants pressed.
    # ⛔⛔ SEVERAL SWEEPS A DAY, BUT ONE ASK A DAY. With a morning and a night
    #    sweep, counting asks per RUN would reach the 3-ask escalation in a day
    #    and a half and name a seat to Ben who has had one working day to
    #    respond. A seat already asked today is skipped, and `n` advances once
    #    per calendar day -- so the second sweep's job is to catch NEW scar
    #    tissue and to clear what was fixed since breakfast, not to nag twice.
    asked_today = {o for o, d in asked.items() if d == today}
    repeats = [o for o in nag if o != "_asked" and o not in asked_today
               and any(k in {claim_key(l) for l in pin_claims.get(o, [])} for k in nag[o])]
    repeats.sort(key=lambda o: -max(nag[o][k].get("n", 0) for k in nag[o]))
    fresh = sorted((o for o in pin_claims if o not in nag and o not in asked_today),
                   key=lambda o: -len(pin_claims[o]))[:PIN_FIRST_CONTACT_SEATS]
    picked_pins = (repeats + fresh)[:PIN_NAG_SEATS_MAX]

    overdue = [(o, max(nag[o][k].get("n", 0) for k in nag[o])) for o in nag
               if o != "_asked" and nag[o]
               and max(nag[o][k].get("n", 0) for k in nag[o]) >= PIN_ESCALATE_DAYS]
    print(f"\npins/claims: {sum(len(v) for v in pin_claims.values())} unanchored claims "
          f"across {len(pin_claims)} owner(s); {len(cleared)} line(s) no longer flagged; "
          f"asking {len(picked_pins)} tonight ({len(repeats)} repeat, {len(fresh)} new); "
          f"{len(overdue)} seat(s) past {PIN_ESCALATE_DAYS} asks")

    for owner in picked_pins:
        lines = pin_claims.get(owner, [])
        if not lines:
            continue
        # ⛔ THE AGE IS THE SECOND TOKEN -- `<pin-id8>  <age>d  <text>`. `^(\d+)d`
        #    matched nothing at all, so every line sorted as 0 and "oldest first"
        #    was the pin's own line order wearing a label. Anchor past the id.
        lines = sorted(lines, key=lambda l: -(int(m.group(1))
                                              if (m := re.match(r"\S+\s+(\d+)d\b", l)) else 0))
        shown, extra = lines[:PIN_MAX_LINES], max(0, len(lines) - PIN_MAX_LINES)
        prior = max((nag.get(owner, {}).get(claim_key(l), {}).get("n", 0) for l in lines),
                    default=0)
        again = (f"\n\n⛔ THIS IS ASK #{prior + 1}. The lines above were in your pin when I "
                 f"last checked and are still there. I re-check every night and I cannot edit "
                 f"your pin -- only you can." if prior else "")
        msg = ("⛔ YOUR STANDING PIN NEEDS REVISING -- it carries claims nothing can reverify.\n\n"
               "Each line below says of itself that it was MEASURED, VERIFIED, CONFIRMED or "
               "OBSERVED -- and cites no memo id, so it cannot be corrected where it was "
               "learned. (Prohibitions, operator rulings and pin headers are deliberately "
               "NOT flagged; only self-declared observations are.) Your pin "
               "is spliced in AHEAD of your first turn, so a line that has gone false is read as "
               "established fact by a you with no memory to question it. Oldest first:\n\n  "
               + "\n  ".join(shown)
               + (f"\n  ... and {extra} more lines in the same pin." if extra else "")
               + again
               + "\n\nWHAT TO DO with each line -- one of three, and the third is fine:\n"
                 "  1. STILL TRUE: put the measurement in a memo (`/memorize`) and cite the id in "
                 "the line. Then a correction lands in ONE place for the whole fleet.\n"
                 "  2. NO LONGER TRUE: delete it. ⭐ A correction that does not RETIRE what it "
                 "corrects is a second claim, not a fix -- `scripts` holds both 'TAKING server5 "
                 "%61, do not converge' and its own later 'RELEASED, no action needed', 55 days "
                 "side by side with no ordering cue a reader is obliged to notice.\n"
                 "  3. NOT A CLAIM: a prohibition or a pointer needs no id. Say so and I will "
                 "stop flagging that line.\n\n"
               "Ben, Slack #memo 2026-10-23 09:50: seats \"build up these pins and start building "
               "up scar tissue around different stuff they think happened or happened once and "
               "it's been fixed\" -- and 09:55, that this must be \"brought up to the holder and "
               "they're told that it needs to be revised\". So: daily, until the line is gone. "
               f"After {PIN_ESCALATE_DAYS} asks I name the seat to him, which is the only "
               "escalation I have -- I do not edit another seat's pin.")
        if dry:
            print(f"--- would DM {owner} (pin claims, ask #{prior + 1}) ---\n{msg}\n")
        else:
            try:
                r = post(owner, "your standing pin needs revising -- claims with no memo id", msg)
            except Unaddressable:
                unaddressable.append(owner)
                print(f"⛔ UNADDRESSABLE {owner}: no broker route -- its pin still fires")
                continue
            except RuntimeError as e:
                # ⛔ NOT RECORDED. An unrecorded seat is re-asked on the next
                #    sweep; a recorded-but-undelivered one never hears at all.
                print(f"⛔ NOT SENT {owner}: {e}")
                undelivered.append(owner)
                continue
            print(f"DM {owner}: sent={r.get('sent')} live={r.get('live')}")
        asked[owner] = today
        for l in lines:
            e = nag.setdefault(owner, {}).setdefault(claim_key(l), {"first": today, "n": 0})
            # ⚠️ Count ASKS, not days. A night the cycle did not run is not an
            #    ignored ask, and counting dates would escalate a seat for my
            #    own downtime.
            if l in shown and e.get("last") != today:
                e["n"] = e.get("n", 0) + 1
            e["last"] = today

    if not dry:
        PIN_STATE.parent.mkdir(parents=True, exist_ok=True)
        out = dict(nag)
        out["_asked"] = asked      # ⚠️ survives the clearing of every line
        PIN_STATE.write_text(json.dumps(out, indent=1, sort_keys=True))

    # ── dead status rows -> `atc`, who owns the board and does the deleting ──
    dead_new = {sid: v for sid, v in dead_rows.items()
                if (ATC_SEAT, sid) not in seen}
    print(f"\nstatus rows from unreachable owners >= {ATC_DEAD_ROW_MIN_AGE_DAYS}d: "
          f"{len(dead_rows)} found, {len(dead_new)} not yet sent to {ATC_SEAT}")
    if dead_new:
        rows = "\n  ".join(
            f"{sid}  {agent_id}  {age}d  {kind}"
            for sid, (agent_id, age, kind, _) in sorted(dead_new.items(), key=lambda kv: -kv[1][1]))
        msg = (f"Dead status rows, nightly audit. Each line is `status_id  agent_id  age  reason`. "
               f"All {len(dead_new)} have an agent_id that resolves to NO live subscriber in "
               f"/sessions or /reminders-owners, and all are >= {ATC_DEAD_ROW_MIN_AGE_DAYS} days "
               f"old -- the two conditions you said you would delete on without asking Ben again "
               f"(your DM 2026-10-23; Ben #atc ts 1792762352).\n\n  {rows}\n\n"
               f"⚠️ Rows belonging to LIVE seats are NOT in this list -- those go to their owner, "
               f"not to you. ⚠️ An age I could not parse is not treated as old; it goes to the "
               f"digest instead of to you.")
        if dry:
            print(f"--- would DM {ATC_SEAT} (dead rows) ---\n{msg}\n")
        else:
            r = post(ATC_SEAT, "dead status rows: unreachable owner, over 30 days", msg)
            print(f"DM {ATC_SEAT}: sent={r.get('sent')} live={r.get('live')}")
        for sid in dead_new:
            seen.add((ATC_SEAT, sid))

    if not dry:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(sorted(seen)))
    # ⭐ The digest carries the FULL current set, not just the new ones -- it is
    #    the copy that can notice a finding nobody acted on, which the once-only
    #    per-seat DM is structurally blind to.
    digest = (f"instruction surface, {len(by_owner)} seat(s) with defects, "
              f"{len(advisory)} advisory, "
              f"{sum(len(v) for v in unanch_by_owner.values())} unanchored claims "
              f"across {len(unanch_by_owner)} owner(s) "
              f"({len(unanch_new)} not yet told, {len(picked)} DMed tonight)."
              + (f"\n\n⛔ PIN HOLDERS ASKED {PIN_ESCALATE_DAYS}+ TIMES, LINE STILL THERE "
                 f"-- Ben asked to be told: "
                 + ", ".join(f"{o} (x{n})" for o, n in sorted(overdue, key=lambda x: -x[1]))
                 if overdue else "")
              + f"\n\nPIN CLAIMS: {sum(len(v) for v in pin_claims.values())} unanchored across "
                f"{len(pin_claims)} owner(s); {len(cleared)} line(s) no longer flagged today."
              f"\n\nDEFECTS (routed):\n  "
              + ("\n  ".join(f"{o}: {'; '.join(ls)}" for o, ls in sorted(by_owner.items())) or "none")
              + "\n\nADVISORY (not routed) -- " + advisory_summary(advisory))
    if dry:
        print("--- digest to memo ---\n" + digest)
    else:
        post("memo", "instruction-surface audit digest", digest)
    print(f"routed {len(new)} seat(s); {sum(len(v) for v in by_owner.values())} defect(s), "
          f"{len(advisory)} advisory")
    if unaddressable:
        print(f"⛔ {len(unaddressable)} owner(s) have NO BROKER ROUTE: "
              f"{sorted(set(unaddressable))}")
        for owner in sorted(set(unaddressable)):
            par = parent_seat(owner)
            note = (f"`{owner}` holds pins or instruction files with findings and the broker "
                    f"has no route to it (drop_reason no_resolution; absent from /sessions). "
                    f"Its pins are NOT retired -- they fire if that seat returns.")
            if par:
                msg = (note + f"\n\nComing to you because `{owner}` is structurally your "
                       f"thread. If you can reach it or speak for it, the findings are in "
                       f"tonight's digest; if it is finished, its pins can be retired.\n\n"
                       f"⛔ I do not edit or delete another seat's pins.")
                if dry:
                    print(f"--- would DM {par} (unaddressable child {owner}) ---\n{msg}\n")
                elif (par, f"unaddressable:{owner}") not in seen:
                    try:
                        r = post(par, f"your thread `{owner}` has pin findings and no address", msg)
                        print(f"DM {par} (for {owner}): sent={r.get('sent')}")
                        seen.add((par, f"unaddressable:{owner}"))
                    except (RuntimeError, Unaddressable) as e:
                        print(f"⛔ NOT SENT {par}: {e}")
            else:
                advisory.append(f"UNADDRESSABLE {owner}: {note} No structural parent -- "
                                f"needs an owner assigned, or its pins retired.")
    if undelivered:
        # ⛔ LOUD, AND LAST. 53 silent drops on this lane's first run read as 53
        #    delivered notices for 20 minutes.
        print(f"⛔ {len(undelivered)} DM(s) THE BROKER REFUSED and NOT recorded as told "
              f"(they are re-asked on the next sweep): {sorted(set(undelivered))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
