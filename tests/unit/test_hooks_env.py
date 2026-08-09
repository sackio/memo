"""memo-hooks install must write the CONFIGURED values into hooks.env. [002/T284]

Written because the opposite was true for months and nothing said so. config.py
carried a block of five fields under the comment "Hook settings (written to
~/.memo/hooks.env during memo-hooks install)", and `cmd_install` wrote five
literals. The file looked correct, the settings looked wired, and
`memo_recall_min_score` was reachable from no Python caller at all — the shell
hook read the env var of that name, and the only writer of it was the literal.

So the refuting observation these tests exist to make, stated before they were
written: **set every field away from its default, install, and read the file
back. If any line still shows the default, the setting is decorative.** That is
exactly what the pre-fix code does, and it is why the equality assertions below
use non-default values throughout. A test written with the defaults would have
passed against the bug.

**Watched to fire, 2026-08-02.** `git stash push src/memo/hooks.py`, rebuild the
test image, run: **5 failed, 2 passed.** The two survivors are the two written to
hold in *both* worlds (defaults unchanged, port still from the argument), so that
split is the designed one.

⚠️ One honest caveat about *how* the five fail there: pre-fix, `hooks.py` has no
`memo_settings` attribute at all, so `monkeypatch.setattr` raises `AttributeError`
before the assertion is reached. They fail because the wiring is absent, not
because a `0.5` was observed where `0.35` was set. The demonstration still shows
these tests are coupled to the fix rather than passing vacuously — but "it fails
without the fix" and "it fails on the value" are different claims and only the
first one was measured.
"""
from __future__ import annotations

import argparse

import pytest

from memo import hooks


def _sandbox(tmp_path, monkeypatch):
    """Point every path cmd_install touches at tmp_path.

    The three script paths are stubbed because `HOOKS_DIR` is derived from the
    source layout (`__file__/../../../hooks`), which does not exist once memo is
    installed as a package — in the test image it resolves to
    `/usr/local/lib/python3.12/hooks` and `cmd_install` exits 1 before reaching
    the code under test. That is a real wart in `memo-hooks`, unrelated to T284
    and left alone here; stubbing keeps this file about env generation.
    """
    monkeypatch.setattr(hooks, "SETTINGS_PATH", tmp_path / ".claude" / "settings.json")
    monkeypatch.setattr(hooks, "HOOKS_ENV_PATH", tmp_path / ".memo" / "hooks.env")
    monkeypatch.delenv("MEMO_PORT", raising=False)
    for name in ("AUTO_RECALL_SCRIPT", "PREWORK_SCRIPT", "AUTO_STORE_SCRIPT"):
        stub = tmp_path / f"{name.lower()}.sh"
        stub.write_text("#!/bin/bash\n")
        monkeypatch.setattr(hooks, name, stub)


def _install(tmp_path, monkeypatch, **overrides):
    """Run cmd_install into a temp HOME and return the parsed hooks.env."""
    _sandbox(tmp_path, monkeypatch)
    for field, value in overrides.items():
        monkeypatch.setattr(hooks.memo_settings, field, value)

    hooks.cmd_install(argparse.Namespace(port=8000, skip_check=True))

    env = {}
    for line in (tmp_path / ".memo" / "hooks.env").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, val = line.partition("=")
            env[key] = val
    return env


def test_min_score_comes_from_settings(tmp_path, monkeypatch):
    """The field that was reachable from nowhere. 0.35 is not the 0.5 default."""
    env = _install(tmp_path, monkeypatch, memo_recall_min_score=0.35)
    assert env["MEMO_RECALL_MIN_SCORE"] == "0.35"


def test_token_budget_comes_from_settings(tmp_path, monkeypatch):
    env = _install(tmp_path, monkeypatch, memo_recall_token_budget=4096)
    assert env["MEMO_RECALL_TOKEN_BUDGET"] == "4096"


@pytest.mark.parametrize(
    "field,key",
    [
        ("memo_auto_recall", "MEMO_AUTO_RECALL"),
        ("memo_prework_recall", "MEMO_PREWORK_RECALL"),
        ("memo_auto_store", "MEMO_AUTO_STORE"),
    ],
)
def test_bool_flags_come_from_settings(tmp_path, monkeypatch, field, key):
    """All three default to True, so False is the value that can fail.

    And it must be written as `false`, not `False`: memo-auto-recall.sh compares
    with `[ "${MEMO_AUTO_RECALL:-true}" = "false" ]`, so a Python-cased `False`
    is not equal to the string that disables the hook — it would leave the hook
    ON while the file plainly says it is off.
    """
    env = _install(tmp_path, monkeypatch, **{field: False})
    assert env[key] == "false"


def test_defaults_still_write_the_documented_values(tmp_path, monkeypatch):
    """The wiring must not have moved the defaults themselves.

    Guards the other direction: a settings-driven file is only an improvement if
    an unconfigured host keeps getting what it got before.
    """
    env = _install(tmp_path, monkeypatch)
    assert env["MEMO_RECALL_MIN_SCORE"] == "0.5"
    assert env["MEMO_RECALL_TOKEN_BUDGET"] == "2000"
    assert env["MEMO_AUTO_RECALL"] == "true"
    assert env["MEMO_PREWORK_RECALL"] == "true"
    assert env["MEMO_AUTO_STORE"] == "true"
    # No config.py field backs this one; it is deliberately still a literal.
    assert env["MEMO_AUTO_STORE_MIN_LEN"] == "200"


def test_port_still_comes_from_the_argument_not_settings(tmp_path, monkeypatch):
    """MEMO_PORT was never a config.py field and must not become one by accident."""
    _sandbox(tmp_path, monkeypatch)
    hooks.cmd_install(argparse.Namespace(port=8091, skip_check=True))
    assert "MEMO_PORT=8091" in (tmp_path / ".memo" / "hooks.env").read_text()
