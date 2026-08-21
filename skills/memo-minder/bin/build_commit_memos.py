#!/usr/bin/env python3
"""Read git_pulse_with_context.py output (jsonl on stdin); emit memo-store-ready records."""
import json, sys, re, os

REPO = os.environ.get('REPO_NAME', 'repo')

def derive_tags(commit, repo):
    s = (commit['subject'] + ' ' + commit.get('body','')).lower()
    tags = ['git-sourced', f'repo-{repo}']
    # heuristic content tags
    if re.search(r'\b(refactor|rewrite|migration|deprecat)', s): tags.append('refactor')
    if re.search(r'\b(perf|optim|throughput|latency|memory|oom)', s): tags.append('performance')
    if re.search(r'\b(fix|bug|crash|regression)', s): tags.append('bugfix')
    if re.search(r'\b(feature|introduce|add new|implement)', s) and 'add' not in s[:10]: tags.append('feature')
    if re.search(r'\b(security|auth|encryp|cve)', s): tags.append('security')
    if re.search(r'\b(test|coverage)', s): tags.append('testing')
    if re.search(r'\b(infra|deploy|cluster|k8s|docker)', s): tags.append('infra')
    return tags

for line in sys.stdin:
    line = line.strip()
    if not line or line.startswith('#'): continue
    try:
        c = json.loads(line)
    except json.JSONDecodeError:
        continue
    sha = c['sha']
    subject = c['subject']
    body = c.get('body','').strip()
    why = c.get('why', [])

    title = f"{REPO}: {subject[:90]} ({sha})"

    content_parts = [f"**Commit**: {sha} — {subject}",
                     f"**Date**: {c['ts']}",
                     f"**Repo**: {REPO}",
                     ""]
    if body:
        content_parts.append("## Rationale (commit body)")
        content_parts.append(body[:4500])
        content_parts.append("")
    if why:
        content_parts.append("## Session context (where this decision was discussed)")
        for w in why[:2]:
            content_parts.append(f"### {w['session']} @ line {w['line']}")
            content_parts.append("```")
            content_parts.append(w['context'][:3000])
            content_parts.append("```")
            content_parts.append("")

    # decide WHETHER to memo: skip if no body AND no why blocks
    if not body and not why:
        continue
    # skip if body very thin AND no why
    if len(body) < 80 and not why:
        continue

    content = '\n'.join(content_parts)
    tags = derive_tags(c, REPO)

    print(json.dumps({
        'title': title,
        'content': content,
        'tags': tags,
        'sha': sha,
        'subject': subject,
    }))
