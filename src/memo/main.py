import contextlib
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from mcp.server.fastmcp import FastMCP

from memo import db, embeddings
from memo.config import settings
from memo.models import (
    DeleteResponse,
    Document,
    SearchRequest,
    SearchResult,
    StoreRequest,
    StoreResponse,
    UpdateRequest,
)

# --- MCP server ---

mcp = FastMCP("memo", stateless_http=True, streamable_http_path="/")
mcp_starlette = mcp.streamable_http_app()
mcp_starlette.router.lifespan_context = lambda app: contextlib.AsyncExitStack()


# --- MCP Tools ---

@mcp.tool()
async def memo_store(
    content: str,
    title: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: str | None = None,
) -> dict:
    """Store a document with automatic embedding and token count.

    db_path controls which database to write to:
    - None (default): global DB
    - directory path (e.g. current working directory): stores in <dir>/.memo.db
    - explicit .db file path: stores in that file
    """
    embedding = await embeddings.embed(content)
    doc_id = await db.store(
        db_path=db_path,
        content=content,
        title=title,
        tags=tags or [],
        metadata=metadata or {},
        embedding=embedding,
    )
    return {"id": doc_id}


@mcp.tool()
async def memo_update(
    id: str,
    content: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: str | None = None,
) -> dict | None:
    """Update an existing memo by ID. Only provided fields are changed.

    If content is updated, the embedding and token_count are recomputed automatically.
    Returns the updated memo, or null if the ID was not found.
    """
    embedding = await embeddings.embed(content) if content is not None else None
    result = await db.update(
        db_path=db_path,
        doc_id=id,
        content=content,
        title=title,
        tags=tags,
        metadata=metadata,
        embedding=embedding,
    )
    return result


@mcp.tool()
async def memo_search(
    query: str,
    limit: int = 10,
    min_score: float | None = None,
    tags: list[str] | None = None,
    after: float | None = None,
    before: float | None = None,
    min_tokens: int | None = None,
    max_tokens: int | None = None,
    db_path: str | None = None,
    scope: str = "local",
) -> list[dict]:
    """Search documents by semantic similarity with optional filters.

    db_path: directory path (uses <dir>/.memo.db), explicit .db file, or None for global DB.
    scope controls which database(s) to search:
    - "local" (default): only the DB specified by db_path (or global if db_path is None)
    - "global": only the global DB, ignoring db_path
    - "all": search both db_path DB and global DB, merge results by score

    Filters:
    - tags: only return docs that have at least one of these tags
    - after/before: Unix timestamps bounding created_at
    - min_tokens/max_tokens: bound by stored token_count of content
    """
    embedding = await embeddings.embed(query)
    kwargs = dict(embedding=embedding, limit=limit, min_score=min_score, tags=tags or [],
                  after=after, before=before, min_tokens=min_tokens, max_tokens=max_tokens)

    if scope == "global" or (scope == "local" and db_path is None):
        return await db.search(db_path=None, **kwargs)

    if scope == "all" and db_path is not None:
        paths = list({db.global_path(), db._resolve_path(db_path)})
        return await db.search_multi(paths, **kwargs)

    return await db.search(db_path=db_path, **kwargs)


@mcp.tool()
async def memo_get(id: str, db_path: str | None = None) -> dict | None:
    """Retrieve a document by ID.

    db_path: directory path (uses <dir>/.memo.db), explicit .db file, or None for global DB.
    """
    return await db.get(db_path=db_path, doc_id=id)


@mcp.tool()
async def memo_delete(id: str, db_path: str | None = None) -> dict:
    """Delete a document by ID.

    db_path: directory path (uses <dir>/.memo.db), explicit .db file, or None for global DB.
    """
    deleted = await db.delete(db_path=db_path, doc_id=id)
    return {"deleted": deleted}


@mcp.tool()
async def memo_list(
    tags: list[str] | None = None,
    after: float | None = None,
    before: float | None = None,
    min_tokens: int | None = None,
    max_tokens: int | None = None,
    limit: int = 100,
    db_path: str | None = None,
    scope: str = "local",
) -> list[dict]:
    """List documents with optional filters.

    db_path: directory path (uses <dir>/.memo.db), explicit .db file, or None for global DB.
    scope controls which database(s) to list from:
    - "local" (default): only the DB specified by db_path (or global if db_path is None)
    - "global": only the global DB, ignoring db_path
    - "all": list from both db_path DB and global DB, merged by created_at desc

    Filters:
    - tags: only return docs that have at least one of these tags
    - after/before: Unix timestamps bounding created_at
    - min_tokens/max_tokens: bound by stored token_count of content
    """
    kwargs = dict(tags=tags or [], limit=limit, after=after, before=before,
                  min_tokens=min_tokens, max_tokens=max_tokens)

    if scope == "global" or (scope == "local" and db_path is None):
        return await db.list_docs(db_path=None, **kwargs)

    if scope == "all" and db_path is not None:
        paths = list({db.global_path(), db._resolve_path(db_path)})
        return await db.list_docs_multi(paths, **kwargs)

    return await db.list_docs(db_path=db_path, **kwargs)


# --- FastAPI app ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="memo", lifespan=lifespan)
app.mount("/mcp", mcp_starlette)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/documents", response_model=StoreResponse)
async def store_document(req: StoreRequest):
    embedding = await embeddings.embed(req.content)
    doc_id = await db.store(
        db_path=req.db_path,
        content=req.content,
        title=req.title,
        tags=req.tags,
        metadata=req.metadata,
        embedding=embedding,
    )
    return StoreResponse(id=doc_id)


@app.get("/documents", response_model=list[Document])
async def list_documents(
    tags: list[str] = Query(default=[]),
    after: float | None = Query(default=None),
    before: float | None = Query(default=None),
    min_tokens: int | None = Query(default=None),
    max_tokens: int | None = Query(default=None),
    limit: int = Query(default=100),
    db_path: str | None = Query(default=None),
):
    docs = await db.list_docs(
        db_path=db_path, tags=tags, limit=limit,
        after=after, before=before, min_tokens=min_tokens, max_tokens=max_tokens,
    )
    return [Document(**d) for d in docs]


@app.get("/documents/{doc_id}", response_model=Document)
async def get_document(doc_id: str, db_path: str | None = Query(default=None)):
    doc = await db.get(db_path=db_path, doc_id=doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return Document(**doc)


@app.patch("/documents/{doc_id}", response_model=Document)
async def update_document(doc_id: str, req: UpdateRequest):
    embedding = await embeddings.embed(req.content) if req.content is not None else None
    result = await db.update(
        db_path=req.db_path,
        doc_id=doc_id,
        content=req.content,
        title=req.title,
        tags=req.tags,
        metadata=req.metadata,
        embedding=embedding,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Memo not found")
    return Document(**result)


@app.delete("/documents/{doc_id}", response_model=DeleteResponse)
async def delete_document(doc_id: str, db_path: str | None = Query(default=None)):
    deleted = await db.delete(db_path=db_path, doc_id=doc_id)
    return DeleteResponse(deleted=deleted)


@app.post("/search", response_model=list[SearchResult])
async def search_documents(req: SearchRequest):
    embedding = await embeddings.embed(req.query)
    results = await db.search(
        db_path=req.db_path,
        embedding=embedding,
        limit=req.limit,
        min_score=req.min_score,
        tags=req.tags,
        after=req.after,
        before=req.before,
        min_tokens=req.min_tokens,
        max_tokens=req.max_tokens,
    )
    return [SearchResult(document=Document(**r["document"]), score=r["score"]) for r in results]


def main():
    uvicorn.run("memo.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
