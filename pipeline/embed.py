"""Semantic vector embeddings for lines and queries.

Uses Google's text-embedding-004 (768 dimensions) when an API key is available.
Graceful offline fallback: returns empty vectors [] if no key is present or network
is unavailable, ensuring ingest and search NEVER crash.
"""

from __future__ import annotations

import logging
import os
from typing import Sequence

logger = logging.getLogger("pipeline.embed")

MODEL = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")
DIMENSIONS = 768


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def available() -> bool:
    return bool(api_key())


_client = None


def get_client():
    global _client
    if _client is not None:
        return _client
    key = api_key()
    if not key:
        return None
    try:
        from google import genai

        _client = genai.Client(api_key=key)
        return _client
    except Exception as error:
        logger.warning("Could not initialize google-genai client: %s", error)
        return None


def embed_text(text: str) -> list[float]:
    """Computes a 768-dim float vector for a single string.

    Returns [] if embedding is unavailable or fails.
    """
    clean = text.strip()
    if not clean:
        return []
    client = get_client()
    if client is None:
        return []
    try:
        response = client.models.embed_content(
            model=MODEL,
            contents=clean,
            config={"output_dimensionality": DIMENSIONS},
        )
        if hasattr(response, "embedding") and response.embedding:
            vals = response.embedding.values or []
            return [float(v) for v in vals]
        if hasattr(response, "embeddings") and response.embeddings:
            vals = response.embeddings[0].values or []
            return [float(v) for v in vals]
    except Exception as error:
        logger.warning("Failed to embed text %r: %s", clean[:40], error)
    return []


def embed_batch(texts: Sequence[str]) -> list[list[float]]:
    """Computes embeddings for a batch of strings.

    Returns a list of vectors matching the length of `texts`.
    """
    if not texts:
        return []
    client = get_client()
    if client is None:
        return [[] for _ in texts]

    results: list[list[float]] = []
    batch_size = 64
    for i in range(0, len(texts), batch_size):
        chunk = [t.strip() for t in texts[i : i + batch_size]]
        try:
            response = client.models.embed_content(
                model=MODEL,
                contents=chunk,
                config={"output_dimensionality": DIMENSIONS},
            )
            embeddings = getattr(response, "embeddings", None) or []
            for item in embeddings:
                vals = getattr(item, "values", None) or []
                results.append([float(v) for v in vals])
            while len(results) < min(i + batch_size, len(texts)):
                results.append([])
        except Exception as error:
            logger.warning("Failed to embed batch of %d items: %s", len(chunk), error)
            for _ in chunk:
                results.append([])

    return results


def embed_take_lines(take: dict) -> dict[int, list[float]]:
    """Extracts line texts from a take dictionary and returns line_id -> vector."""
    lines = take.get("lines", [])
    if not lines or not available():
        return {}

    line_ids: list[int] = []
    line_texts: list[str] = []
    for line in lines:
        lid = int(line["line_id"])
        text = line.get("text") or " ".join(w["word"] for w in line.get("words", []))
        line_ids.append(lid)
        line_texts.append(text)

    vectors = embed_batch(line_texts)
    return {lid: vec for lid, vec in zip(line_ids, vectors) if vec}
