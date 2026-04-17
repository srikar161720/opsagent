"""Tests for embedding utility functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from src.knowledge_base import embeddings


@pytest.fixture(autouse=True)
def _reset_model() -> None:
    """Reset the module-level singleton before each test."""
    embeddings._model = None


class TestEmbeddings:
    """Tests for the sentence-transformers embedding wrapper."""

    def test_lazy_load_singleton(self) -> None:
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.0])

        # Inject mock directly to avoid importing sentence_transformers
        embeddings._model = mock_model

        model1 = embeddings.get_embedding_model()
        model2 = embeddings.get_embedding_model()
        assert model1 is model2
        assert model1 is mock_model

    def test_embed_text_returns_list_of_floats(self) -> None:
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1, 0.2, 0.3])
        embeddings._model = mock_model

        result = embeddings.embed_text("hello world")

        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)
        assert len(result) == 3

    def test_embed_batch_returns_list_of_lists(self) -> None:
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1, 0.2], [0.3, 0.4]])
        embeddings._model = mock_model

        result = embeddings.embed_batch(["hello", "world"])

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(row, list) for row in result)

    def test_embed_text_calls_encode(self) -> None:
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.0])
        embeddings._model = mock_model

        embeddings.embed_text("test input")

        mock_model.encode.assert_called_once_with("test input")

    def test_embed_batch_calls_encode(self) -> None:
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.0]])
        embeddings._model = mock_model

        texts = ["a", "b", "c"]
        embeddings.embed_batch(texts)

        mock_model.encode.assert_called_once_with(texts)
