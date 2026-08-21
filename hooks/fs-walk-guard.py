#!/usr/bin/env python3
"""
PreToolUse hook — refuse an UNBOUNDED filesystem walk rooted at a path that
contains NFS.

WHY THIS EXISTS (2026-08-21). Two `bfs` processes ran for 38-43 minutes on
server4:

    bfs -S dfs / -path /proc -prune -o -type d -iname '*-mnt-nas-data-code-agentkit*'
    bfs -S dfs /mnt/nas -iname '*agent-a97*'

Both are an agent hunting for a Claude Code transcript it could not locate by
path. While they ran, server4's NFS LOOKUP latency was 478 ms (1.53 ms after
they were killed) and READDIR averaged 66 ms. Every seat on every host shares
that mount, so one agent's improvised walk degrades the whole fleet -- and
nothing in the stack said no.

⛔ GUIDANCE IN A SKILL WAS NOT ENOUGH, AND CANNOT BE. The seat that did this was
not reading memo's transcript tooling; it was looking for `agentkit`'s project
directory. A PreToolUse hook is the only layer that sees every agent's tool call
regardless of which skill it loaded or which repo it lives in.

⚠️ FAIL OPEN, ALWAYS. Any parse failure, missing field, unexpected shape or
unknown tool exits 0 and allows. A guard that breaks tool calls when it
malfunctions is worse than the walks it prevents.

What it does NOT block: any `-maxdepth`/`--max-depth`, any root inside a project
directory, anything under /tmp, and ordinary `rg`/`grep` in a repo. The target is
specifically an unbounded descent from a root that contains NFS.
"""
import json
import os
import posixpath
import shlex
import sys

# Roots where an unbounded descent crosses into /mnt/nas (NFS, shared fleet-wide)
# or into every seat's home. Normalised, no trailing slash except "/".
DANGEROUS_ROOTS = {
    "/",
    "/mnt",
    "/mnt/nas",
    "/mnt/nas/data",
    "/mnt/nas/data/code",
    "/mnt/backup",
    "/home",
    "/home/ben",
    os.path.expanduser("~"),
}

WALKERS = {"find", "bfs", "fd", "fdfind"}
GREPPERS = {"rg", "grep", "egrep", "fgrep", "ripgrep"}
SEPARATORS = {";", "&&", "||", "|", "&"}
DEPTH_FLAGS = {"-maxdepth", "--max-depth", "-max-depth", "--maxdepth", "-d"}

# Flags whose NEXT token is a value, not a search root. Both the pre-path
# options (`bfs -S dfs`, `find -regextype ...`) and the predicates that take an
# argument (`-iname '*x*'`, `-newer f`, `-path /proc`).
VALUE_FLAGS = {
    "-S", "-D", "-O", "-j", "-f", "-regextype", "--regextype",
    "-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename",
    "-regex", "-iregex", "-lname", "-ilname", "-samefile",
    "-newer", "-newermt", "-newerat", "-anewer", "-cnewer",
    "-type", "-xtype", "-size", "-perm", "-user", "-group", "-uid", "-gid",
    "-inum", "-links", "-mtime", "-atime", "-ctime", "-mmin", "-amin", "-cmin",
    "-printf", "-fprintf", "-fprint", "-fprint0", "-fls",
    "-exec", "-execdir", "-ok", "-okdir",
    "-mindepth", "-depth", "-limit", "-color", "-status", "-context",
}

HINT = (
    "Bound the walk, or address the file directly.\n"
    "  · A Claude Code transcript is at a DETERMINISTIC path -- never search for one:\n"
    "      ~/.claude/projects/<slug>/<session-id>.jsonl\n"
    "      slug = the project's absolute path with BOTH '/' and '_' replaced by '-'\n"
    "      (e.g. /mnt/nas/data/code/server_admin -> -mnt-nas-data-code-server-admin)\n"
    "      To search transcripts, use the `search-transcripts` skill, not a walk.\n"
    "  · Otherwise: add -maxdepth N, or root the search at the project directory.\n"
    "  · If you genuinely must walk a shared root, say so to the operator first --\n"
    "    it costs every seat on every host, not just you."
)


def norm(p):
    if not p:
        return ""
    p = os.path.expanduser(p)
    if not posixpath.isabs(p):
        return ""  # relative -> resolved against cwd, which is a project dir; allow
    return posixpath.normpath(p)


def is_dangerous(p):
    return norm(p) in DANGEROUS_ROOTS


def deny(reason):
    msg = "⛔ Unbounded filesystem walk refused.\n\n" + reason + "\n\n" + HINT
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        },
        # older schema, harmless if unread
        "decision": "block",
        "reason": msg,
    }))
    sys.exit(0)


def check_bash(command):
    try:
        toks = shlex.split(command, comments=True)
    except ValueError:
        return  # unbalanced quotes / heredoc -> fail open
    for i, tok in enumerate(toks):
        base = posixpath.basename(tok)
        if base not in WALKERS and base not in GREPPERS:
            continue
        # tokens belonging to THIS invocation only
        seg = []
        for t in toks[i + 1:]:
            if t in SEPARATORS:
                break
            seg.append(t)
        if base in WALKERS:
            if any(f in seg for f in DEPTH_FLAGS):
                continue  # bounded
            # ⛔ A FLAG'S VALUE IS NOT A ROOT. The first version of this guard
            #    collected `dfs` out of `bfs -S dfs / -iname ...` and stopped
            #    there, so it ALLOWED both real incident commands while passing
            #    every synthetic case. Skip each value-taking flag's argument.
            roots, skip = [], False
            for t in seg:
                if skip:
                    skip = False
                    continue
                if t.startswith("-"):
                    if t in VALUE_FLAGS:
                        skip = True
                    continue
                roots.append(t)
            bad = [r for r in roots if is_dangerous(r)]
            if bad:
                deny("`%s` would descend from %s with no -maxdepth."
                     % (base, ", ".join(bad)))
        else:
            recursive = any(t in ("-r", "-R", "--recursive", "-rn", "-rl", "-rln",
                                  "-rni", "-ril", "-rin") for t in seg)
            if base in ("rg", "ripgrep"):
                recursive = True   # ripgrep recurses by default
            if not recursive:
                continue
            bad = [t for t in seg if not t.startswith("-") and is_dangerous(t)]
            if bad:
                deny("`%s` would recurse from %s." % (base, ", ".join(bad)))


def check_search_tool(tool_input):
    path = tool_input.get("path")
    if path and is_dangerous(path):
        deny("The search is rooted at %s, which descends the whole shared tree."
             % norm(path))
    # Glob patterns can carry their own absolute root
    pattern = tool_input.get("pattern") or ""
    if isinstance(pattern, str) and pattern.startswith("/"):
        head = pattern.split("*", 1)[0]
        head = head.rsplit("/", 1)[0] if "/" in head else head
        if head and is_dangerous(head):
            deny("The glob pattern is rooted at %s." % norm(head))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input")
    if not isinstance(ti, dict):
        sys.exit(0)
    try:
        if tool == "Bash":
            cmd = ti.get("command")
            if isinstance(cmd, str):
                check_bash(cmd)
        elif tool in ("Glob", "Grep", "Search"):
            check_search_tool(ti)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open
    sys.exit(0)


if __name__ == "__main__":
    main()
