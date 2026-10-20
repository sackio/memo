import json, re, subprocess, sys, urllib.request

MEMO = "http://192.168.1.168:8000"
LINE = re.compile(
    r'^(?:(?P<ts>\S+)\s+)?INFO: +(?P<ip>[0-9.]+):(?P<port>\d+) - '
    r'"(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+" (?P<code>\d{3})')
FULL_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
SHORT_ID  = re.compile(r'^[0-9a-f]{8}$')


def parse_ts(tsv):
    import datetime, re as _re
    v = tsv.replace("Z", "+00:00")
    # trim fractional seconds to 6 digits (docker emits 9)
    v = _re.sub(r"\.(\d{6})\d+", r".\1", v)
    try:
        return datetime.datetime.fromisoformat(v)
    except ValueError:
        return None

def fetch_served():
    # Returns (set_of_first_segments, ok). ok False => rule MUST be skipped, loudly.
    try:
        with urllib.request.urlopen(MEMO + "/openapi.json", timeout=10) as r:
            spec = json.loads(r.read().decode())
        segs = set()
        tmpl = set()
        for p in spec.get("paths", {}):
            parts = [x for x in p.split("/") if x]
            segs.add(parts[0] if parts else "")
            # normalise {param} segments to a wildcard so a full-path match is possible
            tmpl.add(tuple("*" if x.startswith("{") else x for x in parts))
        return (segs, tmpl), bool(segs)
    except Exception as e:
        print("  openapi FETCH FAILED (%s) -- unknown-route rule SKIPPED, not applied" % e)
        return (set(), set()), False

def path_is_served(seg, tmpl):
    # True/False only; caller must have served_ok. A served TEMPLATE must match the
    # WHOLE path, not just its first segment -- that first-segment-only test is what
    # split one caller behaviour (<served-prefix>/health) across three labels.
    for t in tmpl:
        if len(t) != len(seg):
            continue
        if all(tt == "*" or tt == ss for tt, ss in zip(t, seg)):
            return True
    return False

def doc_is_live(doc_id):
    # Ask the store. ?wp=1 marks it as our own probe so the next fire skips it.
    try:
        req = urllib.request.Request(MEMO + "/documents/" + doc_id + "?wp=1")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return None
    except Exception:
        return None

def main():
    # Optional arg: only keep lines whose leading RFC3339 timestamp is within
    # N minutes of now. Used when `docker logs --since` cannot be trusted --
    # measured 2026-09-22: on the freshly-moved NVMe daemon `--since` returns
    # ZERO BYTES while the log is current, so the window must be applied here.
    import datetime
    window_min = None
    if len(sys.argv) > 1:
        try: window_min = float(sys.argv[1])
        except ValueError: window_min = None
    cutoff = None
    if window_min:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=window_min)

    raw = sys.stdin.read().splitlines()
    events = []
    kept_by_time = 0
    unparsed_ts = [0]
    matched_lines = [0]
    newest = [None]
    oldest = [None]
    for line in raw:
        m = LINE.match(line.strip())
        if not m:
            continue
        matched_lines[0] += 1
        if cutoff is not None:
            tsv = m.groupdict().get("ts")
            if not tsv:
                continue          # no timestamp => cannot place it in the window
            t = parse_ts(tsv)
            if t is None:
                unparsed_ts[0] += 1
                continue
            if newest[0] is None or t > newest[0]:
                newest[0] = t
            if oldest[0] is None or t < oldest[0]:
                oldest[0] = t
            if t < cutoff:
                continue
            kept_by_time += 1
        d = m.groupdict()
        if "wp=1" in d["path"]:
            continue          # our own classifier probe -- never counted
        d["code"] = int(d["code"])
        events.append(d)

    codes = {}
    for e in events:
        codes[e["code"]] = codes.get(e["code"], 0) + 1
    if cutoff is not None:
        print("  window applied in-parser: last %g min (non-tail docker reads are stale)" % window_min)
        if oldest[0] is not None and newest[0] is not None:
            span = (newest[0] - oldest[0]).total_seconds() / 60.0
            if oldest[0] > cutoff:
                print("  ⚠️ PARTIAL COVERAGE: this read only goes back %.1f min (%s -> %s)."
                      % (span, oldest[0].strftime("%H:%M:%SZ"), newest[0].strftime("%H:%M:%SZ")))
                print("     The window asked for %g min. Anything before that was NOT examined."
                      % window_min)
            else:
                print("  coverage OK: read spans %.1f min (%s -> %s), window %g min"
                      % (span, oldest[0].strftime("%H:%M:%SZ"), newest[0].strftime("%H:%M:%SZ"), window_min))
        if unparsed_ts[0]:
            print("  ⛔ %d of %d access-log lines had an UNPARSEABLE timestamp and were DROPPED."
                  % (unparsed_ts[0], matched_lines[0]))
            print("     A dropped line is NOT a clean line -- this window is INCOMPLETE, not green.")
        if matched_lines[0] and not events:
            import datetime as _dt
            age = (_dt.datetime.now(_dt.timezone.utc) - newest[0]).total_seconds()/60.0 if newest[0] else None
            print("  ⛔ %d access-log lines matched but NONE fell in the %g-min window."
                  % (matched_lines[0], window_min))
            if newest[0] is not None:
                print("     Newest line in this read: %s (%.0f min old) -- the LOG IS STALE, not the service."
                      % (newest[0].isoformat(), age))
            print("     ⇒ SKIPPED, never a pass. Step 3+4 has NO evidence about this window.")
    print("codes: %s | total %d" % (dict(sorted(codes.items())), len(events)))

    ips = {}
    for e in events:
        if e["ip"] != "127.0.0.1":
            ips[e["ip"]] = ips.get(e["ip"], 0) + 1
    print("distinct remote IPs: %d %s" % (len(ips), dict(sorted(ips.items(), key=lambda x: -x[1]))))

    bad = [ (i,e) for i,e in enumerate(events) if e["code"] >= 400 ]
    if not bad:
        print(">=400: 0 | UNRECOVERED: 0 | window-edge INCONCLUSIVE: 0 | 5xx: 0")
        print("  documented-benign: {} | UNEXPLAINED-and-UNRECOVERED: 0")
        return

    (served, served_tmpl), served_ok = fetch_served()
    benign, unrecovered, inconclusive, fivexx = {}, [], [], []

    def recovered_after(idx, ip):
        for e in events[idx+1:]:
            if e["ip"] == ip and e["code"] < 400:
                return True
        return False

    def tail_of_window(idx):
        # no later traffic at all from anyone => cannot judge recovery
        return idx >= len(events) - 1

    for idx, e in bad:
        code, path, ip, method = e["code"], e["path"], e["ip"], e["method"]
        tag = None
        if code >= 500:
            fivexx.append(e); continue
        if code == 405:
            tag = "wrong-method-405"
        elif code == 413 and path.split("?")[0].startswith("/documents"):
            tag = "oversize-refusal-413-by-design"
        elif code == 404:
            seg = [x for x in path.split("?")[0].split("/") if x]
            first = seg[0] if seg else ""
            if first == "documents" and len(seg) >= 2:
                tok = seg[1]
                if not (FULL_UUID.match(tok) or SHORT_ID.match(tok)):
                    tag = "malformed-doc-id-404"
                else:
                    live = doc_is_live(tok)
                    if live is False:
                        tag = "nonexistent-doc-404"
                    elif live is True:
                        # A 404 on a LIVE document is a fault. The generic
                        # "recovered from that IP" rule must NOT excuse it --
                        # an unrelated later 200 says nothing about this miss.
                        e["why"] = "404 on a LIVE document (route is served)"
                        unrecovered.append(e)
                        continue
                    else:
                        e["why"] = "could not ask the store whether the id is live"
                        inconclusive.append(e)
                        continue
            elif served_ok and not path_is_served(seg, served_tmpl):
                tag = "unknown-route-404"
        elif code == 400 and path.split("?")[0].rstrip("/") == "/mcp":
            if recovered_after(idx, ip):
                tag = "mcp-400-reconnect"
        elif code == 422:
            if recovered_after(idx, ip):
                tag = "4xx-422-caller-retry"

        if tag:
            benign[tag] = benign.get(tag, 0) + 1
            continue
        if recovered_after(idx, ip):
            benign["unclassified-but-recovered"] = benign.get("unclassified-but-recovered", 0) + 1
        elif tail_of_window(idx):
            inconclusive.append(e)
        else:
            unrecovered.append(e)

    print(">=400: %d | UNRECOVERED: %d | window-edge INCONCLUSIVE: %d | 5xx: %d"
          % (len(bad), len(unrecovered), len(inconclusive), len(fivexx)))
    print("  documented-benign: %s | UNEXPLAINED-and-UNRECOVERED: %d"
          % (dict(sorted(benign.items())), len(unrecovered)))
    for e in fivexx:
        print("  -> 5xx FINDING %s %s %s %s" % (e.get("ts") or "", e["ip"], e["method"], e["path"]))
    for e in unrecovered:
        print("  -> %d %s %s %s %s" % (e["code"], e.get("ts") or "", e["ip"], e["method"], e["path"]))
    for e in inconclusive:
        print("  -> %d INCONCLUSIVE(window-edge) %s %s %s" % (e["code"], e["ip"], e["method"], e["path"]))

if __name__ == "__main__":
    main()
