"""Embedding utility functions using sentence-transformers all-MiniLM-L6-v2.

Provides lazy-loaded singleton access to the embedding model and convenience
functions for single and batch text embedding. Used by components that need
vector embeddings outside of ChromaDB (e.g., direct similarity computations).

Model: all-MiniLM-L6-v2 — 384-dimensional, optimized for short texts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    """Return the shared sentence-transformers model, loading on first call."""
    global _model  # noqa: PLW0603
    if _model is None:
        from sentence_transformers import (
            SentenceTransformer as SentenceTransformerModel,
        )

        _model = SentenceTransformerModel("all-MiniLM-L6-v2")
    return _model


def embed_text(text: str) -> list[float]:
    """Encode a single text string into a 384-dim embedding vector."""
    model = get_embedding_model()
    return model.encode(text).tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Encode a batch of text strings into embedding vectors."""
    model = get_embedding_model()
    result: list[list[float]] = model.encode(texts).tolist()
    return result
