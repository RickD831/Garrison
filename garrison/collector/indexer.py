#!/usr/bin/env python3
"""
collector/indexer.py — Background log collector and embedder

Polls all hosts in agency.yaml every 5 minutes, pulls recent log entries,
chunks them into 20-line blocks, embeds via nomic-embed-text, and writes
to Qdrant with deduplication by MD5 hash.

Run directly: python collector/indexer.py
Or via garrison.sh: ./garrison.sh start (which backgrounds this process)
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python collector/indexer.py` from the garrison/ root
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from core.config import load_config
from core.connection import HostConnection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("garrison.collector")

# ── Configuration ─────────────────────────────────────────────────────────────

POLL_INTERVAL = int(os.environ.get("GARRISON_POLL_INTERVAL", "300"))   # seconds
CHUNK_SIZE     = int(os.environ.get("GARRISON_CHUNK_SIZE", "20"))       # lines per chunk
LOOK_BACK_MINS = int(os.environ.get("GARRISON_LOOKBACK_MINS", "6"))     # slightly more than poll interval

QDRANT_HOST  = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT  = int(os.environ.get("QDRANT_PORT", "6333"))
OLLAMA_URL   = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL  = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
COLLECTION   = "host_logs"
VECTOR_DIM   = 768   # nomic-embed-text output dimension


# ── Qdrant setup ──────────────────────────────────────────────────────────────

def ensure_collection(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        logger.info("Created Qdrant collection '%s'", COLLECTION)


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    import json, urllib.request
    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data["embedding"]


# ── Log fetching ──────────────────────────────────────────────────────────────

def fetch_logs(conn: HostConnection, minutes: int) -> str:
    if conn.os == "linux":
        return conn.run(
            f"journalctl --no-pager --since='-{minutes}min' --output=short-iso -n 2000"
        )
    else:
        return conn.run(
            f"$since = (Get-Date).AddMinutes(-{minutes}); "
            f"Get-WinEvent -FilterHashtable @{{LogName='System','Application','Security'; "
            f"StartTime=$since}} -MaxEvents 2000 -ErrorAction SilentlyContinue "
            f"| Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message "
            f"| Format-List"
        )


def chunk_text(text: str, chunk_size: int) -> list[str]:
    lines = [l for l in text.splitlines() if l.strip()]
    return [
        "\n".join(lines[i : i + chunk_size])
        for i in range(0, len(lines), chunk_size)
        if lines[i : i + chunk_size]
    ]


# ── Main loop ─────────────────────────────────────────────────────────────────

def collect_once(cfg, client: QdrantClient) -> None:
    hosts = cfg.all_hosts()
    logger.info("Polling %d host(s)...", len(hosts))

    for conn in hosts:
        try:
            _index_host(conn, client)
        except Exception as e:
            logger.error("Failed to index %s: %s", conn.name, e)


def _index_host(conn: HostConnection, client: QdrantClient) -> None:
    logger.info("Fetching logs from %s (%s)...", conn.name, conn.os)
    raw = fetch_logs(conn, LOOK_BACK_MINS)

    if not raw.strip():
        logger.info("No new logs from %s", conn.name)
        return

    chunks = chunk_text(raw, CHUNK_SIZE)
    logger.info("%s: %d chunks to index", conn.name, len(chunks))

    collected_at = datetime.now(timezone.utc).isoformat()
    points: list[PointStruct] = []

    for chunk in chunks:
        chunk_hash = hashlib.md5(chunk.encode()).hexdigest()

        # Deduplication: skip if this exact chunk is already in Qdrant
        hits = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=None,
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        # Use payload hash field for dedup
        existing = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=_hash_filter(chunk_hash),
            limit=1,
        )[0]
        if existing:
            continue

        try:
            vector = embed(chunk)
        except Exception as e:
            logger.warning("Embedding failed for chunk on %s: %s", conn.name, e)
            continue

        point_id = int(chunk_hash[:8], 16)  # stable numeric ID from hash prefix
        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "host": conn.name,
                    "os": conn.os,
                    "collected_at": collected_at,
                    "hash": chunk_hash,
                    "text": chunk,
                },
            )
        )

    if points:
        client.upsert(collection_name=COLLECTION, points=points)
        logger.info("Indexed %d new chunk(s) from %s", len(points), conn.name)
    else:
        logger.info("All chunks from %s already indexed", conn.name)


def _hash_filter(chunk_hash: str):
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    return Filter(
        must=[FieldCondition(key="hash", match=MatchValue(value=chunk_hash))]
    )


def main() -> None:
    logger.info("Garrison log collector starting (poll interval: %ds)", POLL_INTERVAL)

    try:
        cfg = load_config()
    except FileNotFoundError as e:
        logger.error("%s", e)
        sys.exit(1)

    try:
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        ensure_collection(client)
    except Exception as e:
        logger.error("Cannot connect to Qdrant at %s:%d — %s", QDRANT_HOST, QDRANT_PORT, e)
        sys.exit(1)

    logger.info("Connected to Qdrant. Starting collection loop.")

    while True:
        try:
            collect_once(cfg, client)
        except Exception as e:
            logger.error("Collection cycle error: %s", e)
        logger.info("Sleeping %ds until next poll...", POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
