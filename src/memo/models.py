from typing import Any
from pydantic import BaseModel


class Document(BaseModel):
    id: str
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    token_count: int = 0
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


class Filters(BaseModel):
    tags: list[str] = []
    after: float | None = None       # created_at >= after (Unix timestamp)
    before: float | None = None      # created_at <= before (Unix timestamp)
    min_tokens: int | None = None
    max_tokens: int | None = None


class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    min_score: float | None = None
    tags: list[str] = []
    after: float | None = None
    before: float | None = None
    min_tokens: int | None = None
    max_tokens: int | None = None
    db_path: str | None = None


class SearchResult(BaseModel):
    document: Document
    score: float


class UpdateRequest(BaseModel):
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    db_path: str | None = None


class DeleteResponse(BaseModel):
    deleted: bool
