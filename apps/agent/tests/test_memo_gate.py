from app.graph.nodes import memo_gate


def test_memo_gate_does_not_skip_when_status_approved_and_draft_exists(monkeypatch):
    """HitL sets approved; report body exists — user must still see publish gate."""
    interrupted: list[dict] = []

    def fake_interrupt(payload):
        interrupted.append(payload)
        return {"action": "publish"}

    monkeypatch.setattr(memo_gate, "interrupt", fake_interrupt)
    monkeypatch.setattr(memo_gate, "_persist_knowledge", lambda _s, _r=None: None)

    out = memo_gate.memo_gate_node(
        {
            "status": "approved",
            "memo_confirmed": False,
            "query": "test-time compute scaling",
            "report": {
                "title": "Draft memo",
                "body_markdown": (
                    "## Executive summary\n\nSubstantive draft body with enough text.\n\n"
                    "## Detailed analysis\n\nEnough analysis text to pass structure validation."
                ),
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


def test_memo_gate_logs_run_token_summary_on_publish(monkeypatch):
    """One clean total-tokens-for-this-run log line on publish, so evaluating
    "how many tokens did this one question cost" doesn't require grepping
    and manually summing every llm_call_ok line by hand."""
    monkeypatch.setattr(memo_gate, "interrupt", lambda _payload: {"action": "publish"})
    monkeypatch.setattr(memo_gate, "_persist_knowledge", lambda _s, _r=None: None)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(memo_gate, "event", lambda name, **kw: events.append((name, kw)))

    memo_gate.memo_gate_node(
        {
            "status": "approved",
            "memo_confirmed": False,
            "query": "test-time compute scaling",
            "thread_id": "run-123",
            "brief": {"depth": "deep"},
            "report": {
                "body_markdown": (
                    "## Executive summary\n\nSubstantive draft body with enough text.\n\n"
                    "## Detailed analysis\n\nEnough analysis text to pass structure validation."
                ),
                "citations": [{"n": 1, "url": "https://arxiv.org/abs/2401.1", "title": "Paper"}],
                "metrics": {"usd_est": 0.041},
            },
            "critic": {},
            "budget": {"used_tokens": 98000, "used_tool_calls": 20, "used_enrich_calls": 18, "used_retrieval_calls": 2, "iterations": 1},
        }
    )
    summaries = [kw for name, kw in events if name == "run_token_summary"]
    assert len(summaries) == 1
    assert summaries[0]["thread_id"] == "run-123"
    assert summaries[0]["used_tokens"] == 98000
    assert summaries[0]["usd_est"] == 0.041


def test_memo_gate_keeps_prior_when_fresh_augment_scores_lower(monkeypatch):
    """Augment mode rewrites the whole memo from a shrunken evidence set with
    no guarantee the rewrite beats what's already stored. Must never show or
    persist a regressed memo — fall back to the prior verbatim and just mark
    it reused, not re-save an inferior copy over it."""
    monkeypatch.setattr(memo_gate, "interrupt", lambda _payload: {"action": "publish"})
    marked: list[str] = []
    monkeypatch.setattr(memo_gate, "mark_reused", lambda kid: marked.append(kid))
    saved_calls: list[tuple] = []
    monkeypatch.setattr(memo_gate, "save_answer", lambda *a, **kw: saved_calls.append((a, kw)))

    prior = {
        "id": "prior-123",
        "version": 3,
        "depth_score": 100,
        "title": "Good prior memo",
        "executive_summary": "Solid prior summary.",
        "body_markdown": "## Executive summary\n\nRich, well-grounded prior body.",
        "decision_rule": "Prior decision rule.",
        "open_questions": ["Prior open question"],
        "limitations": ["Prior limitation"],
        "claims": [{"text": "prior claim"}],
        "citations": [{"n": 1, "url": "https://arxiv.org/abs/2401.1", "title": "Prior paper"}],
    }
    out = memo_gate.memo_gate_node(
        {
            "status": "approved",
            "memo_confirmed": False,
            "query": "single vs multi-agent",
            "reuse_mode": "augment",
            "prior_knowledge": prior,
            "brief": {"depth": "deep"},
            "report": {
                "title": "Thin fresh memo",
                "executive_summary": "Thin fresh summary.",
                "body_markdown": (
                    "## Executive summary\n\nThin fresh body with weak evidence.\n\n"
                    "## Detailed analysis\n\nEnough analysis text to pass structure validation."
                ),
                "decision_rule": "Fresh decision rule.",
                "citations": [{"n": 1, "url": "https://example.com/x", "title": "Weak source"}],
                "metrics": {"depth_score": 55},
            },
            "critic": {},
            "budget": {},
        }
    )
    report = out["report"]
    assert report["body_markdown"] == prior["body_markdown"]
    assert report["title"] == prior["title"]
    assert report["metrics"]["knowledge_id"] == "prior-123"
    assert report["metrics"]["augment_kept_prior"] is True
    assert marked == ["prior-123"]
    assert saved_calls == []


def test_memo_gate_keeps_fresh_when_augment_beats_prior(monkeypatch):
    monkeypatch.setattr(memo_gate, "interrupt", lambda _payload: {"action": "publish"})
    monkeypatch.setattr(memo_gate, "_persist_knowledge", lambda _s, _r=None: None)
    prior = {"id": "prior-123", "depth_score": 40, "body_markdown": "Old thin body."}
    out = memo_gate.memo_gate_node(
        {
            "status": "approved",
            "memo_confirmed": False,
            "query": "single vs multi-agent",
            "reuse_mode": "augment",
            "prior_knowledge": prior,
            "report": {
                "body_markdown": (
                    "## Executive summary\n\nBetter, richer fresh body with more evidence.\n\n"
                    "## Detailed analysis\n\nEnough analysis text to pass structure validation."
                ),
                "citations": [{"n": 1, "url": "https://arxiv.org/abs/2401.1", "title": "New paper"}],
                "metrics": {"depth_score": 80},
            },
            "critic": {},
            "budget": {},
        }
    )
    assert out["report"]["body_markdown"].startswith("## Executive summary\n\nBetter")
    assert "augment_kept_prior" not in out["report"]["metrics"]


def test_memo_gate_skips_when_already_confirmed():
    out = memo_gate.memo_gate_node(
        {
            "status": "approved",
            "memo_confirmed": True,
            "report": {"body_markdown": "# Memo\n\nBody."},
        }
    )
    assert out["traces"][0].get("skipped") == "confirmed"
