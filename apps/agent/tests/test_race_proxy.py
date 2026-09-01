from app.eval.race_proxy import score_race_proxy


def test_race_proxy_scores_complete_memo():
    md = """# vLLM batching

## At a glance
Continuous batching improves throughput.

## Executive summary
vLLM uses continuous batching to merge requests without waiting for full batches, reducing time-to-first-token latency for LoRA adapter workloads.

## Key findings
1. Scheduler merges prefill and decode [1]

## Detailed analysis
### Mechanism
PagedAttention stores KV cache in non-contiguous blocks.

### Bottlenecks
Kernel launch overhead dominates at small batch sizes.

## Decision rule
Prefer continuous batching when concurrent requests exceed eight.

## References
[1] vLLM paper
"""
    s = score_race_proxy(
        "How does vLLM batch requests for LoRA adapters?",
        md,
        critic={"depth_score": {"score": 72}},
    )
    assert s["overall_proxy"] >= 55
    assert s["readability"] >= 70
    assert not s["missing_sections"]


def test_race_proxy_penalizes_truncation():
    md = "# Title\n\n## Executive summary\n\nShort."
    s = score_race_proxy("Compare RAG vs fine-tune", md)
    assert s["overall_proxy"] < 55
    assert s["missing_sections"]
