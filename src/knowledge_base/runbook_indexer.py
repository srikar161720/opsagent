"""Index runbook markdown files into ChromaDB for vector similarity search.

Used by the agent's search_runbooks tool to retrieve relevant
troubleshooting documentation given a natural language issue description.

Embedding model: all-MiniLM-L6-v2 (fast, 384-dim, strong for short texts)
ChromaDB persistence: data/chromadb/ (survives restarts)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions


class RunbookIndexer:
    """ChromaDB-backed vector search over runbook markdown files.

    Chunks markdown content at paragraph boundaries and indexes each chunk
    with ``all-MiniLM-L6-v2`` embeddings for semantic retrieval.
    """

    def __init__(self, persist_directory: str = "data/chromadb") -> None:
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        self.collection = self.client.get_or_create_collection(
            name="runbooks",
            embedding_function=self.embedding_fn,  # type: ignore[arg-type]
            metadata={"description": "OpsAgent runbook knowledge base"},
        )

    def index_file(self, file_path: str, chunk_size: int = 500) -> int:
        """Index a markdown runbook file, split into paragraph-level chunks.

        Args:
            file_path:  Path to the .md runbook file.
            chunk_size: Target character size per chunk (soft limit).

        Returns:
            Number of chunks indexed.
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        chunks = self._chunk_content(content, chunk_size)

        for i, chunk in enumerate(chunks):
            doc_id = hashlib.md5(f"{path.name}_{i}".encode()).hexdigest()
            self.collection.upsert(
                ids=[doc_id],
                documents=[chunk],
                metadatas=[
                    {
                        "source": path.name,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                    }
                ],
            )
        return len(chunks)

    def index_directory(self, directory: str) -> int:
        """Index all .md files in a directory recursively."""
        total = 0
        for md_file in sorted(Path(directory).glob("**/*.md")):
            n = self.index_file(str(md_file))
            print(f"Indexed {md_file.name}: {n} chunks")
            total += n
        return total

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """Retrieve the most relevant runbook chunks for a query.

        Returns:
            List of dicts: {content, source, relevance_score}
            Sorted by relevance_score descending.
        """
        results = self.collection.query(query_texts=[query], n_results=top_k)
        documents = results.get("documents")
        metadatas = results.get("metadatas")
        distances = results.get("distances")

        if not documents or not documents[0]:
            return []
        if not metadatas or not distances:
            return []

        return [
            {
                "content": doc,
                "source": meta["source"],
                "relevance_score": round(1.0 - dist, 4),
            }
            for doc, meta, dist in zip(
                documents[0],
                metadatas[0],
                distances[0],
                strict=True,
            )
        ]

    def _chunk_content(self, content: str, chunk_size: int) -> list[str]:
        """Split markdown content at paragraph boundaries, respecting chunk_size."""
        paragraphs = content.split("\n\n")
        chunks: list[str] = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) < chunk_size:
                current += para + "\n\n"
            else:
                if current:
                    chunks.append(current.strip())
                current = para + "\n\n"
        if current:
            chunks.append(current.strip())
        return chunks
