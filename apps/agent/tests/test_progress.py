from app.graph.state import overwrite
from app.runtime import PIPELINE, _annotate, _next_node_after_resume, _unwrap_interrupt


def test_last_execution_id_keeps_the_newer_write():
    assert overwrite("exec-old", "exec-new") == "exec-new"


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
    assert "model" in out["hint"]
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


def test_unwrap_interrupt_nested_langgraph_shapes():
    payload = {"type": "research_brief", "title": "Research plan"}
    assert _unwrap_interrupt(payload)["type"] == "research_brief"
    assert _unwrap_interrupt({"value": payload})["type"] == "research_brief"
    assert _unwrap_interrupt([{"value": payload}])["type"] == "research_brief"
    assert _unwrap_interrupt({"interrupts": [{"value": payload}]})["type"] == "research_brief"


def test_annotate_brief_interrupt_stays_on_briefing():
    out = _annotate(
        {
            "status": "awaiting_human",
            "interrupt": {"type": "research_brief"},
            "values": {"status": "running", "traces": []},
        }
    )
    assert out["current_node"] == "briefing"


def test_annotate_after_approve_shows_report_not_review():
    out = _annotate(
        {
            "status": "running",
            "next": ["report"],
            "values": {
                "status": "approved",
                "traces": [{"node": "hitl", "action": "approve"}],
            },
        }
    )
    assert out["current_node"] == "report"
    assert "memo" in out["hint"].lower() or "writing" in out["hint"].lower()
    assert "review" not in out["hint"].lower()
