from app.runtime import PIPELINE, _annotate, _next_node_after_resume


def test_annotate_planner_progress():
    run = {
        "status": "running",
        "started_at": 0,
        "current_node": "planner",
        "values": {"traces": [{"node": "briefing"}]},
    }
    out = _annotate(run)
    assert out["current_node"] == "planner"
    assert 0 < out["progress"] < 0.3
    assert "Gemini" in out["hint"]
    assert out["eta_s"] > 0


def test_annotate_completed_is_full():
    traces = [{"node": n} for n in PIPELINE]
    out = _annotate({"status": "completed", "current_node": "report", "values": {"traces": traces}})
    assert out["progress"] == 1.0


def test_resume_after_brief_goes_to_planner():
    nxt = _next_node_after_resume({"interrupt": {"type": "research_brief"}}, {"action": "start"})
    assert nxt == "planner"
    assert _next_node_after_resume({"interrupt": {"type": "approve_report"}}, {"action": "approve"}) == "report"
    assert _next_node_after_resume({"interrupt": {"type": "approve_report"}}, {"action": "revise"}) == "planner"
