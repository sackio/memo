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


# ⛔ THERE IS DELIBERATELY NO `embed()`. Call `embed_query` or `embed_document`.
#
# Until 2026-08-02 both callers shared one `embed()`, and the query/document
# distinction existed only in the local variable name at each of 25 call sites.
# That is not a stylistic problem: qwen3-embedding is trained for ASYMMETRIC
# retrieval and wants an instruction prefix on the QUERY side only. memo sent bare
# queries everywhere, which cost 20 rank-1 points across the whole corpus (R-10),
# and nothing anywhere errored — a bare query is a perfectly valid embedding of the
# wrong thing.
#
# Both directions of a wrong choice fail SILENTLY:
#   a document routed through embed_query  -> stores a prefixed vector, corrupting
#                                             a corpus row that will never match
#   a query routed through embed_document  -> reproduces the R-10 regression
#
# So the ambiguous function is GONE rather than deprecated. A missing name is a
# NameError at import; a deprecated one is a warning nobody reads. Removing the
# default is what converts ~21 silent misclassifications into ~21 decisions someone
# had to make on purpose. [002/FR-111]

# The form Qwen3-Embedding's own documentation uses for retrieval queries. Held as
# one constant because it is a live variable, not a formality: a sibling measured a
# domain-specific task string at 7/10 against a generic one at 5/10 — with a worse
# tail. Changing this string invalidates R-11's numbers, which were all measured on
# exactly this text.
QUERY_INSTRUCTION = (
    "Instruct: Given a search query, retrieve relevant documents that match the "
    "query\nQuery: {query}"
)


def _needs_query_instruction() -> bool:
    """Only qwen3-family encoders want the prefix.

    ⚠️ Model-conditional, because this is NOT universally correct. Sending an
    instruction prefix to `text-embedding-3-*` just embeds the literal word
    "Instruct:" along with the query and degrades it. v1 runs 3-small and must
    never receive this. Keyed on the configured model rather than on a flag, so
    switching encoders cannot leave the prefix on by accident.
    """
    return "qwen" in (settings.embedding_model or "").lower()


async def embed_query(text: str) -> list[float]:
    """Embed a SEARCH QUERY. Applies the encoder's query-side instruction.

    Use this for anything a caller is searching WITH. If you are embedding
    something being stored, you want `embed_document`.
    """
    if _needs_query_instruction():
        text = QUERY_INSTRUCTION.format(query=text)
    response = await _client.embeddings.create(
        model=settings.embedding_model,
        input=text,
    )
    return response.data[0].embedding


async def embed_document(text: str) -> list[float]:
    """Embed CONTENT BEING STORED. Never prefixed.

    ⛔ Do not "make this consistent" with `embed_query`. Documents are bare on
    purpose — that is what the model expects, and prefixing them would require
    re-embedding the entire corpus to no benefit.

    It is also load-bearing for detection: `memo-verify-provenance` re-embeds
    stored text through this path and compares it to the stored vector, so a
    document that was wrongly sent through `embed_query` fails loudly here. Make
    the two agree about the prefix and both halves of that guard go blind at once.
    """
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
