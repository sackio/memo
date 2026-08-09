"""Retrieval + storage mediators (US2).

Every read goes through `recall`, every write through `store`. Agents do not
touch raw `POST /search` / `POST /documents` on the mediated path.
"""
