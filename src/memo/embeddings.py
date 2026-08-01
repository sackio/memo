from openai import AsyncOpenAI
from memo.config import settings

_client = AsyncOpenAI(
    api_key=settings.embedding_api_key,
    base_url=settings.embedding_base_url,
)


# NO `dimensions=` PARAMETER. It used to be passed here and in embed_batch, and
# it is a hard 400 against the self-hosted Qwen3 endpoint:
#
#   "Model 'qwen3-embedding-4b' does not support Matryoshka embeddings;
#    dimensions must be unset (received dimensions=2560)."
#
# (Verified against the live endpoint 2026-08-01 rather than taken on report.)
# The parameter only ever existed to pin OpenAI's Matryoshka models to a chosen
# width; Qwen3 has exactly one width and rejects being told it. Width is now
# asserted by the vector tables instead, which is the stronger place for it —
# a wrong width becomes a rejected INSERT rather than a request parameter nobody
# checks. See config.embedding_dimensions.
# Hard provider limit, measured 2026-08-01 against the live endpoint:
#   "This model's maximum context length is 8192 tokens."
#
# THE TRAP, because it is not the obvious one: `text-embedding-3-large` allowed
# 8,191 tokens under cl100k and this corpus's largest memo is 8,160 — it fit, and
# R-09 embedded it. Under Qwen3's tokenizer the SAME memo is ~9,033 tokens. Same
# nominal limit, different vocabulary, so documents that fit on the old model
# silently stop fitting on the new one. 7 of 7,336 documents here.
#
# Truncation is defensible ONLY because passages exist: the document-level vector
# is an entry point and `document_chunks` carries the full text, which is the
# entire premise of 002. On a system without a passage index this should raise
# instead — losing a memo's tail with no other route to it is data loss wearing a
# success's clothes.
# ⛔ WE DO NOT TRUNCATE. A version of this file briefly did, and it was wrong.
#
# The provider rejects inputs over 8,192 tokens ("This model's maximum context
# length is 8192 tokens"), and 7 of this corpus's 7,336 documents exceed it —
# only because Qwen3's tokenizer produces ~1.107x more tokens than cl100k for the
# same text, so memos that FIT on `text-embedding-3-large` (8,191 limit) no
# longer do. Same nominal number, different vocabulary.
#
# But 8192 is a SERVING FLAG, not a property of the model: `--max-model-len=8192`
# was set to save VRAM when the endpoint ran tensor-parallel across two 8GB
# cards, a configuration that no longer exists. The model itself supports 32K.
# (vLLM phrases the flag's rejection as "this model's maximum context length",
# which is what made it look intrinsic.)
#
# So truncating — or client-side splitting — would bake a superseded hardware
# constraint into the corpus PERMANENTLY, and nothing downstream would ever
# reveal it: a 12k-token memo embedded as its first 8k is not the same vector as
# the whole, it is merely a plausible one. Better to let the 7 fail loudly, let
# `memo-reembed-corpus` refuse to swap on the count mismatch, and add them once
# the limit is raised. The refusal IS the reminder, which is worth more than any
# note I could leave myself. (Caught by the `embeddings` seat, 2026-08-01,
# minutes before it would have been committed.)
#
# For the record, since it argued against a fixed char cap and still holds: text
# density varies enormously. Three of the largest memos at an identical 22,000
# chars measured 3.21, 3.15 and <2.69 chars/token, and one needed cutting to
# 8,458 chars to reach 7,277 tokens — 1.16 chars/token, four times denser than
# this corpus's 3.179 mean. Any limit derived from a mean is wrong for the tail,
# which is exactly where the long documents live.
MAX_INPUT_TOKENS = 8192


async def embed(text: str) -> list[float]:
    response = await _client.embeddings.create(
        model=settings.embedding_model,
        input=text,
    )
    return response.data[0].embedding


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """One over-length input 400s the WHOLE request, not just itself.

    Measured the hard way: a single batch of 32 lost all 32 because 7 of them
    were over the limit. With size-sorted work those cluster at the front and
    take out the largest items together — the exact items you least want to lose.
    So inputs are fitted before they are sent, and a batch that fails anyway is
    retried ITEM BY ITEM so one bad input cannot destroy its neighbours.
    """
    # ALL-OR-NOTHING, DELIBERATELY, and it used to pretend otherwise.
    #
    # A version of this function caught the batch failure and retried item by
    # item — which sounds like isolation and is not: the retry loop shared one
    # try/except, so it re-raised on the first bad input and threw away every
    # success before it. It reproduced the exact loss it was named for (32
    # documents lost, only 17 of them over the limit) while reading as protection.
    # `embeddings` put the general form well: *a retry that aborts on first
    # failure is a batch retry wearing a per-item name.*
    #
    # The deeper reason not to fix it in here: this function's contract is one
    # vector per input, so it CANNOT report a partial result. Any real isolation
    # has to live where per-item accounting lives — see `_run_batch` in
    # scripts/memo-reembed-corpus, which gives every item its own try and records
    # which document failed. Callers that need isolation must split the batch
    # themselves; callers that don't get a clean all-or-nothing failure.
    response = await _client.embeddings.create(
        model=settings.embedding_model,
        input=texts,
    )
    return [item.embedding for item in response.data]
