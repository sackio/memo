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
    """Store a document with automatic embedding generation."""
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
async def memo_search(
    query: str,
    limit: int = 10,
    min_score: float | None = None,
    tags: list[str] | None = None,
    db_path: str | None = None,
) -> list[dict]:
    """Search documents by semantic similarity."""
    embedding = await embeddings.embed(query)
    results = await db.search(
        db_path=db_path,
        embedding=embedding,
        limit=limit,
        min_score=min_score,
        tags=tags or [],
    )
    return results


@mcp.tool()
async def memo_get(id: str, db_path: str | None = None) -> dict | None:
    """Retrieve a document by ID."""
    return await db.get(db_path=db_path, doc_id=id)


@mcp.tool()
async def memo_delete(id: str, db_path: str | None = None) -> dict:
    """Delete a document by ID."""
    deleted = await db.delete(db_path=db_path, doc_id=id)
    return {"deleted": deleted}


@mcp.tool()
async def memo_list(
    tags: list[str] | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """List documents with optional tag filter."""
    return await db.list_docs(db_path=db_path, tags=tags or [], limit=limit)


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
    limit: int = Query(default=100),
    db_path: str | None = Query(default=None),
):
    docs = await db.list_docs(db_path=db_path, tags=tags, limit=limit)
    return [Document(**d) for d in docs]


@app.get("/documents/{doc_id}", response_model=Document)
async def get_document(doc_id: str, db_path: str | None = Query(default=None)):
    doc = await db.get(db_path=db_path, doc_id=doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return Document(**doc)


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
    )
    return [SearchResult(document=Document(**r["document"]), score=r["score"]) for r in results]


def main():
    uvicorn.run("memo.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
