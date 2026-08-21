#!/usr/bin/env python3
"""For each commit in a repo since CUTOFF, find context in matching session logs.

Usage: python3 git_pulse_with_context.py <repo_path> <cutoff> <session_path_or_remote>

Emits per-commit JSON to stdout: {sha, subject, body, why_blocks: [...]}.
"""
import subprocess, json, re, sys, os

def commits_since(repo, cutoff):
    out = subprocess.check_output(
        ['git', '-C', repo, 'log', f'--since={cutoff}', '--format=%h%x00%ai%x00%s%x00%b%x1e'],
        text=True, errors='replace')
    commits = []
    for record in out.split('\x1e'):
        record = record.strip()
        if not record: continue
        parts = record.split('\x00')
        if len(parts) < 3: continue
        sha, ts, subject = parts[0], parts[1], parts[2]
        body = parts[3] if len(parts) > 3 else ''
        commits.append({'sha':sha, 'ts':ts, 'subject':subject, 'body':body})
    return commits

def is_substantive(commit):
    """Heuristic for commits that name a durable decision worth WHY-mining."""
    s = commit['subject'].lower()
    if not s: return False
    if s.startswith(('merge ', 'wip', 'fixup', 'fmt', 'lint', 'typo', 'comment', 'rename ')):
        return False
    if 'dependency' in s or 'bump ' in s:
        return False
    return True

def extract_anchor_phrase(subject):
    """Return a distinctive substring (4+ tokens) of the commit subject to grep for."""
    # strip "<prefix>: " if present
    if ': ' in subject:
        _, rest = subject.split(': ', 1)
    else:
        rest = subject
    rest = rest.strip()
    # use the first ~50 chars as the anchor
    return rest[:60] if len(rest) >= 20 else rest

def find_context_local(session_path, anchor, window=80, max_blocks=3):
    """Read a session jsonl, find lines containing anchor, return surrounding ±window lines."""
    if not os.path.exists(session_path):
        return []
    try:
        lines = open(session_path, errors='replace').readlines()
    except Exception:
        return []
    blocks = []
    seen_at = set()
    for i, ln in enumerate(lines):
        if anchor in ln and not any(abs(i - j) < window for j in seen_at):
            start = max(0, i - window)
            end = min(len(lines), i + window)
            blocks.append({'session': os.path.basename(session_path),
                           'line': i+1,
                           'context': ''.join(lines[start:end])[:6000]})  # cap context size
            seen_at.add(i)
            if len(blocks) >= max_blocks: break
    return blocks

def find_context_ssh(host, session_path, anchor, window=80, max_blocks=3):
    """grep -n on remote host, then for each hit fetch ±window with sed."""
    anchor_esc = anchor.replace("'", "'\\''")
    # find first N matching line numbers
    try:
        out = subprocess.check_output(
            ['ssh', '-o', 'ConnectTimeout=5', '-o', 'BatchMode=yes', '-p', '4999',
             f'ben@{host}',
             f"grep -nF '{anchor_esc}' {session_path} 2>/dev/null | head -{max_blocks*3} | cut -d: -f1"],
            text=True, errors='replace')
    except subprocess.CalledProcessError:
        return []
    line_nums = []
    for x in out.strip().split('\n'):
        if not x.strip(): continue
        try: line_nums.append(int(x))
        except ValueError: continue
    if not line_nums:
        return []
    # dedup by window
    line_nums.sort()
    keep = []
    for ln in line_nums:
        if not any(abs(ln - k) < window for k in keep):
            keep.append(ln)
        if len(keep) >= max_blocks: break
    blocks = []
    for ln in keep:
        start = max(1, ln - window)
        end = ln + window
        try:
            ctx = subprocess.check_output(
                ['ssh', '-o', 'ConnectTimeout=5', '-o', 'BatchMode=yes', '-p', '4999',
                 f'ben@{host}',
                 f"sed -n '{start},{end}p' {session_path} | head -c 6000"],
                text=True, errors='replace')
        except subprocess.CalledProcessError:
            ctx = ''
        blocks.append({'session': os.path.basename(session_path), 'line': ln, 'context': ctx})
    return blocks

if __name__ == '__main__':
    repo = sys.argv[1]
    cutoff = sys.argv[2]
    # remaining args: each "host:path" or "local:path"
    locations = sys.argv[3:]
    commits = commits_since(repo, cutoff)
    sub = [c for c in commits if is_substantive(c)]
    print(f"# {repo}: {len(commits)} commits since {cutoff}, {len(sub)} substantive", file=sys.stderr)
    for c in sub[:30]:  # cap at 30 per run
        anchor = extract_anchor_phrase(c['subject'])
        why = []
        for loc in locations:
            host, path = loc.split(':', 1)
            if host == 'local':
                blocks = find_context_local(path, anchor)
            else:
                blocks = find_context_ssh(host, path, anchor)
            if blocks:
                why.extend(blocks)
                if len(why) >= 3: break
        c['anchor'] = anchor
        c['why'] = why
        print(json.dumps({k:v for k,v in c.items() if k!='body' or v}))
