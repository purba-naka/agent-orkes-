from collections.abc import Sequence
import hashlib
import math
import re

from litellm import aembedding
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import settings
from orchestrator.db.models import ModelRevision
from orchestrator.domain.catalog import CatalogService

_TOKEN_PATTERN = re.compile(r"[\w'-]+", re.UNICODE)


class EmbeddingError(ValueError):
    pass


def deterministic_embedding(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    for token in _TOKEN_PATTERN.findall(text.casefold()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % dimensions
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[index] += sign
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


async def embed_texts(
    session: AsyncSession, revision: ModelRevision, texts: Sequence[str]
) -> list[list[float]]:
    dimensions = settings.retrieval_embedding_dimensions
    configured = int(
        revision.parameters.get(
            "dimensions", revision.parameters.get("embedding_dimensions", dimensions)
        )
    )
    if configured != dimensions:
        raise EmbeddingError(f"Embedding model dimension must be {dimensions}")
    if revision.provider in {"fake", "test"}:
        return [deterministic_embedding(text, dimensions) for text in texts]

    model = (
        revision.model_name
        if revision.model_name.startswith(f"{revision.provider}/")
        else f"{revision.provider}/{revision.model_name}"
    )
    kwargs = {
        key: value
        for key, value in revision.parameters.items()
        if key not in {"dimensions", "embedding_dimensions"}
    }
    if revision.base_url:
        kwargs["api_base"] = revision.base_url
    if revision.api_key_id:
        kwargs["api_key"] = await CatalogService.get_decrypted_credential(
            session, revision.api_key_id
        )
    try:
        response = await aembedding(model=model, input=list(texts), **kwargs)
        embeddings = [list(item["embedding"]) for item in response.data]
    except Exception as exc:
        raise EmbeddingError("Embedding provider request failed") from exc
    if len(embeddings) != len(texts) or any(
        len(embedding) != dimensions for embedding in embeddings
    ):
        raise EmbeddingError(
            f"Embedding provider must return {dimensions}-dimension vectors"
        )
    return embeddings
