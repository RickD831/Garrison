"""
tools/rag_tool.py — Semantic log history search via Qdrant

Tool: search_log_history
Used for queries that imply historical context ("last week", "yesterday",
"has this happened before", etc.)
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)

QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
COLLECTION = "host_logs"


def search_log_history(query: str, host_filter: str | None = None, k: int = 6) -> str:
    """
    Semantic search over indexed log history in Qdrant.

    Args:
        query: Natural language query, e.g. "authentication failures on appserver01"
        host_filter: Optional hostname to restrict search to one host
        k: Number of results to return

    Returns:
        Formatted string of matching log chunks with source metadata
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue
    except ImportError:
        return "Qdrant client not installed. Run: pip install qdrant-client"

    try:
        vector = _embed(query)
    except Exception as e:
        return f"Embedding failed — is Ollama running? Error: {e}"

    try:
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    except Exception as e:
        return f"Cannot connect to Qdrant at {QDRANT_HOST}:{QDRANT_PORT} — {e}"

    search_filter = None
    if host_filter:
        search_filter = Filter(
            must=[FieldCondition(key="host", match=MatchValue(value=host_filter))]
        )

    try:
        results = client.search(
            collection_name=COLLECTION,
            query_vector=vector,
            query_filter=search_filter,
            limit=k,
            with_payload=True,
        )
    except Exception as e:
        return f"Qdrant search failed: {e}"

    if not results:
        return "No matching log history found."

    parts = []
    for i, hit in enumerate(results, 1):
        payload = hit.payload or {}
        host = payload.get("host", "unknown")
        os_type = payload.get("os", "")
        collected_at = payload.get("collected_at", "")
        text = payload.get("text", "")
        parts.append(
            f"[{i}] Host: {host} ({os_type})  Collected: {collected_at}  Score: {hit.score:.3f}\n"
            f"{text}\n"
        )

    return "\n".join(parts)


def _embed(text: str) -> list[float]:
    """Embed text using nomic-embed-text via Ollama."""
    import urllib.request
    import json

    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["embedding"]
