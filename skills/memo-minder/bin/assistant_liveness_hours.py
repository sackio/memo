import json, glob, os
hours=set()
for d in ("-mnt-nas-data-code-assistant","-code-assistant"):
    for p in glob.glob(f"/home/ben/.claude/projects/{d}/*.jsonl"):
        try:
            for line in open(p, errors="replace"):
                try: t=json.loads(line).get("timestamp")
                except Exception: continue
                if t and t >= "2026-07-18": hours.add(t[:13])
        except OSError: continue
print(json.dumps(sorted(hours)))
