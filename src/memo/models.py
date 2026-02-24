from typing import Any
from pydantic import BaseModel


class Document(BaseModel):
    id: str
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    created_at: float
    updated_at: float


class StoreRequest(BaseModel):
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    db_path: str | None = None


class StoreResponse(BaseModel):
    id: str


class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    min_score: float | None = None
    tags: list[str] = []
    db_path: str | None = None


class SearchResult(BaseModel):
    document: Document
    score: float


class DeleteResponse(BaseModel):
    deleted: bool
