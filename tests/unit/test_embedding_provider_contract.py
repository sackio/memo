"""The embedding request must match what the provider actually accepts. [002/FR-108]

2026-08-01, moving off OpenRouter onto the self-hosted `qwen3-embedding-4b`.

`embed()` and `embed_batch()` both passed `dimensions=settings.embedding_dimensions`.
That is a hard 400 against Qwen3:

    Model 'qwen3-embedding-4b' does not support Matryoshka embeddings;
    dimensions must be unset (received dimensions=2560).

Verified against the live endpoint before the change, not taken on report. The
parameter only ever existed to pin OpenAI's Matryoshka models to a chosen width;
Qwen3 has one width and refuses to be told it. So every embed — every store,
every search, every passage — would have failed at once.

That failure would at least be loud. The reason it gets a test anyway is that it
is a one-word regression: anyone re-adding `dimensions=` for a future provider
breaks the current one instantly, and the symptom (100% of embeds 400ing) is far
from the cause (a kwarg). Asserted on the call itself, not on a docstring.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from memo import embeddings
from memo.config import settings


def _source() -> str:
    return Path(embeddings.__file__).read_text()


def _embed_calls() -> list[ast.Call]:
    """Every `_client.embeddings.create(...)` call in the module."""
    tree = ast.parse(_source())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "create":
            inner = f.value
            if isinstance(inner, ast.Attribute) and inner.attr == "embeddings":
                out.append(node)
    return out


def test_both_embed_call_sites_are_found():
    """Guard the guard: if this drops to 0 the checks below pass vacuously."""
    assert len(_embed_calls()) == 2, (
        "expected exactly two provider calls (embed, embed_batch) — if the module "
        "was restructured, update this test rather than deleting it")


def test_no_call_site_passes_a_dimensions_parameter():
    """The regression. Fails the instant `dimensions=` is re-added."""
    offenders = [
        kw.arg for call in _embed_calls() for kw in call.keywords
        if kw.arg == "dimensions"
    ]
    assert not offenders, (
        "`dimensions=` is a 400 against qwen3-embedding-4b — the model has one "
        "native width and rejects being told it. Width is asserted by the vector "
        "tables instead.")


def test_every_call_site_passes_the_configured_model():
    for call in _embed_calls():
        names = {kw.arg for kw in call.keywords}
        assert "model" in names, "the model must come from settings, never a literal"
        assert "input" in names


def test_client_is_pointed_at_the_configured_endpoint_not_a_literal():
    """A hardcoded OpenRouter URL here would silently keep billing us and, worse,
    return 3072-wide vectors that the 2560 tables reject on insert."""
    src = _source()
    assert "openrouter.ai" not in src, (
        "the embedding provider is now a LAN service; a leftover OpenRouter base "
        "URL would embed at the wrong width and bill us for it")
    assert "settings.embedding_base_url" in src
    assert "settings.embedding_api_key" in src


# --- The width, which is the safety property ---

def test_configured_width_collides_with_neither_prior_model():
    """2560 is chosen so a half-finished re-embed CRASHES rather than scoring
    nonsense: sqlite-vec rejects an insert whose width differs from the table's.

    1536 = text-embedding-3-small, 3072 = text-embedding-3-large. Both have been
    live in this corpus. A new model sharing either width would make old and new
    vectors indistinguishable in shape, which is the failure this project has
    spent a week engineering against.
    """
    assert settings.embedding_dimensions not in (1536, 3072), (
        f"{settings.embedding_dimensions} collides with a model this corpus has "
        "already used — pick a width that makes a partial re-embed fail loudly")


def test_embedding_provider_is_separate_from_the_chat_provider():
    """auto-store/classify still use OpenRouter; embeddings do not. Folding them
    back into one setting re-couples two providers that deliberately moved apart."""
    assert settings.embedding_base_url != "https://openrouter.ai/api/v1"
    assert hasattr(settings, "openrouter_api_key"), (
        "the chat provider key must survive — only the EMBEDDING provider moved")
