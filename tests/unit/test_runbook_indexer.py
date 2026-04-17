"""Tests for RunbookIndexer ChromaDB search."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.knowledge_base.runbook_indexer import RunbookIndexer


class TestChunkContent:
    """Tests for the paragraph-based chunking logic."""

    def test_chunk_content_splits_paragraphs(self) -> None:
        content = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        indexer_cls = RunbookIndexer.__new__(RunbookIndexer)
        chunks = indexer_cls._chunk_content(content, chunk_size=500)
        assert len(chunks) == 1
        assert "Paragraph one." in chunks[0]
        assert "Paragraph three." in chunks[0]

    def test_chunk_content_respects_size(self) -> None:
        content = "A" * 100 + "\n\n" + "B" * 100 + "\n\n" + "C" * 100
        indexer_cls = RunbookIndexer.__new__(RunbookIndexer)
        chunks = indexer_cls._chunk_content(content, chunk_size=150)
        assert len(chunks) >= 2

    def test_chunk_content_single_paragraph(self) -> None:
        content = "Just one paragraph with no double newlines."
        indexer_cls = RunbookIndexer.__new__(RunbookIndexer)
        chunks = indexer_cls._chunk_content(content, chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_chunk_content_empty(self) -> None:
        indexer_cls = RunbookIndexer.__new__(RunbookIndexer)
        chunks = indexer_cls._chunk_content("", chunk_size=500)
        assert len(chunks) <= 1


def _make_mock_indexer() -> tuple[RunbookIndexer, MagicMock]:
    """Create a RunbookIndexer with mocked ChromaDB internals."""
    indexer = RunbookIndexer.__new__(RunbookIndexer)
    mock_collection = MagicMock()
    indexer.client = MagicMock()
    indexer.embedding_fn = MagicMock()
    indexer.collection = mock_collection
    return indexer, mock_collection


class TestRunbookIndexer:
    """Tests for indexing and search with mocked ChromaDB."""

    @patch("src.knowledge_base.runbook_indexer.chromadb")
    @patch("src.knowledge_base.runbook_indexer.embedding_functions")
    def test_init_creates_collection(
        self,
        mock_ef: MagicMock,
        mock_chromadb: MagicMock,
    ) -> None:
        mock_client = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client
        RunbookIndexer(persist_directory="/tmp/test_chromadb")
        mock_client.get_or_create_collection.assert_called_once()
        call_kwargs = mock_client.get_or_create_collection.call_args
        assert call_kwargs.kwargs.get("name") == "runbooks"

    def test_index_file_returns_chunk_count(self, tmp_path: Path) -> None:
        indexer, mock_collection = _make_mock_indexer()
        md_file = tmp_path / "test.md"
        md_file.write_text("Para 1.\n\nPara 2.\n\nPara 3.")

        count = indexer.index_file(str(md_file), chunk_size=500)

        assert count >= 1
        assert mock_collection.upsert.call_count == count

    def test_index_file_calls_upsert(self, tmp_path: Path) -> None:
        indexer, mock_collection = _make_mock_indexer()
        md_file = tmp_path / "runbook.md"
        md_file.write_text("Short content.")

        indexer.index_file(str(md_file))

        mock_collection.upsert.assert_called_once()

    def test_search_returns_formatted_results(self) -> None:
        indexer, mock_collection = _make_mock_indexer()
        mock_collection.query.return_value = {
            "documents": [["chunk content here"]],
            "metadatas": [
                [
                    {
                        "source": "test.md",
                        "chunk_index": 0,
                        "total_chunks": 1,
                    }
                ]
            ],
            "distances": [[0.25]],
        }

        results = indexer.search("test query", top_k=1)

        assert len(results) == 1
        assert results[0]["content"] == "chunk content here"
        assert results[0]["source"] == "test.md"
        assert "relevance_score" in results[0]

    def test_search_relevance_score_calculation(self) -> None:
        indexer, mock_collection = _make_mock_indexer()
        mock_collection.query.return_value = {
            "documents": [["doc1", "doc2"]],
            "metadatas": [
                [
                    {
                        "source": "a.md",
                        "chunk_index": 0,
                        "total_chunks": 1,
                    },
                    {
                        "source": "b.md",
                        "chunk_index": 0,
                        "total_chunks": 1,
                    },
                ]
            ],
            "distances": [[0.1, 0.4]],
        }

        results = indexer.search("query", top_k=2)

        assert results[0]["relevance_score"] == round(1.0 - 0.1, 4)
        assert results[1]["relevance_score"] == round(1.0 - 0.4, 4)

    def test_search_empty_results(self) -> None:
        indexer, mock_collection = _make_mock_indexer()
        mock_collection.query.return_value = {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

        results = indexer.search("no match", top_k=3)
        assert results == []

    def test_index_directory_iterates_md_files(self, tmp_path: Path) -> None:
        indexer, mock_collection = _make_mock_indexer()
        (tmp_path / "a.md").write_text("Content A")
        (tmp_path / "b.md").write_text("Content B")
        (tmp_path / "not_md.txt").write_text("Ignored")

        total = indexer.index_directory(str(tmp_path))

        assert total >= 2
        assert mock_collection.upsert.call_count >= 2
