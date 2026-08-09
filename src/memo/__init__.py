"""memo v2 — mediated, bi-temporal, integration-ready knowledge substrate.

See specs/001-memo-renovation/ for the full design + FR list. Every
public module in this package anchors to specific requirements via
spec-prefixed requirement markers in the narrowest owning unit
(fn/class header or docstring). Do NOT anchor at this package-level
docstring — reserved for genuinely package-spanning invariants only.

(This docstring deliberately spells out no literal marker: an example
marker here scans as a real anchor and reports as dangling, which is
exactly what happened on 2026-07-29.)
"""
__version__ = "2.0.0-alpha1"
