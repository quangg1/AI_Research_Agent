from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from functools import lru_cache

import numpy as np

from app.config import settings
from app.observability.logging import logger

DIM = 256
_CACHE_MAX = 2048
_embedding_cache: OrderedDict[str, list[float]] = OrderedDict()
_cache_lock = threading.RLock()
_cache_hits = 0
_cache_misses = 0


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts with a bounded, thread-safe content cache.

    The provider/model is part of the key, so switching from the deterministic
    fallback to Gemini cannot return vectors from a different embedding space.
    """
    global _cache_hits, _cache_misses
    if not texts:
        return []
    provider = f"gemini:{settings.gemini_embed_model}" if settings.google_api_key else f"hash:{DIM}"
    keys = [_cache_key(provider, text) for text in texts]
    out: list[list[float] | None] = [None] * len(texts)
    missing: dict[str, str] = {}
    with _cache_lock:
        for i, key in enumerate(keys):
            vector = _embedding_cache.get(key)
            if vector is not None:
                _embedding_cache.move_to_end(key)
                out[i] = vector
                _cache_hits += 1
            else:
                missing.setdefault(key, texts[i])
        _cache_misses += len(missing)
    if missing:
        fresh = _embed_uncached(list(missing.values()))
        fresh_by_key = dict(zip(missing, fresh))
        with _cache_lock:
            for key, vector in fresh_by_key.items():
                _embedding_cache[key] = vector
                _embedding_cache.move_to_end(key)
                while len(_embedding_cache) > _CACHE_MAX:
                    _embedding_cache.popitem(last=False)
            for i, key in enumerate(keys):
                if out[i] is None:
                    # Use this call's result even if a very large batch or a
                    # concurrent writer evicted it before assembly.
                    out[i] = fresh_by_key.get(key) or _embedding_cache.get(key)
    return [list(vector or []) for vector in out]


def _embed_uncached(texts: list[str]) -> list[list[float]]:
    if settings.google_api_key:
        vectors = _gemini_embed(texts)
        if vectors:
            return vectors
    return [_hash_embed(t) for t in texts]


def _cache_key(provider: str, text: str) -> str:
    digest = hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()
    return f"{provider}:{digest}"


def embedding_cache_stats() -> dict[str, int]:
    with _cache_lock:
        return {"size": len(_embedding_cache), "max_size": _CACHE_MAX, "hits": _cache_hits, "misses": _cache_misses}


def clear_embedding_cache() -> None:
    global _cache_hits, _cache_misses
    with _cache_lock:
        _embedding_cache.clear()
        _cache_hits = 0
        _cache_misses = 0


# Gemini's embed_content accepts a list[str] as one batch request (one RPM
# slot, not one per text). The old code called it once per text on a single
# hardcoded key — a 20-text retrieve step meant 20 sequential calls on the
# same key within a couple seconds, tripping that one key's free-tier RPM
# while the other 15+ keys in the pool never touched the embed endpoint at
# all. Batch it, and rotate across the whole key pool on failure.
_EMBED_BATCH_SIZE = 100


def _gemini_embed(texts: list[str]) -> list[list[float]] | None:
    try:
        from app.llm.providers import split_api_keys

        # settings.google_api_key is a ";"-joined pool (see llm/client.py's
        # slot failover) — passing the raw multi-key string straight to
        # genai.Client() sends one garbled, invalid credential and Gemini
        # replies with a confusing 401 ACCESS_TOKEN_TYPE_UNSUPPORTED rather
        # than "bad API key".
        keys = split_api_keys(settings.google_api_key)
        if not keys:
            return None
        clipped = [text[:8000] for text in texts]
        out: list[list[float]] = []
        for start in range(0, len(clipped), _EMBED_BATCH_SIZE):
            chunk = clipped[start : start + _EMBED_BATCH_SIZE]
            vectors = _embed_chunk(chunk, keys)
            if vectors is None:
                return None
            out.extend(vectors)
        return out
    except Exception as exc:
        logger.warning("embed_fallback_hash %s", exc)
        return None


def _embed_chunk(chunk: list[str], keys: list[str]) -> list[list[float]] | None:
    from google import genai

    last_exc: Exception | None = None
    for key in keys:
        try:
            client = genai.Client(api_key=key)
            result = client.models.embed_content(model=settings.gemini_embed_model, contents=chunk)
            embeddings = result.embeddings or []
            if len(embeddings) != len(chunk):
                last_exc = ValueError(f"embed count mismatch: got {len(embeddings)} for {len(chunk)}")
                continue
            return [_l2(list(e.values)) for e in embeddings]
        except Exception as exc:
            last_exc = exc
            logger.warning("embed_key_failed rotating %s", exc)
            continue
    if last_exc:
        raise last_exc
    return None


def _hash_embed(text: str) -> list[float]:
    vec = np.zeros(DIM, dtype=np.float64)
    tokens = text.lower().split()
    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        for i in range(0, 32, 4):
            idx = int.from_bytes(digest[i : i + 4], "little") % DIM
            vec[idx] += 1.0
    return _l2(vec.tolist())


def _l2(vector: list[float]) -> list[float]:
    arr = np.array(vector, dtype=np.float64)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return arr.tolist()
    return (arr / norm).tolist()


@lru_cache(maxsize=1)
def corpus_dim() -> int:
    return DIM


def cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return float(np.dot(a[:n], b[:n]) / (math.sqrt(sum(x * x for x in a[:n])) * math.sqrt(sum(x * x for x in b[:n])) + 1e-9))
