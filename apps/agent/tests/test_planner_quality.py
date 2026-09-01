from app.domain.schema import AgentName, Budget
from app.graph.nodes.enrich import _enrich_cap, _prioritize_enrich_urls
from app.graph.nodes.planner import _merge_subqueries, _planner_sync, _reserve_calls
from app.domain.adversarial import falsification_queries
from app.domain.decompose import subquestions_for


Q = (
    "How does Punica/LoRAX efficiently serve thousands of LoRA adapters in the same batch "
    "without blowing GPU memory, and what are the kernel-level bottlenecks?"
)


def test_iter1_merges_slot_queries_with_falsification():
    slots = subquestions_for(Q, 8)
    fals = falsification_queries(Q, {"depth": "deep"})
    merged = _merge_subqueries(slots, fals)
    assert len(merged) >= len(slots)
    labels = " ".join(s.question.lower() for s in merged)
    assert "contrary" in labels or "counter" in labels or "benchmark" in labels
    assert any("mechanism" in s.rationale.lower() or "dimension" in s.rationale.lower() for s in merged)


def test_deep_first_pass_reserves_budget_for_critic_loop():
    assert _reserve_calls("deep", 1) == 10
    assert _reserve_calls("deep", 2) == 0


def test_planner_iter1_uses_slot_aligned_followups(monkeypatch):
    monkeypatch.setattr("app.graph.nodes.planner.corpus_available", lambda: False)
    state = {
        "query": Q,
        "brief": {"depth": "deep"},
        "budget": Budget(max_iterations=5, max_tool_calls=52).model_dump(mode="json"),
        "followups": [],
    }
    out = _planner_sync(state)
    plan = out["plan"]
    subs = plan.get("sub_queries") or []
    assert len(subs) <= 12
    assert len(subs) >= 3
    rationales = " ".join(s.get("rationale", "") for s in subs).lower()
    assert "dimension" in rationales or "must-answer" in rationales or "cover" in rationales


def test_enrich_cap_limits_first_pass():
    assert _enrich_cap("deep", 1) == 10
    assert _enrich_cap("deep", 2) == 18


def test_enrich_prioritizes_primary_urls():
    evidence = [
        {"url": "https://medium.com/x", "snippet": "blog"},
        {"url": "https://arxiv.org/abs/2401.1", "snippet": "paper"},
        {"url": "https://github.com/org/repo", "snippet": "code"},
    ]
    ordered = _prioritize_enrich_urls(evidence, [e["url"] for e in evidence])
    assert ordered[0].startswith("https://arxiv.org")
    assert "github.com" in ordered[1]
