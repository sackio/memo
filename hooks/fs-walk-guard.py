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
import re
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

# ⛔ NFS. A walk rooted ANYWHERE at or under these is on the shared mount, so the
#    cost lands on every seat on all four hosts. Prefix match, not exact.
#    ⭐ WHY PREFIX (2026-08-21, second incident): an exact-root list missed
#    `ugrep -rn <pat> .` from cwd /mnt/nas/data/code/quantum-feed -- 15 minutes,
#    whole repo over NFS, on a host at 95% io pressure, with that seat's own
#    claude blocked in nfs_wait. A repo subtree is not cheap just because it is
#    named specifically.
NFS_PREFIXES = ("/mnt/nas", "/mnt/backup")

# grep-family flags whose next token is a value, not a path
GREP_VALUE_FLAGS = {"-e", "--regexp", "-f", "--file", "-m", "--max-count",
                    "-A", "-B", "-C", "--context", "--include", "--exclude",
                    "--exclude-dir", "--include-dir", "-g", "--glob", "-t",
                    "--type", "-T", "--type-not", "--threads", "-j",
                    "--max-filesize", "--max-depth", "--maxdepth", "-M"}

WALKERS = {"find", "bfs", "fd", "fdfind"}
GREPPERS = {"rg", "grep", "egrep", "fgrep", "ripgrep", "ugrep", "ug",
            "ack", "ack-grep", "ag", "pt"}
SEPARATORS = {";", "&&", "||", "|", "&"}

# Wrappers that sit in front of the real command without changing what it reads.
PREFIX_WRAPPERS = {"command", "sudo", "env", "nice", "ionice", "stdbuf",
                   "nohup", "time", "builtin", "exec", "xargs", "timeout"}
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
    "  · To find a file in a git repo on NFS: `git ls-files | grep <pat>` reads the\n"
    "    index without stat-ing the tree. `git grep <pat>` for contents.\n"
    "  · Otherwise: add -maxdepth N / --max-depth N.\n"
    "  · If you genuinely must walk a shared root, say so to the operator first --\n"
    "    it costs every seat on every host, not just you."
)


def norm(p, cwd=None):
    """Absolute, normalised. A relative path is resolved against `cwd`.

    ⛔ RELATIVE IS NOT SAFE. This used to return "" for any relative path, on the
    assumption that a cwd is a local project directory. Most fleet seats have a
    cwd under /mnt/nas/data/code, so `.` IS the NAS and `rg -rn pat .` costs
    exactly what the absolute form costs. The exemption was doing the opposite of
    its intent for the majority of callers.
    """
    if not p:
        return ""
    p = os.path.expanduser(p)
    if not posixpath.isabs(p):
        if not cwd:
            return ""  # cwd unknown -> fail open
        p = posixpath.join(cwd, p)
    return posixpath.normpath(p)


def is_dangerous(p, cwd=None, nfs_prefix=True):
    """`nfs_prefix=False` checks only the exact roots.

    ⭐ THE HARNESS'S OWN Glob/Grep TOOLS GET THE NARROW RULE. A repo-scoped Glob
    is what every agent does all day in a cwd that is nearly always on /mnt/nas,
    so denying it would replace a 15-minute walk with a blocked fleet.

    ⚠️ DEFENSIBLE, NOT FREE. Measured by `agents` on server4, 2026-08-21, at the
    filesystem level (`find <repo> -name '<pat>' -not -path '*/.git/*'`, which is
    what a repo-scoped Glob costs):

        agentkit      695 .md files     4.85s
        atc           876 .md files     7.22s
        quantum-feed  783 .toml files  65.95s

    So a repo-scoped Glob on the NAS is a MINUTE on a large repo, not a blink --
    two orders of magnitude better than the unbounded walk it replaces, which is
    why the exemption stands, but not something to reach for casually. The figure
    is the filesystem cost; the harness Glob may add ignore-handling on top.

    The strict prefix rule applies to ad-hoc shell walkers, which is where every
    measured incident came from and where `-maxdepth` and `git ls-files` are easy
    to name as the alternative.
    """
    n = norm(p, cwd)
    if not n:
        return False
    # ⛔ A TOKEN IS ONLY A SEARCH ROOT IF IT IS A DIRECTORY THAT EXISTS.
    #    Prefix-matching plus relative resolution made EVERY bare word dangerous
    #    from a NAS cwd: prose inside an unquoted echo, a `2>/dev/null`, a regex,
    #    all resolved to /mnt/nas/data/code/<repo>/<word> and matched the prefix.
    #    Measured on 25,793 real commands -- roots like "the", "is", "and" and
    #    "2>/dev/null" were a large share of the hits. One stat per candidate
    #    settles it; a walk of a path that does not exist costs nothing anyway.
    try:
        if not os.path.isdir(n):
            return False
    except OSError:
        return False
    if n in DANGEROUS_ROOTS:
        return True
    if not nfs_prefix:
        return False
    return any(n == pre or n.startswith(pre + "/") for pre in NFS_PREFIXES)


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


def strip_heredocs(command):
    """Remove heredoc BODIES before parsing.

    ⛔ A HEREDOC BODY IS DATA, NOT COMMANDS. Caught by this guard blocking its own
    commit, 2026-08-21: the commit message quoted the ugrep command it was fixing,
    the quotes balanced so shlex parsed it happily, and the prose became a walk.
    Anyone writing a script, a commit message or documentation that MENTIONS a
    command would hit this -- and the failure lands on the people most likely to
    be documenting the rule.

    ⚠️ The bodies are RETURNED, not discarded. For `ssh host 'bash -s' <<REMOTE`
    the body IS the command, and this fleet runs nearly all cross-host work that
    way -- so a rule that is right for a commit message is wrong for the shape a
    supervisor uses most. The caller decides which it is.
    """
    out, bodies, lines, i = [], [], command.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        i += 1
        if not m:
            continue
        delim, body = m.group(2), []
        while i < len(lines) and lines[i].strip() != delim:
            body.append(lines[i])
            i += 1
        if i < len(lines):
            i += 1  # drop the terminator too
        bodies.append("\n".join(body))
    return "\n".join(out), bodies


def _after_pipe(toks, i):
    """True if toks[i] is the command on the receiving end of a pipe."""
    j = i - 1
    while j >= 0:
        t = toks[j]
        if t in PREFIX_WRAPPERS:
            j -= 1
            continue
        if t.isdigit() and j > 0 and toks[j - 1] == "timeout":
            j -= 2
            continue
        return t == "|"
    return False


def check_bash(command, cwd=None, depth=0):
    if depth > 3:
        return
    stripped, bodies = strip_heredocs(command)
    try:
        toks = shlex.split(stripped, comments=True)
    except ValueError:
        return  # unbalanced quotes / heredoc -> fail open
    # ⛔ A COMMAND SENT OVER ssh COSTS THE SAME AS A LOCAL ONE. `/mnt/nas` is the
    #    same filer from every host, so `ssh server5 "find /mnt/nas -name x"` and
    #    `ssh host 'bash -s' <<REMOTE ... REMOTE` are the local walk wearing a
    #    hostname. Judge the payload as a command, with cwd UNKNOWN -- relative
    #    roots on the far side must keep failing open, absolute ones must not.
    nested = []
    for i, tok in enumerate(toks):
        base = posixpath.basename(tok)
        if base == "ssh":
            nested += [t for t in toks[i + 1:] if " " in t.strip()]
            nested += bodies
        elif base in ("bash", "sh", "zsh", "dash") and "-c" in toks[i + 1:i + 3]:
            nested += [t for t in toks[i + 1:i + 4] if " " in t.strip()]
    for payload in nested:
        check_bash(payload, None, depth + 1)
    # ⚠️ A leading `cd` changes what a later `.` means. Track it, or
    # `cd /tmp && find . ...` is judged against the session cwd instead.
    for i, tok in enumerate(toks):
        base = posixpath.basename(tok)
        if base == "cd" and i + 1 < len(toks) and not toks[i + 1].startswith("-"):
            cwd = norm(toks[i + 1], cwd) or cwd
            continue
        if base not in WALKERS and base not in GREPPERS:
            continue
        # ⛔ A GREP AT THE END OF A PIPE READS STDIN -- IT IS NOT A WALK.
        #    `ps aux | grep foo`, `crontab -l | command grep x`, `docker logs |
        #    grep y` have no path argument, and treating the missing path as "."
        #    denies them from any NAS cwd. Measured on 25,793 real fleet commands:
        #    this single rule is most of the false positives, and every one of
        #    them is a command that touches no filesystem at all.
        if base in GREPPERS and _after_pipe(toks, i):
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
            bad = [r for r in roots if is_dangerous(r, cwd)]
            if bad:
                deny("`%s` would descend from %s with no -maxdepth."
                     % (base, ", ".join(bad)))
        else:
            if any(f in seg for f in DEPTH_FLAGS):
                continue  # bounded
            recursive = any(t.startswith("-") and not t.startswith("--")
                            and "r" in t[1:].lower() for t in seg) \
                or "--recursive" in seg
            if base in ("rg", "ripgrep", "ag", "pt", "ack", "ack-grep", "ug", "ugrep"):
                recursive = True   # these recurse by default
            if not recursive:
                continue
            # ⚠️ The FIRST bare token is the PATTERN, not a path. Without this the
            # deny message names the search string as a root, which reads as a bug
            # to whoever it stops and costs the guard its credibility.
            roots, skip, seen_pattern = [], False, False
            for t in seg:
                if skip:
                    skip = False
                    continue
                if t.startswith("-"):
                    if t in GREP_VALUE_FLAGS:
                        skip = True
                    continue
                if not seen_pattern:
                    seen_pattern = True
                    continue
                roots.append(t)
            if not roots and seen_pattern:
                roots = ["."]      # `rg pat` with no path means cwd
            bad = [r for r in roots if is_dangerous(r, cwd)]
            if bad:
                deny("`%s` would recurse from %s."
                     % (base, ", ".join(norm(r, cwd) or r for r in bad)))


def check_search_tool(tool_input, cwd=None):
    path = tool_input.get("path")
    if path and is_dangerous(path, cwd, nfs_prefix=False):
        deny("The search is rooted at %s, which is on the shared NFS mount."
             % norm(path, cwd))
    # Glob patterns can carry their own absolute root
    pattern = tool_input.get("pattern") or ""
    if isinstance(pattern, str) and pattern.startswith("/"):
        head = pattern.split("*", 1)[0]
        head = head.rsplit("/", 1)[0] if "/" in head else head
        if head and is_dangerous(head, cwd, nfs_prefix=False):
            deny("The glob pattern is rooted at %s." % norm(head, cwd))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    tool = data.get("tool_name") or ""
    # ⚠️ No cwd in the payload -> relative roots are UNKNOWN, so fail open on them
    #    rather than guessing from this subprocess's own cwd. Absolute roots are
    #    still checked.
    cwd = data.get("cwd")
    if not (isinstance(cwd, str) and posixpath.isabs(cwd)):
        cwd = None
    ti = data.get("tool_input")
    if not isinstance(ti, dict):
        sys.exit(0)
    try:
        if tool == "Bash":
            cmd = ti.get("command")
            if isinstance(cmd, str):
                check_bash(cmd, cwd)
        elif tool in ("Glob", "Grep", "Search"):
            check_search_tool(ti, cwd)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open
    sys.exit(0)


if __name__ == "__main__":
    main()
