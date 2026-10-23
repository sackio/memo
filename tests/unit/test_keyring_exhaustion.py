"""`keyring.mark_exhausted` -- recording that a grant account is empty.

⭐ WHY THIS FILE EXISTS. On 2026-10-23 the audit lane discovered `agents` was
   out of credit at 03:16, rolled to `pushbuild`, and wrote nothing down.
   `grants.json` went on reporting $111.98 (a 10-22 Console snapshot minus the
   day's logged spend), so memo-minder picked `agents` at 06:17 and spent $2.41
   over 44 minutes to rediscover the same fact, losing the day's mining.
   `mark_exhausted` is what writes it down.

⛔ THE POSITIVE CONTROL POINTS AT A TEMP FILE, NOT THE REAL ONE. Writing this
   test the lazy way -- driving `with-grant-key` end to end against the live
   keyring -- zeroed `pushbuild`'s real $87.39 balance during development. A
   control for a destructive function must not be aimed at production.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "memo-audit"))
import keyring  # noqa: E402


GRANTS = {
    "agents": {"monthly": 200, "expires": "2026-10-26",
               "console_left": 199.68, "console_at": "2026-10-22T15:07:40Z"},
    "pushbuild": {"monthly": 200, "expires": "2026-11-11",
                  "console_left": 162.78, "console_at": "2026-10-22T15:07:40Z"},
    # NEVER-listed: load_grants() filters it out, so a writer built on that
    # dict would silently delete it. mark_exhausted must preserve it.
    "admin": {"monthly": 0, "note": "Laura's, real billing"},
}


@pytest.fixture
def grants(tmp_path, monkeypatch):
    """⛔ Isolate BOTH halves of `remaining()`: the grants file AND the spend
    ledger. Patching only the file left the test reading the live ledger, so
    `remaining()` moved with the fleet's real spend and the assertions below
    passed or failed depending on the hour."""
    p = tmp_path / "grants.json"
    p.write_text(json.dumps(GRANTS, indent=2))
    monkeypatch.setattr(keyring, "GRANTS_PATH", p)
    monkeypatch.setattr(keyring, "_sum_spend", lambda *a, **k: 0.0)
    return p


def read(p):
    return json.loads(p.read_text())


@pytest.mark.parametrize("text", [
    '{"result":"Credit balance is too low","is_error":true}',
    "ERROR: your credit balance is TOO LOW to access the Anthropic API",
])
def test_looks_exhausted_positive(text):
    assert keyring.looks_exhausted(text)


@pytest.mark.parametrize("text", [
    "", "all good", '{"result":"rate limited","is_error":true}',
    "balance is low",            # near-miss: must NOT match
])
def test_looks_exhausted_negative(text):
    assert not keyring.looks_exhausted(text)


def test_marks_the_account_empty(grants):
    assert keyring.mark_exhausted("agents", "unit test") is True
    g = read(grants)["agents"]
    assert g["console_left"] == 0.0
    assert g["console_at"].endswith("Z")
    assert "MEASURED EMPTY" in g["note"]
    assert "199.68" in g["note"], "the note must record what it overwrote"


def test_leaves_other_accounts_alone(grants):
    keyring.mark_exhausted("agents", "unit test")
    assert read(grants)["pushbuild"] == GRANTS["pushbuild"]


def test_preserves_never_listed_accounts(grants):
    """⛔ Writing load_grants() output back would DELETE `admin`."""
    keyring.mark_exhausted("agents", "unit test")
    assert read(grants)["admin"] == GRANTS["admin"]


def test_is_idempotent(grants):
    assert keyring.mark_exhausted("agents", "first") is True
    first = read(grants)["agents"]["note"]
    assert keyring.mark_exhausted("agents", "second") is False
    assert read(grants)["agents"]["note"] == first, "no second note, no churn"


def test_unknown_account_is_a_no_op(grants):
    assert keyring.mark_exhausted("nosuchacct", "unit test") is False
    assert read(grants) == GRANTS


def test_remaining_reads_zero_afterwards(grants):
    assert keyring.remaining("agents") > 100     # the stale, confident figure
    keyring.mark_exhausted("agents", "unit test")
    assert keyring.remaining("agents") == 0.0


def test_next_acct_stops_picking_it(grants):
    assert keyring.next_acct() == "agents"       # soonest expiry, 10-26
    keyring.mark_exhausted("agents", "unit test")
    assert keyring.next_acct() == "pushbuild"    # rolls, without a human edit


def test_a_backup_is_written(grants):
    keyring.mark_exhausted("agents", "unit test")
    baks = list(grants.parent.glob("grants.json.bak-exhausted-*"))
    assert len(baks) == 1
    assert json.loads(baks[0].read_text()) == GRANTS, "backup is the PRE state"


# ── the 06:17 regression: a job started on an account it could not fund ─────

def test_the_dollar_floor_hands_over_an_account_that_cannot_fund_the_job(
        tmp_path, monkeypatch):
    """Reproduce 2026-10-23 06:17 exactly, then show `--min` prevents it.

    `agents` read $1.35 and expires soonest; memo-minder costs $1.69-$5.00.
    The old call -- `next_acct()` with no min_balance -- floors at $1.00, so
    $1.35 qualified and the run died broke 44 minutes later.
    """
    p = tmp_path / "grants.json"
    p.write_text(json.dumps({
        "agents": {"monthly": 200, "expires": "2026-10-26",
                   "console_left": 1.35, "console_at": "2026-10-22T15:07:40Z"},
        "pushbuild": {"monthly": 200, "expires": "2026-11-11",
                      "console_left": 87.39, "console_at": "2026-10-22T15:07:40Z"},
    }, indent=2))
    monkeypatch.setattr(keyring, "GRANTS_PATH", p)
    monkeypatch.setattr(keyring, "_sum_spend", lambda *a, **k: 0.0)

    # what actually happened
    assert keyring.next_acct() == "agents"

    # what with-grant-key now asks for (DEFAULT_MIN_USD = 6.0)
    assert keyring.next_acct(min_balance=6.0) == "pushbuild"


# ─────────────────────────────────────────────────────────────────────────────
# `keyring.nightly_budget` -- pacing each grant to finish at its own expiry.
#
# ⭐ WHY THESE EXIST. This function decides how much real money the nightly cycle
#    spends. Both failure directions are silent and expensive: too low strands a
#    grant that expires unused, too high drains the pool and the maintenance lane
#    goes dark for the rest of the month. Neither raises, and the cycle log looks
#    normal either way -- so the only thing standing between those two outcomes
#    is arithmetic nobody re-reads.
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)


def test_each_grant_is_paced_over_its_own_days_not_a_shared_horizon(grants):
    """agents $199.68/3d + pushbuild $162.78/19d -- summed, not averaged."""
    got = keyring.nightly_budget(now=NOW)
    assert got == pytest.approx(199.68 / 3 + 162.78 / 19, rel=1e-6)


def test_it_is_a_small_fraction_of_the_pool_it_replaces(grants):
    """The regression that motivated it: the old rule offered the WHOLE pool."""
    burst = sum(keyring.remaining(a) for a in keyring.load_grants())
    assert burst == pytest.approx(199.68 + 162.78, rel=1e-6)
    assert keyring.nightly_budget(now=NOW) < burst / 4


def test_a_grant_expiring_tomorrow_offers_its_whole_balance_tonight(grants, tmp_path):
    """⛔ The money-stranding case. A grant with 1 day left must not be paced."""
    raw = json.loads((tmp_path / "grants.json").read_text())
    raw["agents"]["expires"] = "2026-10-24"
    (tmp_path / "grants.json").write_text(json.dumps(raw))
    assert keyring.nightly_budget(now=NOW) == pytest.approx(199.68 + 162.78 / 19, rel=1e-6)


def test_an_expiry_already_past_is_not_a_negative_budget(grants, tmp_path):
    """max(1, days) -- a stale `expires` must not subtract from the night."""
    raw = json.loads((tmp_path / "grants.json").read_text())
    raw["agents"]["expires"] = "2026-10-01"          # 22 days ago
    (tmp_path / "grants.json").write_text(json.dumps(raw))
    got = keyring.nightly_budget(now=NOW)
    assert got > 0
    assert got == pytest.approx(199.68 + 162.78 / 19, rel=1e-6)


def test_an_empty_account_contributes_nothing(grants, tmp_path):
    raw = json.loads((tmp_path / "grants.json").read_text())
    raw["agents"]["console_left"] = 0.0
    (tmp_path / "grants.json").write_text(json.dumps(raw))
    assert keyring.nightly_budget(now=NOW) == pytest.approx(162.78 / 19, rel=1e-6)


def test_marking_an_account_exhausted_drops_it_out_of_the_budget(grants):
    """The two mechanisms must agree: a measured-empty account stops being paced."""
    before = keyring.nightly_budget(now=NOW)
    keyring.mark_exhausted("agents", evidence="unit test")
    after = keyring.nightly_budget(now=NOW)
    assert after == pytest.approx(162.78 / 19, rel=1e-6)
    assert after < before


def test_the_NEVER_account_is_never_paced(grants):
    """`admin` is Laura's real billing. load_grants filters it; so must this."""
    raw = json.loads(keyring.GRANTS_PATH.read_text())
    assert "admin" in raw, "fixture must still carry the NEVER account"
    assert "admin" not in keyring.load_grants()
    # its absence is already implied by the exact total asserted above
    assert keyring.nightly_budget(now=NOW) == pytest.approx(199.68 / 3 + 162.78 / 19, rel=1e-6)
