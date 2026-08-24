from __future__ import annotations

import json
import logging
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("kiln")


def event(name: str, **payload: Any) -> None:
    from app.llm.redact import scrub_obj

    logger.info("event=%s %s", name, json.dumps(scrub_obj(payload), default=str)[:4000])
