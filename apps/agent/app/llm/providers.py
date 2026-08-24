from __future__ import annotations

from typing import Literal

LlmProvider = Literal["gemini", "openai", "grok"]

PROVIDERS: tuple[LlmProvider, ...] = ("gemini", "openai", "grok")

DEFAULT_MODELS: dict[LlmProvider, str] = {
    "gemini": "gemini-3.6-flash",
    "openai": "gpt-4.1-mini",
    "grok": "grok-4.6",
}

OPENAI_COMPAT_BASE: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "grok": "https://api.x.ai/v1",
}

PROVIDER_DOCS: dict[LlmProvider, str] = {
    "gemini": "https://aistudio.google.com/apikey",
    "openai": "https://platform.openai.com/api-keys",
    "grok": "https://console.x.ai/",
}

KEY_DEAD_MARKERS = (
    "invalid_api_key",
    "invalid api key",
    "incorrect api key",
    "api key not valid",
    "api_key_invalid",
    "permission_denied",
    "unauthenticated",
    "unauthorized",
    "api key expired",
)


def split_api_keys(raw: str | None) -> list[str]:
    """Split `key1;key2` pools. Order is kept; blanks and duplicates are dropped."""
    out: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").split(";"):
        key = part.strip()
        if len(key) >= 8 and key not in seen:
            seen.add(key)
            out.append(key)
    return out


CREDIT_MARKERS = (
    "insufficient_quota",
    "insufficient_credits",
    "quota_exceeded",
    "quota exceeded",
    "exceeded your current quota",
    "billing_hard_limit",
    "credit balance",
    "out of credits",
    "payment required",
    "check quota",
    "free tier",
    "generativelanguage.googleapis.com/generate_content_free_tier",
)

# Gemini uses RESOURCE_EXHAUSTED for daily quota AND RPM. Fail over either way.
RATE_LIMIT_ONLY_MARKERS = (
    "rate_limit_exceeded",
    "rate limit",
    "too many requests",
    "try again later",
)


def _blob(body: str) -> str:
    return (body or "").lower()


def is_credits_error(status: int | None, body: str) -> bool:
    """True only for billing/quota exhaustion — not RPM rate limits."""
    if status == 402:
        return True
    blob = _blob(body)
    # OpenAI/xAI: rate_limit_exceeded is RPM/TPM, not empty wallet.
    if "rate_limit_exceeded" in blob or "rate limit reached" in blob:
        return False
    if any(marker in blob for marker in CREDIT_MARKERS):
        return True
    # Gemini free-tier / project quota almost always arrives as RESOURCE_EXHAUSTED.
    if "resource_exhausted" in blob or "resource exhausted" in blob:
        return True
    return False


def is_unusable_key(status: int | None, body: str) -> bool:
    if status in {401, 403}:
        return True
    blob = _blob(body)
    return any(marker in blob for marker in KEY_DEAD_MARKERS)


def is_retryable_slot_error(status: int | None, body: str) -> bool:
    if status in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    blob = _blob(body)
    return any(
        marker in blob
        for marker in (
            *RATE_LIMIT_ONLY_MARKERS,
            "resource_exhausted",
            "resource exhausted",
            "unavailable",
            "overloaded",
        )
    )
