"""Runbook knowledge base search tool for the OpsAgent investigation agent.

Wraps the ChromaDB-backed RunbookIndexer to retrieve relevant
troubleshooting documentation via semantic similarity search.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from src.knowledge_base.runbook_indexer import RunbookIndexer

logger = logging.getLogger(__name__)


@tool
def search_runbooks(
    query: str,
    top_k: int = 3,
) -> dict:
    """Search the runbook knowledge base for relevant troubleshooting guidance.

    Use this tool near the END of the investigation once the root cause is
    identified.  Retrieves documentation on known failure modes, remediation
    steps, and operational procedures for the identified issue type.

    Args:
        query: Natural language description of the issue being investigated.
            Examples:
              "Redis connection pool exhaustion in cartservice"
              "database connection timeout causing cascading failure"
              "memory leak gradual performance degradation"
        top_k: Number of runbook results to retrieve.  Default 3.

    Returns:
        dict with key 'results': list of dicts each containing title,
        content, relevance_score, and source.
    """
    try:
        indexer = RunbookIndexer()
        raw_results = indexer.search(query, top_k=top_k)
        return {
            "results": [
                {
                    "title": r["source"],
                    "content": r["content"],
                    "relevance_score": r["relevance_score"],
                    "source": r["source"],
                }
                for r in raw_results
            ]
        }
    except Exception:
        logger.exception("search_runbooks failed")
        return {"results": [], "error": "Failed to search runbooks"}
