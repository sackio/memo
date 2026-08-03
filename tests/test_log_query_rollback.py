"""A failed query-log commit must not pin a stale read snapshot. [v0.3.9]

⛔ THE BUG THIS PINS. `log_query` runs inside every read on live fleet
infrastructure. Python's sqlite3 opens a transaction implicitly before DML and
holds it until commit, so when the INSERT succeeded and the COMMIT failed — a 5s
`busy_timeout` on a host at 50% iowait — the bare `except: pass` left the
transaction OPEN on that thread's connection. Every later read on that
connection then served a snapshot from before other connections' commits.

Reported independently on 2026-08-03 by two seats on the same host in the same
hour: `memo_update` returning `not_found` for an id `memo_search` had just
returned, and `database is locked` on bulk writes — both recovering on an
unchanged retry, which is the signature of a stale snapshot rather than a
missing row.

⚠️ Introduced by the query logging added in v0.3.8. The observation damaged the
thing it observed, in exactly the way `log_query`'s own docstring says it must
never do — so the test asserts the CONNECTION STATE, not just that no exception
escaped. A test that only checked "log_query does not raise" passed throughout.
"""
from __future__ import annotations

import sqlite3

import pytest

from memo import db


class _FailCommit:
    """Delegates everything; COMMIT raises, as a locked disk makes it.

    ⚠️ A real `sqlite3.Connection.commit` is READ-ONLY and cannot be
    monkeypatched — the first version of this test tried and died with
    `AttributeError: attribute 'commit' is read-only`, which would have looked
    like a broken test rather than an untestable fix.
    """

    def __init__(self, conn):
        self._c = conn
        self.commits = 0
        self.rollbacks = 0

    def execute(self, *a, **k):
        return self._c.execute(*a, **k)

    def commit(self):
        self.commits += 1
        raise sqlite3.OperationalError("database is locked")

    def rollback(self):
        self.rollbacks += 1
        return self._c.rollback()

    @property
    def in_transaction(self):
        return self._c.in_transaction


@pytest.fixture()
def conn(tmp_path):
    # ⚠️ log_query IGNORES db_path (single-global since 2026-06-29), so the
    # connection has to be injected rather than addressed by path — otherwise
    # this test writes to the REAL corpus.
    return db._get_or_create_conn(str(tmp_path / "t.db"))


def test_an_insert_really_does_open_a_transaction(conn):
    """⭐ NEGATIVE CONTROL FOR THE WHOLE FIX. If DML did not leave a transaction
    open, there would have been no stale-snapshot bug and nothing to roll back —
    the fix would be cargo cult. Measured: False → True → False."""
    assert conn.in_transaction is False
    conn.execute("INSERT INTO query_log (ts,op,query,n_results) "
                 "VALUES (?,?,?,?)", (1.0, "search", "x", 0))
    assert conn.in_transaction is True, (
        "DML did not open a transaction; the premise of this fix is wrong")
    conn.rollback()
    assert conn.in_transaction is False


def test_failed_commit_rolls_back_and_leaves_no_open_transaction(conn, monkeypatch):
    """⭐ THE ONE THAT MATTERS."""
    proxy = _FailCommit(conn)
    monkeypatch.setattr(db, "_get_or_create_conn", lambda *a, **k: proxy)
    db.log_query(None, "search", query="forced failure", result_ids=["b"])
    assert proxy.commits == 1, "the commit path never ran; test proves nothing"
    assert proxy.rollbacks == 1, "no rollback after a failed commit"
    # ⛔ THE ASSERTION THE OLD CODE FAILED.
    assert conn.in_transaction is False, (
        "a failed commit left the transaction open; every later read on this "
        "connection serves a pre-commit snapshot")


def test_log_query_still_never_raises(conn, monkeypatch):
    """The original contract, unchanged: this runs inside every live read."""
    class _Explode:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O error")
        def commit(self): raise AssertionError("unreachable")
        def rollback(self): pass
    monkeypatch.setattr(db, "_get_or_create_conn", lambda *a, **k: _Explode())
    db.log_query(None, "search", query="anything")   # must not raise


def test_a_committed_log_is_visible_and_leaves_no_transaction(conn, monkeypatch):
    """POSITIVE CONTROL — without it, a log_query that silently did nothing
    would satisfy every assertion above."""
    monkeypatch.setattr(db, "_get_or_create_conn", lambda *a, **k: conn)
    db.log_query(None, "search", query="control probe", result_ids=["a", "b"])
    assert conn.in_transaction is False
    n = conn.execute("SELECT COUNT(*) FROM query_log WHERE query = ?",
                     ("control probe",)).fetchone()[0]
    assert n == 1, "log_query wrote nothing; the other tests are vacuous"


def test_busy_timeout_is_generous_enough_for_a_loaded_disk(conn):
    """5s produced real `database is locked` errors on 2026-08-03. The timeout
    is a ceiling, not a delay, so a large value costs nothing when healthy."""
    got = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert got >= 30000, f"busy_timeout is {got}ms; a saturated disk exceeds it"
