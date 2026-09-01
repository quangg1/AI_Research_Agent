from app.domain.schema import Budget
from app.graph.nodes.extract import extract_node


Q = "How does Punica serve LoRA adapters on GPU with kernel-level efficiency?"


def test_extract_runs_gap_micro_loop(monkeypatch):
    fetched = {
        "url": "https://arxiv.org/abs/2310.1",
        "title": "LoRAX",
        "full_text": (
            "Punica SGMV kernel batches thousands of LoRA adapters on GPU memory with "
            "segmented matrix multiplication and CUDA kernel optimizations for latency."
        ),
        "snippet": "Punica SGMV kernel batches LoRA adapters",
    }

    def fake_fetch(url: str, title: str = ""):
        return {**fetched, "url": url, "title": title or fetched["title"]}

    monkeypatch.setattr("app.domain.gap_enrich.evidence_from_url", fake_fetch)

    state = {
        "query": Q,
        "brief": {"depth": "deep"},
        "budget": Budget(max_tool_calls=24, max_iterations=5).model_dump(mode="json"),
        "retrieved": [
            {
                "id": "thin",
                "url": "https://arxiv.org/abs/2310.1",
                "title": "LoRAX",
                "snippet": "Punica kernel mention",
                "tier": "peer_reviewed",
                "credibility": 0.85,
            },
            {
                "id": "support",
                "url": "https://github.com/punica-ai/punica",
                "title": "punica",
                "snippet": "SGMV CUDA implementation for multi-tenant LoRA serving",
                "tier": "specialist_research",
                "credibility": 0.8,
            },
            {
                "id": "support2",
                "url": "https://arxiv.org/abs/2310.2",
                "title": "batching",
                "snippet": "GPU batching for LoRA adapters memory efficiency",
                "tier": "peer_reviewed",
                "credibility": 0.8,
            },
        ],
    }
    out = extract_node(state)
    trace = out["traces"][0]
    assert trace.get("gap_micro_fetched", 0) >= 1
    assert out.get("gap_micro_retries") == 1
    assert out["brief"]["must_answer"]
    assert (trace.get("depth_score") or 0) >= 0
