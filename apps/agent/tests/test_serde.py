from app.domain.routing_policy import heuristic_plan
from app.graph.serde import dump


def test_plan_dump_is_plain_json():
    plan = dump(heuristic_plan("What is continuous batching in vLLM?", 8))
    assert plan["query_type"] == "factual"
    assert all(isinstance(a, str) for a in plan["agents_to_run"])
    assert all(isinstance(s["agent"], str) for s in plan["sub_queries"])
