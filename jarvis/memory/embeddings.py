"""Local embeddings for semantic memory recall.

This is real, on-device ML: Ollama already serves `nomic-embed-text` on this
machine (274 MB, CPU/GPU either way, no cloud call, no cost). Recall degrades
honestly rather than pretending: if the model or the Ollama server is not
available, `embed()` returns None and callers fall back to the existing
keyword/overlap scoring in memory/store.py - never a fabricated vector.
"""

from __future__ import annotations

import math
import time

import requests

from jarvis.config import settings

MODEL = "nomic-embed-text"
_TIMEOUT = (0.5, 8)   # tight connect timeout: a down Ollama must fail fast
_EMBED_CACHE_TTL_S = 30.0
_availability_cache: tuple[float, bool] = (0.0, False)


def available() -> bool:
    """Is the embedding model actually callable right now?

    Cached briefly so a hot path (recall on every request) does not repeat a
    network probe; long enough to matter, short enough that Ollama starting
    up mid-session is noticed within half a minute.
    """
    global _availability_cache
    now = time.time()
    if now - _availability_cache[0] < _EMBED_CACHE_TTL_S:
        return _availability_cache[1]
    ok = embed("availability probe") is not None
    _availability_cache = (now, ok)
    return ok


def embed(text: str) -> list[float] | None:
    """One embedding vector for `text`, or None if it could not be computed."""
    text = text.strip()
    if not text:
        return None
    try:
        resp = requests.post(
            f"{settings.OLLAMA_URL}/api/embed",
            json={"model": MODEL, "input": text},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        vectors = resp.json().get("embeddings")
        if not vectors or not vectors[0]:
            return None
        return vectors[0]
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
