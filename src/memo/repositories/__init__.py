"""Storage-substrate abstractions.

See ``documents.py``. Per research.md R-03 the bi-temporal + supersession
layer lives behind a repository interface so a future Postgres migration is a
single-file swap rather than an edit to every call site.
"""
