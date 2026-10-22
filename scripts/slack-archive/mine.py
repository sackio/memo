#!/usr/bin/env python3
"""Build per-channel digests of what BEN said in Slack, for the corpus miner.

Operator instruction, Ben, Slack #memo 2026-10-22: *"I think that slack is
likely to be a very primary source for understanding what I want and what
issues have arisen and where truth needs to lie"* -- and, answering ask `a0t`,
**yes** to mining it on the daily cycle.

⭐ SCRIPT FIRST. 2,189 Ben messages in the last 7 days across 101 channels
(measured 2026-10-22). Handing that to an LLM raw is the expensive, lossy way.
This builds the digest -- window, channel set, thread context, speaker labels --
and the model is spent only on the judgement: which of these is a durable fact.

⛔ A CHANNEL ABSENT FROM THE ARCHIVE WAS NOT SEARCHED. The archiver runs on a
bot token and sees only the channels the bot is in (141 covered, measured
2026-10-22). Missing is not empty, and this script reports the two separately
rather than letting a thin result read as a quiet week.

⚠️ SPEAKER LABELS ARE LOad-BEARING. An agent's claim relayed in a channel is not
Ben's instruction, and a memo written from one as if it were the other inherits
the confidence of the relay rather than the source -- the single most common way
this fleet has recorded a wrong fact. Every line in a digest says who said it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEN = "U0NGEHS2J"
ROOT = Path("/mnt/nas/data/slack-archive")
HERE = Path(__file__).resolve().parent
CHECKPOINT = HERE / "mine-checkpoint.json"
PENDING = HERE / "mine-checkpoint-pending.json"
LEAD_IN = 4                      # messages before Ben's, so his reply has a referent


def load_channel(ch: Path, since: float) -> list[dict]:
    """Every message in this channel at or after `since`, oldest first."""
    out: list[dict] = []
    # ⚠ SKIP MONTH FILES THE WINDOW CANNOT REACH. The archive is 55 MB across
    # 153 channels; reading all of it to answer a one-day question took minutes
    # of NFS. The filename IS the index -- but compare on the month CONTAINING
    # `since`, never on equality, or a window that straddles a month boundary
    # silently loses its older half.
    first = datetime.fromtimestamp(since, timezone.utc).astimezone().strftime("%Y-%m")
    for f in sorted(ch.glob("*.ndjson")):
        if f.stem < first:
            continue
        for line in f.open(errors="replace"):
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if float(m.get("ts") or 0) >= since:
                out.append(m)
    out.sort(key=lambda m: float(m.get("ts") or 0))
    return out


def speaker(m: dict) -> str:
    if m.get("user") == BEN:
        return "BEN"
    if m.get("username"):
        return f"agent:{m['username']}"
    if m.get("bot_id"):
        return "bot"
    return f"user:{m.get('user') or '?'}"


def body_of(m: dict) -> str:
    """The message's text PLUS its attachments.

    ⛔⛔ A BARE `yes` IS UNREADABLE WITHOUT THE ASK IT ANSWERS, AND THE ASK IS
    NOT IN `text`. The Slack bridge HOISTS every `ASK:`/`NEED:` line out of the
    message body into a green attachment, so an agent's question lives in
    `attachments[].text` while `text` holds only the prose around it. A digest
    built from `text` alone shows Ben answering nothing.

    Found 2026-10-22 by the first mining worker, which hit exactly this on his
    10-21 "yes" in #alpaca and had to go back to the live thread for the
    question. It only recovered because it had tools; the digest had lost it.
    """
    parts = [m.get("text") or ""]
    for att in m.get("attachments") or []:
        t = att.get("text") or att.get("fallback") or ""
        if t:
            parts.append(f"[ASK/attachment] {t}")
    for f in m.get("files") or []:
        parts.append(f"[file uploaded: {f.get('name') or f.get('id')}]")
    return "\n".join(p for p in parts if p.strip())


def clean(text: str) -> str:
    text = re.sub(r"<@([A-Z0-9]+)>", lambda m: "@ben" if m.group(1) == BEN else "@someone", text or "")
    text = re.sub(r"<(https?://[^|>]+)(\|[^>]*)?>", r"\1", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def digest_channel(name: str, msgs: list[dict], max_chars: int) -> tuple[str, int]:
    """A transcript of Ben's turns with the context needed to read them."""
    keep: set[int] = set()
    threads: dict[str, list[int]] = defaultdict(list)
    for i, m in enumerate(msgs):
        if m.get("thread_ts"):
            threads[m["thread_ts"]].append(i)
    by_ts = {m.get("ts"): i for i, m in enumerate(msgs)}

    bens = 0
    for i, m in enumerate(msgs):
        if m.get("user") != BEN:
            continue
        bens += 1
        keep.add(i)
        for j in range(max(0, i - LEAD_IN), i):          # what he is answering
            keep.add(j)
        root = m.get("thread_ts")
        if root:
            keep.update(threads.get(root, []))
            if root in by_ts:
                keep.add(by_ts[root])
        else:                                            # replies TO him
            keep.update(threads.get(m.get("ts", ""), []))

    lines = []
    for i in sorted(keep):
        m = msgs[i]
        body = clean(body_of(m))
        if not body:
            continue
        when = datetime.fromtimestamp(float(m.get("ts") or 0), timezone.utc).astimezone()
        lines.append(f"[{when:%Y-%m-%d %H:%M}] {speaker(m)}: {body}")
    text = f"### #{name}\n" + "\n".join(lines)
    if len(text) > max_chars:                            # oldest first is the right cut
        text = text[-max_chars:]
        text = "### #" + name + " (TRUNCATED — older turns dropped)\n" + text.split("\n", 1)[-1]
    return text, bens


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=1.0)
    ap.add_argument("--since", type=float, help="epoch seconds; overrides --days")
    ap.add_argument("--min-ben", type=int, default=2,
                    help="skip a channel with fewer than this many Ben turns")
    ap.add_argument("--max-chars", type=int, default=60000, help="per channel")
    ap.add_argument("--out", default=str(HERE / "digests"))
    ap.add_argument("--checkpoint", action="store_true",
                    help="start from the last run's high-water mark, and record a new one")
    a = ap.parse_args()

    cp = {}
    if CHECKPOINT.exists():
        try:
            cp = json.loads(CHECKPOINT.read_text())
        except ValueError:
            cp = {}

    default_since = a.since or (datetime.now(timezone.utc) - timedelta(days=a.days)).timestamp()
    if not ROOT.is_dir():
        print(f"⛔ archive root {ROOT} is missing — NOT SEARCHED, not empty")
        return 2

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.md"):
        old.unlink()

    chans = sorted(p for p in ROOT.iterdir() if p.is_dir())
    written = skipped = 0
    high: dict[str, float] = {}
    manifest = []
    for ch in chans:
        since = max(default_since, cp.get(ch.name, 0)) if a.checkpoint else default_since
        msgs = load_channel(ch, since)
        if not msgs:
            continue
        high[ch.name] = max(float(m.get("ts") or 0) for m in msgs)
        text, bens = digest_channel(ch.name, msgs, a.max_chars)
        if bens < a.min_ben:
            skipped += 1
            continue
        (out / f"{ch.name}.md").write_text(text)
        manifest.append({"channel": ch.name, "ben_turns": bens, "chars": len(text)})
        written += 1

    manifest.sort(key=lambda r: -r["ben_turns"])
    (out / "manifest.json").write_text(json.dumps(
        {"at": datetime.now(timezone.utc).isoformat(),
         "since": default_since, "channels": manifest}, indent=1))

    total = sum(r["ben_turns"] for r in manifest)
    print(f"archive dirs {len(chans)} · digests {written} · "
          f"skipped (<{a.min_ben} Ben turns) {skipped} · Ben turns {total}")
    print(f"⚠️ a channel the archiving bot is not in is ABSENT here, not empty — "
          f"{len(chans)} covered")
    for r in manifest[:12]:
        print(f"  {r['ben_turns']:4d} turns  {r['chars']:7d} chars  #{r['channel']}")

    if a.checkpoint:
        # ⛔⛔ THE CHECKPOINT IS NOT ADVANCED HERE. Building a digest is not
        # mining it. If this ran the high-water mark forward at digest time, a
        # crash in the mining phase would retire that window permanently and
        # the channels would simply never be read -- silently, because the next
        # run would start after the messages it never looked at.
        # Measured 2026-10-22: a 128 KiB argv limit killed 3 of 10 mining
        # groups, 4 channels, AFTER their digests were built. Under the old
        # behaviour that day's #servers, #scripts and #shopping would be gone.
        # ⇒ Write the marks as PENDING; `mine-run.py` promotes a channel only
        # when its worker returns.
        PENDING.write_text(json.dumps(high, indent=1))
        print(f"{len(high)} channel marks staged PENDING → {PENDING} "
              f"(promoted by mine-run.py on success, never here)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
