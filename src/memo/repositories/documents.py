"""Document repository — the only module above this line that knows we use sqlite. [001/FR-001]

Per research.md R-03: sqlite + sqlite-vec stays the substrate for now, but the
bi-temporal and supersession-edge operations are funnelled through this
interface so swapping in Postgres is a single-file change instead of an edit to
every call site.

The contract for anyone implementing a second backend:

* Methods are ``async`` and return plain ``dict`` rows (JSON columns already
  decoded) or None — NOT ``sqlite3.Row``, and NOT Pydantic models. Keeping the
  boundary at plain dicts is what lets the substrate change without touching
  the mediators; converting to :class:`memo.models.Memo` is the caller's job.
* ``db_path`` is accepted and forwarded but the server is single-global
  (see ``db._resolve_path``); a backend may ignore it.
* No method may raise on "not found" — that is a None return. Exceptions are
  reserved for genuine substrate failures, so callers can map None to 404
  without a try/except.
"""
from __future__ import annotations

from typing import Any, Protocol

from memo import db


class DocumentRepository(Protocol):
    """Structural interface a storage backend must satisfy.

    A ``Protocol`` rather than an ABC on purpose: ``SqliteDocumentRepository``
    below does not inherit from anything, and a future
    ``PostgresDocumentRepository`` will not either. Conformance is checked by
    the type checker at the call site, which is where a swap would actually go
    wrong.
    """

    async def get(self, doc_id: str, *, db_path: str | None = ...) -> dict | None: ...

    async def get_current(self, doc_id: str, *, db_path: str | None = ...) -> dict | None: ...

    async def get_as_of(self, doc_id: str, t: float, *,
                        db_path: str | None = ...) -> dict | None: ...

    async def supersede(self, old_id: str, new_memo: dict[str, Any],
                        embedding: list[float], actor: str, *,
                        reason: str | None = ...,
                        operator_directive_ref: dict[str, Any] | None = ...,
                        db_path: str | None = ...) -> dict | None: ...

    async def reap_expired(self, *, now: float | None = ...,
                           db_path: str | None = ...) -> list[str]: ...


class SqliteDocumentRepository:
    """sqlite + sqlite-vec implementation, delegating to :mod:`memo.db`.

    Deliberately a thin pass-through — the SQL itself stays in ``db.py`` where
    the connection cache and migration runner live. This class exists to pin
    the *shape* of the interface, not to add behavior; logic that creeps in
    here would have to be reimplemented by every future backend.
    """

    async def get(self, doc_id: str, *, db_path: str | None = None) -> dict | None:
        """Fetch by exact id, ignoring bi-temporal state.

        Returns the row even when superseded — use :meth:`get_current` for
        "what is true now". Kept because audit/migration paths legitimately
        need to read a specific historical version.
        """
        return await db.get(db_path, doc_id)

    async def get_current(self, doc_id: str, *, db_path: str | None = None) -> dict | None:
        """Currently-valid version of ``doc_id``'s lineage. [001/FR-002]"""
        return await db.get_current(db_path, doc_id)

    async def get_as_of(self, doc_id: str, t: float, *,
                        db_path: str | None = None) -> dict | None:
        """Version of ``doc_id``'s lineage that was true at ``t``. [001/FR-002]"""
        return await db.get_as_of(db_path, doc_id, t)

    async def supersede(self, old_id: str, new_memo: dict[str, Any],
                        embedding: list[float], actor: str, *,
                        reason: str | None = None,
                        operator_directive_ref: dict[str, Any] | None = None,
                        db_path: str | None = None) -> dict | None:
        """Atomically close ``old_id`` and write its replacement. [001/FR-003]

        None means ``old_id`` is unknown or already superseded.
        """
        return await db.supersede(
            db_path, old_id, new_memo, embedding, actor,
            reason=reason, operator_directive_ref=operator_directive_ref,
        )

    async def reap_expired(self, *, now: float | None = None,
                           db_path: str | None = None) -> list[str]:
        """Delete rows past their ``expires_at``; returns reaped ids. [001/FR-007]"""
        return await db.reap_expired(db_path, now)


# Module-level default. Call sites import THIS rather than constructing a
# backend, so the Postgres swap is one assignment here.
documents: DocumentRepository = SqliteDocumentRepository()
