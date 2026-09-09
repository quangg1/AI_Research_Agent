"""Multi-tenant guardrails."""

from __future__ import annotations

from app.config import settings
from app.graph.state import ResearchState


def missing_org_guard(state: ResearchState) -> dict | None:
    """Fail closed when production requires org_id but run is anonymous."""
    if not settings.require_org_id:
        return None
    if (state.get("org_id") or "").strip():
        return None
    return {
        "status": "failed",
        "traces": [{"node": "tenancy", "error": "org_id_required"}],
    }
