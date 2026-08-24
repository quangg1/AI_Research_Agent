from types import SimpleNamespace

from app.llm.client import CreditsExhaustedError
from app.runtime import _credits_interrupt_frame, _looks_like_credits_error


def test_looks_like_credits_error():
    assert _looks_like_credits_error("gemini is out of credits. Fallback failed")
    assert not _looks_like_credits_error("connection reset by peer")


def test_credits_interrupt_frame_parks_awaiting_human():
    snap = SimpleNamespace(
        values={"status": "researching", "started_at": 1.0, "traces": [{"node": "enrich"}]},
        next=("retrieve",),
        tasks=[],
        config={"configurable": {"checkpoint_id": "cp-1"}},
    )
    exc = CreditsExhaustedError("gemini", "platform", tried=["gemini", "openai", "grok"])
    frame = _credits_interrupt_frame("run-1", "exec-1", snap, exc)
    assert frame["type"] == "interrupt"
    assert frame["data"]["type"] == "credits_exhausted"
    assert frame["data"]["failed_node"] == "retrieve"
    assert frame["snapshot"]["status"] == "awaiting_human"
    assert "Continue" in frame["data"]["resume_hint"] or "resumes" in frame["data"]["resume_hint"]
