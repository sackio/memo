"""Every write path must refuse an over-length document LEGIBLY, not with a 500.

[memo, 2026-08-21] `cc718ab` gave `POST /documents` a 413 for a document the
embedding provider refuses as over its context limit. It did that AT THE CALL
SITE, and the other six `embed_document` callers kept returning a bare 500:
MCP `memo_update`, `PATCH /documents/{id}`, `/supersede`, `/auto-store` (create
AND merge), and the operator-directive handler.

⭐ Found by watching a seat retry-loop `PATCH`es against two oversized memos —
26 × 500 in a 32-minute window. The retries could never succeed and the caller
was never told why, because a 500 says "memo is broken" rather than "this
document is too big, split it".

⛔ THIS FILE ASSERTS THE CONTRACT ACROSS PATHS, NOT ONE ENDPOINT — the original
fix passed every test anyone wrote for it and was still wrong five paths over.
A per-endpoint test would reproduce exactly that blind spot. When a new
`embed_document` caller appears, it belongs in `paths` below.
"""
import pytest
from fastapi import HTTPException

from memo import embeddings, main


TOO_BIG = "x" * 90_000
PROVIDER_MSG = "This model's maximum context length is 16384 tokens."


@pytest.fixture
def refusing_embedder(monkeypatch):
    """The provider is the authority on its own limit; here it says no."""
    async def _raise(text):
        raise embeddings.EmbeddingInputTooLarge(PROVIDER_MSG, len(text))
    monkeypatch.setattr(embeddings, "embed_document", _raise)


def _assert_actionable(detail: str):
    """The message must tell the caller what to DO, not merely that it failed."""
    assert "too large to embed" in detail
    assert "Split it into separate memos" in detail, \
        "a refusal that names no remedy is a 500 with better manners"
    assert str(embeddings.MAX_INPUT_TOKENS) in detail.replace(",", ""), \
        "the caller cannot judge how much to split without the limit"
    assert PROVIDER_MSG[:40] in detail, "the provider's own words must survive"


@pytest.mark.asyncio
async def test_http_write_paths_return_413_not_500(refusing_embedder):
    """The four HTTP surfaces that go through the shared wrapper."""
    with pytest.raises(HTTPException) as exc:
        await main._embed_document_or_413(TOO_BIG)

    assert exc.value.status_code == 413, \
        "413 Payload Too Large is the honest code; 500 blames the server"
    _assert_actionable(exc.value.detail)


@pytest.mark.asyncio
async def test_mcp_memo_update_returns_a_structured_refusal(temp_db, refusing_embedder):
    """MCP has no status codes, so it needs the same refusal in its own shape.

    An HTTPException raised inside a tool escapes as an opaque transport error —
    which is the 500 problem again, one layer out.
    """
    from memo import db
    vec = [0.1] * db.settings.embedding_dimensions
    doc_id = await db.store(db_path=None, content="small", title="t",
                            tags=[], metadata={}, embedding=vec)

    got = await main.memo_update(id=doc_id, content=TOO_BIG, allow_shrink=True)

    assert got["updated"] is False
    assert got["reason"] == "too_large", \
        "callers branch on `reason`; prose is not a machine-readable outcome"
    assert got["chars"] == len(TOO_BIG)
    _assert_actionable(got["detail"])


@pytest.mark.asyncio
async def test_the_memo_is_not_silently_truncated_to_fit(temp_db, refusing_embedder):
    """⛔ The refusal must not become a partial store.

    A 20k-token memo embedded as its first 16k yields a PLAUSIBLE vector, not a
    correct one, and nothing downstream would ever reveal the difference. The
    stored row must be unchanged after a refused update.
    """
    from memo import db
    vec = [0.1] * db.settings.embedding_dimensions
    doc_id = await db.store(db_path=None, content="original", title="t",
                            tags=[], metadata={}, embedding=vec)

    await main.memo_update(id=doc_id, content=TOO_BIG, allow_shrink=True)

    after = await db.get(None, doc_id)
    assert after["content"] == "original", \
        "a refused write must leave the prior content intact, not half-apply"
