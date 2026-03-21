"""memo-hooks: install/remove/status Claude Code hooks for memo auto-recall."""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
AUTO_RECALL_SCRIPT = HOOKS_DIR / "memo-auto-recall.sh"
PREWORK_SCRIPT = HOOKS_DIR / "memo-prework-recall.sh"
AUTO_STORE_SCRIPT = HOOKS_DIR / "memo-auto-store.sh"

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
HOOKS_ENV_PATH = Path.home() / ".memo" / "hooks.env"


def _load_settings() -> dict:
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    return {}


def _save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
    print(f"Updated {SETTINGS_PATH}")


def _hook_entry(script_path: Path) -> dict:
    """Wrap a command script in the Claude Code hook envelope format."""
    return {"hooks": [{"type": "command", "command": str(script_path)}]}


_MEMO_SCRIPTS = ("memo-auto-recall.sh", "memo-prework-recall.sh", "memo-auto-store.sh")


def _is_memo_hook(entry: dict) -> bool:
    """Check if a hook entry (in envelope format) references a memo script."""
    for hook in entry.get("hooks", []):
        cmd = hook.get("command", "")
        if any(s in cmd for s in _MEMO_SCRIPTS):
            return True
    # Also handle the (now-invalid) flat format for cleanup purposes
    cmd = entry.get("command", "")
    return any(s in cmd for s in _MEMO_SCRIPTS)


def _check_server(port: int) -> bool:
    try:
        req = urllib.request.urlopen(f"http://localhost:{port}/health", timeout=3)
        return req.status == 200
    except Exception:
        return False


def cmd_install(args) -> None:
    port = int(os.environ.get("MEMO_PORT", args.port))

    if not args.skip_check:
        print(f"Checking memo server at localhost:{port}...")
        if not _check_server(port):
            print(f"Warning: memo server not reachable at localhost:{port}/health")
            print("Install will proceed, but hooks will silently no-op until the server is running.")
        else:
            print("Memo server reachable.")

    for script in (AUTO_RECALL_SCRIPT, PREWORK_SCRIPT, AUTO_STORE_SCRIPT):
        if not script.exists():
            print(f"Error: hook script not found at {script}", file=sys.stderr)
            sys.exit(1)

    settings = _load_settings()
    hooks = settings.setdefault("hooks", {})

    # UserPromptSubmit hook — semantic recall on each prompt
    prompt_hooks = hooks.setdefault("UserPromptSubmit", [])
    auto_entry = _hook_entry(AUTO_RECALL_SCRIPT)
    if any(_is_memo_hook(e) and "memo-auto-recall.sh" in str(e) for e in prompt_hooks):
        print("UserPromptSubmit hook already installed.")
    else:
        prompt_hooks.append(auto_entry)
        print(f"Added UserPromptSubmit hook: {AUTO_RECALL_SCRIPT}")

    # PreToolUse hook — inject memo index once per session
    pretool_hooks = hooks.setdefault("PreToolUse", [])
    prework_entry = _hook_entry(PREWORK_SCRIPT)
    if any(_is_memo_hook(e) and "memo-prework-recall.sh" in str(e) for e in pretool_hooks):
        print("PreToolUse hook already installed.")
    else:
        pretool_hooks.append(prework_entry)
        print(f"Added PreToolUse hook: {PREWORK_SCRIPT}")

    # Stop hook — auto-store/update memos from completed exchanges
    stop_hooks = hooks.setdefault("Stop", [])
    store_entry = _hook_entry(AUTO_STORE_SCRIPT)
    if any(_is_memo_hook(e) and "memo-auto-store.sh" in str(e) for e in stop_hooks):
        print("Stop hook already installed.")
    else:
        stop_hooks.append(store_entry)
        print(f"Added Stop hook: {AUTO_STORE_SCRIPT}")

    _save_settings(settings)

    # Write hooks.env
    HOOKS_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    env_content = f"""\
# memo hooks configuration
# Edit these values to tune behavior without reinstalling hooks.

MEMO_PORT={port}
MEMO_AUTO_RECALL=true
MEMO_PREWORK_RECALL=true
MEMO_AUTO_STORE=true
MEMO_RECALL_MIN_SCORE=0.5
MEMO_RECALL_TOKEN_BUDGET=2000
MEMO_AUTO_STORE_MIN_LEN=200
"""
    with open(HOOKS_ENV_PATH, "w") as f:
        f.write(env_content)
    print(f"Wrote {HOOKS_ENV_PATH}")

    print("\nInstallation complete.")
    print("To disable a hook without removing it, set the relevant flag to false in:")
    print(f"  {HOOKS_ENV_PATH}")
    print("  e.g. MEMO_AUTO_STORE=false  MEMO_AUTO_RECALL=false  MEMO_PREWORK_RECALL=false")
    print("\nRun 'memo-hooks status' to verify.")


def cmd_remove(args) -> None:
    if not SETTINGS_PATH.exists():
        print("settings.json not found — nothing to remove.")
        return

    settings = _load_settings()
    hooks = settings.get("hooks", {})
    changed = False

    for event in ("UserPromptSubmit", "PreToolUse", "Stop"):
        original = hooks.get(event, [])
        filtered = [e for e in original if not _is_memo_hook(e)]
        if len(filtered) < len(original):
            hooks[event] = filtered
            removed = len(original) - len(filtered)
            print(f"Removed {removed} memo hook(s) from {event}.")
            changed = True
        else:
            print(f"No memo hooks found in {event}.")

    if changed:
        _save_settings(settings)
    else:
        print("No changes made.")

    if HOOKS_ENV_PATH.exists() and not args.keep_env:
        HOOKS_ENV_PATH.unlink()
        print(f"Removed {HOOKS_ENV_PATH}")
    elif args.keep_env:
        print(f"Kept {HOOKS_ENV_PATH} (--keep-env)")


def cmd_status(args) -> None:
    print(f"Settings file: {SETTINGS_PATH}")
    print(f"Hooks env:     {HOOKS_ENV_PATH}")
    print(f"Hook scripts:  {HOOKS_DIR}/")
    print()

    if not SETTINGS_PATH.exists():
        print("settings.json: not found")
        return

    settings = _load_settings()
    hooks = settings.get("hooks", {})

    def _hook_installed(event: str, script_name: str) -> bool:
        return any(
            script_name in h.get("command", "")
            for e in hooks.get(event, [])
            for h in e.get("hooks", [])
        )

    auto_installed = _hook_installed("UserPromptSubmit", "memo-auto-recall.sh")
    prework_installed = _hook_installed("PreToolUse", "memo-prework-recall.sh")
    store_installed = _hook_installed("Stop", "memo-auto-store.sh")

    print(f"UserPromptSubmit (auto-recall):  {'INSTALLED' if auto_installed else 'not installed'}")
    print(f"PreToolUse (prework index):      {'INSTALLED' if prework_installed else 'not installed'}")
    print(f"Stop (auto-store):               {'INSTALLED' if store_installed else 'not installed'}")

    print()
    if HOOKS_ENV_PATH.exists():
        print(f"hooks.env ({HOOKS_ENV_PATH}):")
        with open(HOOKS_ENV_PATH) as f:
            for line in f:
                line = line.rstrip()
                if line and not line.startswith("#"):
                    print(f"  {line}")
    else:
        print("hooks.env: not found (defaults will be used)")

    port = int(os.environ.get("MEMO_PORT", 8000))
    print()
    reachable = _check_server(port)
    print(f"Memo server (localhost:{port}): {'reachable' if reachable else 'not reachable'}")


def main():
    parser = argparse.ArgumentParser(
        prog="memo-hooks",
        description="Install/remove/check Claude Code hooks for memo auto-recall",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_install = sub.add_parser("install", help="Install hooks into ~/.claude/settings.json")
    p_install.add_argument("--port", default=8000, type=int, help="Memo server port (default: 8000)")
    p_install.add_argument("--skip-check", action="store_true", help="Skip server reachability check")

    p_remove = sub.add_parser("remove", help="Remove memo hooks from ~/.claude/settings.json")
    p_remove.add_argument("--keep-env", action="store_true", help="Do not delete ~/.memo/hooks.env")

    sub.add_parser("status", help="Show hook installation status")

    args = parser.parse_args()
    dispatch = {"install": cmd_install, "remove": cmd_remove, "status": cmd_status}
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
