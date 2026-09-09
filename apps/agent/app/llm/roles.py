"""Per-role LLM model selection (Tier 1)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from app.config import settings
from app.llm.client import LLMClient, current_role, get_llm


def model_for_role(role: str) -> str | None:
    """Return role-specific model override, or None to use default."""
    role = (role or "").strip().lower()
    mapping = {
        "critic": settings.gemini_model_critic,
        "planner": settings.gemini_model_planner,
        "briefing": settings.gemini_model_planner,
        "report": settings.gemini_model_report,
        "writer": settings.gemini_model_report,
        "integrity": settings.gemini_model_report,
        "rerank": settings.gemini_model_critic,
    }
    override = (mapping.get(role) or "").strip()
    return override or None


@contextmanager
def use_role_model(client: LLMClient, role: str) -> Iterator[LLMClient]:
    """Temporarily bind a role-specific model on the shared client.

    `client` is normally a concrete LLMClient with a settable `.model`. Call
    sites in the graph nodes instead pass the `llm` singleton, which is a
    proxy whose `.model` is read-only (it forwards to whichever real
    LLMClient is currently bound, platform key or BYOK) — fall back to
    resolving and mutating that real client in that case.
    """
    role_token = current_role.set(role or "")
    try:
        override = model_for_role(role)
        if not override:
            yield client
            return
        target = client
        prev = target.model
        try:
            target.model = override
        except AttributeError:
            target = get_llm()
            prev = target.model
            target.model = override
        try:
            yield client
        finally:
            target.model = prev
    finally:
        current_role.reset(role_token)
