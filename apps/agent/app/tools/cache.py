from __future__ import annotations

import hashlib
import json
import time
from typing import Any

_STORE: dict[str, tuple[float, Any]] = {}
TTL = 60 * 30


def cache_key(*parts: Any) -> str:
    blob = json.dumps(parts, default=str, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()


def get(key: str) -> Any | None:
    row = _STORE.get(key)
    if not row:
        return None
    exp, value = row
    if exp < time.time():
        _STORE.pop(key, None)
        return None
    return value


def put(key: str, value: Any, ttl: int = TTL) -> Any:
    _STORE[key] = (time.time() + ttl, value)
    return value
