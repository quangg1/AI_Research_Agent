from app.graph.nodes import memo_gate


def test_memo_gate_does_not_skip_when_status_approved_and_draft_exists(monkeypatch):
    """HitL sets approved; report body exists — user must still see publish gate."""
    interrupted: list[dict] = []

    def fake_interrupt(payload):
        interrupted.append(payload)
        return {"action": "publish"}

    monkeypatch.setattr(memo_gate, "interrupt", fake_interrupt)
    monkeypatch.setattr(memo_gate, "_persist_knowledge", lambda _s: None)

    out = memo_gate.memo_gate_node(
        {
            "status": "approved",
            "memo_confirmed": False,
            "query": "test-time compute scaling",
            "report": {
                "title": "Draft memo",
                "body_markdown": "## Executive summary\n\nSubstantive draft body with enough text.",
                "executive_summary": "Summary.",
                "decision_rule": "Rule.",
                "citations": [{"n": 1, "url": "https://arxiv.org/abs/2401.1", "title": "Paper"}],
            },
            "critic": {},
            "budget": {},
        }
    )

    assert interrupted, "memo_gate should interrupt for draft review"
    assert out.get("status") == "completed"
    assert out.get("memo_confirmed") is True
    assert out["traces"][0].get("action") == "publish"


def test_memo_gate_skips_when_already_confirmed():
    out = memo_gate.memo_gate_node(
        {
            "status": "approved",
            "memo_confirmed": True,
            "report": {"body_markdown": "# Memo\n\nBody."},
        }
    )
    assert out["traces"][0].get("skipped") == "confirmed"
