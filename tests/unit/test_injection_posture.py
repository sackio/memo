"""Memory-posture + injection opt-out detection. [001/FR-017 001/FR-018]"""
import pytest

from memo.injection import posture


def test_posture_on_by_default():
    assert posture.memory_posture(env={}) == "on"


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_posture_off_when_disable_set(val):
    assert posture.memory_posture(env={posture.DISABLE_AUTO_MEMORY: val}) == "off"


@pytest.mark.parametrize("val", ["0", "false", "", "no", "off"])
def test_posture_on_for_falsey_values(val):
    assert posture.memory_posture(env={posture.DISABLE_AUTO_MEMORY: val}) == "on"


def test_opt_out_requires_explicit_value():
    assert posture.injection_opted_out(env={}) is False
    assert posture.injection_opted_out(env={posture.DISABLE_INJECTION: "1"}) is True
    assert posture.injection_opted_out(env={posture.DISABLE_INJECTION: "0"}) is False


def test_unreadable_environ_fails_open():
    """A /proc read failure must not be read as 'opted out'.

    Failing closed here would silently strip memory from any session whose
    environ we cannot read — a much worse outcome than injecting for a session
    that wanted to opt out.
    """
    assert posture.read_environ(None) == {}
    assert posture.read_environ(999_999_999) == {}
    assert posture.injection_opted_out(pid=999_999_999) is False
    assert posture.memory_posture(pid=999_999_999) == "on"


def test_reads_real_process_environ(monkeypatch):
    """Sanity-check the /proc parse against this very process."""
    import os
    env = posture.read_environ(os.getpid())
    assert env, "should read own environ"
    assert "PATH" in env


def test_disable_auto_memory_does_not_mean_inject_less():
    """C71: posture 'off' means Layer 2 IS the memory layer, not that it's off."""
    env = {posture.DISABLE_AUTO_MEMORY: "1"}
    assert posture.memory_posture(env=env) == "off"
    assert posture.injection_opted_out(env=env) is False
