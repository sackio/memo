#!/usr/bin/env python3
"""Archive Slack channel history to disk so a SCRIPT, not an LLM, can mine it.

WHY THIS EXISTS (2026-10-14, memo seat, under Ben's #agents directive 07:21 EDT
"use these credits more aggressively to maintain and improve memo, using the slack
conversations as a guide and verifying memo facts"):

  Claude transcripts are archived hourly to /mnt/backup/claude-logs/<host>/ with no
  --delete, so any seat can grep a month of history for free. SLACK WAS NOT ARCHIVED
  ANYWHERE. The atc bridge log (/tmp/atc-slack-bridge.log) records only ts/channel/user
  -- no message text -- so the ONLY way to read a past Slack conversation was to pull it
  through the API into an LLM context, one channel at a time. That makes Slack the one
  knowledge source the fleet cannot mine cheaply, and it has no local copy at all.

CREDENTIALS: read at runtime from /home/ben/.config/atc-slack-bridge/env (mode 0600,
HOST-LOCAL to server4). The value is never printed, logged or written to the archive.
Uses SLACK_BOT_TOKEN: measured 2026-10-14, it is the only one of the two with
channels:read + channels:history. SLACK_USER_TOKEN returns missing_scope for both.
COVERAGE LIMIT, stated because it cannot be inferred from the output: the bot token
sees only channels the bot has been invited to, so a channel absent from this archive
is NOT SEARCHED, not empty.

Stdlib only -- nothing is installed on the host.
"""
import json, os, sys, time, urllib.request, urllib.error, urllib.parse, argparse, re

ENV_PATH = "/home/ben/.config/atc-slack-bridge/env"
OUT_ROOT = "/mnt/nas/data/slack-archive"
API = "https://slack.com/api/"


def load_token():
    """Return (token, which). Never returns or logs the value anywhere else."""
    if not os.path.exists(ENV_PATH):
        sys.exit(f"no env file at {ENV_PATH}")
    vals = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    # MEASURED 2026-10-14 on server4: SLACK_BOT_TOKEN has channels:read and
    # channels:history; SLACK_USER_TOKEN returns missing_scope for BOTH and carries
    # only search:read (which is why atc's slack_search can use the native engine
    # while this archiver cannot use that token at all). So the bot token is the
    # one that works, and its coverage limit -- only channels the bot is a member
    # of -- is a real blind spot that this script reports rather than hides.
    for name in ("SLACK_BOT_TOKEN", "SLACK_USER_TOKEN"):
        if vals.get(name):
            return vals[name], name
    sys.exit("no SLACK_BOT_TOKEN or SLACK_USER_TOKEN in env file")


def call(token, method, **params):
    """One Slack API call, with 429 handling. Raises on a Slack-level error."""
    url = API + method + "?" + urllib.parse.urlencode(params)
    for attempt in range(6):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "5")) + 1
                time.sleep(wait)
                continue
            raise
        if body.get("ok"):
            return body
        err = body.get("error", "")
        if err == "ratelimited":
            time.sleep(int(body.get("retry_after", 5)) + 1)
            continue
        # not_in_channel / channel_not_found are expected for some channels
        raise RuntimeError(f"{method}: {err}")
    raise RuntimeError(f"{method}: gave up after rate limiting")


def list_channels(token):
    out, cursor = [], ""
    while True:
        b = call(token, "conversations.list", limit=200, cursor=cursor,
                 types="public_channel,private_channel", exclude_archived="true")
        out.extend(b.get("channels") or [])
        cursor = (b.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            return out
        time.sleep(1.2)


def fetch_history(token, cid, oldest):
    msgs, cursor = [], ""
    while True:
        kw = dict(channel=cid, limit=200, cursor=cursor)
        if oldest:
            kw["oldest"] = oldest
        b = call(token, "conversations.history", **kw)
        msgs.extend(b.get("messages") or [])
        if not b.get("has_more"):
            return msgs
        cursor = (b.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            return msgs
        time.sleep(1.2)


def fetch_replies(token, cid, ts):
    b = call(token, "conversations.replies", channel=cid, ts=ts, limit=200)
    # index 0 is the parent, already captured by history
    return (b.get("messages") or [])[1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=30.0,
                    help="how far back on a FIRST archive of a channel (default 30)")
    ap.add_argument("--channels", default="",
                    help="comma-separated names to restrict to (default: all)")
    ap.add_argument("--no-threads", action="store_true", help="skip thread replies")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    token, which = load_token()
    print(f"token: {which} (value not shown)")

    os.makedirs(OUT_ROOT, exist_ok=True)
    state_path = os.path.join(OUT_ROOT, "_state.json")
    state = {}
    if os.path.exists(state_path):
        state = json.load(open(state_path))

    want = {c.strip() for c in a.channels.split(",") if c.strip()}
    chans = list_channels(token)
    if want:
        chans = [c for c in chans if c["name"] in want or c["id"] in want]
    print(f"channels: {len(chans)}")

    floor = time.time() - a.days * 86400
    totals = {"channels": 0, "messages": 0, "replies": 0, "skipped": 0, "errors": 0}

    for c in sorted(chans, key=lambda x: x["name"]):
        cid, name = c["id"], c["name"]
        last = state.get(cid, {}).get("last_ts")
        oldest = last if last else f"{floor:.6f}"
        try:
            msgs = fetch_history(token, cid, oldest)
        except RuntimeError as e:
            # not_in_channel etc. -- report, never silently count as empty
            print(f"  {name:28s} SKIP ({e})")
            totals["skipped"] += 1
            continue
        except Exception as e:
            print(f"  {name:28s} ERROR {e}")
            totals["errors"] += 1
            continue

        replies = []
        if not a.no_threads:
            for m in msgs:
                if m.get("reply_count"):
                    try:
                        replies.extend(fetch_replies(token, cid, m["ts"]))
                        time.sleep(1.2)
                    except Exception as e:
                        print(f"  {name:28s} thread {m['ts']} ERROR {e}")
                        totals["errors"] += 1

        allm = msgs + replies
        if not allm:
            print(f"  {name:28s} 0 new")
            totals["channels"] += 1
            continue

        newest = max(float(m["ts"]) for m in allm)
        if not a.dry_run:
            d = os.path.join(OUT_ROOT, name)
            os.makedirs(d, exist_ok=True)
            # one file per month of the message's own time, append-only
            by_month = {}
            for m in allm:
                mo = time.strftime("%Y-%m", time.localtime(float(m["ts"])))
                m["_channel"] = name
                m["_channel_id"] = cid
                by_month.setdefault(mo, []).append(m)
            for mo, rows in by_month.items():
                with open(os.path.join(d, f"{mo}.ndjson"), "a") as f:
                    for m in sorted(rows, key=lambda x: float(x["ts"])):
                        f.write(json.dumps(m, ensure_ascii=False) + "\n")
            state[cid] = {"name": name, "last_ts": f"{newest:.6f}",
                          "archived_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

        print(f"  {name:28s} {len(msgs):5d} msgs + {len(replies):4d} replies")
        totals["channels"] += 1
        totals["messages"] += len(msgs)
        totals["replies"] += len(replies)
        time.sleep(1.2)

    if not a.dry_run:
        json.dump(state, open(state_path, "w"), indent=1, sort_keys=True)
    print(json.dumps(totals))
    # the denominator, stated in both directions
    print(f"DENOMINATOR: {totals['channels']} channels archived, "
          f"{totals['skipped']} skipped (not a zero -- NOT SEARCHED), "
          f"{totals['errors']} errors")


if __name__ == "__main__":
    main()
