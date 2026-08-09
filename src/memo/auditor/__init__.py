"""Auditor — shadow observers, liveness, global sweep, constitution proposals.

Principle V is the boundary that shapes this whole package: the auditor may
write and modify ordinary memos, but it may only ever PROPOSE constitutional
ones. The operator owns the constitution.
"""
