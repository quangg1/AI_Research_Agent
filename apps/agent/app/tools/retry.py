from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from app.observability.logging import logger

T = TypeVar("T")


def retry_call(fn: Callable[[], T], *, attempts: int = 3, delay: float = 0.4, default: T | None = None) -> T | None:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
            logger.warning("retry %s/%s failed: %s", i + 1, attempts, exc)
            time.sleep(delay * (i + 1))
    if last:
        logger.warning("retry_exhausted %s", last)
    return default
