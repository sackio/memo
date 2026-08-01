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
async def embed(text: str) -> list[float]:
    response = await _client.embeddings.create(
        model=settings.embedding_model,
        input=text,
    )
    return response.data[0].embedding


async def embed_batch(texts: list[str]) -> list[list[float]]:
    response = await _client.embeddings.create(
        model=settings.embedding_model,
        input=texts,
    )
    return [item.embedding for item in response.data]
