from app.scenarios.llm_cost import compare_serving, rag_tradeoff


def test_serving_compare_has_rows():
    out = compare_serving(8_000_000, 2_000_000, 30)
    assert len(out["rows"]) >= 3
    assert out["cheapest"]
    assert "Research" in out["research_query"] or "Compare" in out["research_query"]


def test_rag_tradeoff_bm25_cheaper_than_finetune_weekly():
    out = rag_tradeoff(queries_per_month=10_000, corpus_tokens=500_000, refresh_jobs_per_month=8)
    by_id = {r["id"]: r for r in out["rows"]}
    assert by_id["bm25"]["monthly_usd"] < by_id["finetune"]["monthly_usd"]
