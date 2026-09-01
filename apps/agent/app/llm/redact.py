from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.llm.providers import split_api_keys

_SECRET_KEYS = {
    "apikey",
    "api_key",
    "authorization",
    "x-api-key",
    "x-agent-key",
    "secret",
    "password",
    "token",
}

_KEY_SHAPE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{10,}|AIza[A-Za-z0-9_-]{10,}|xai-[A-Za-z0-9_-]{10,}"
    r"|tvly-[A-Za-z0-9_-]{10,}|hf_[A-Za-z0-9_-]{10,}|Bearer\s+\S+)"
)


def platform_secrets() -> tuple[str, ...]:
    secrets: list[str] = []
    for raw in (
        settings.google_api_key,
        settings.openai_api_key,
        settings.xai_api_key,
        settings.grok_api_key,
        settings.tavily_api_key,
    ):
        secrets.extend(split_api_keys(raw))
    return tuple(secrets)


def scrub_text(text: str, extra: tuple[str, ...] = ()) -> str:
    if not text:
        return text
    out = text
    for secret in (*extra, *platform_secrets()):
        if secret:
            out = out.replace(secret, "***")
    return _KEY_SHAPE.sub("***", out)


def scrub_obj(obj: Any, extra: tuple[str, ...] = ()) -> Any:
    if isinstance(obj, str):
        return scrub_text(obj, extra)
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for key, value in obj.items():
            name = str(key).lower().replace("-", "_")
            if name in _SECRET_KEYS or "api_key" in name or name.endswith("apikey"):
                cleaned[str(key)] = "***"
            else:
                cleaned[str(key)] = scrub_obj(value, extra)
        return cleaned
    if isinstance(obj, (list, tuple)):
        return [scrub_obj(item, extra) for item in obj]
    return obj
