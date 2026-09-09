from app.domain.coverage import gap_claims_for_coverage, score_must_answer
from app.domain.evidence_pipeline import access_allows_fetch, expected_fetch_priority
from app.domain.loop_health import assess_loop_health
from app.domain.quant_reconcile import reconcile_quantitative_rows
from app.domain.source_dedup import is_near_duplicate, select_urls_for_enrich


def test_near_duplicate_syndicated_snippets():
    a = {
        "url": "https://news.example/a",
        "title": "Synthetic data benchmarks rise",
        "snippet": "Researchers report synthetic data improves benchmark accuracy by 4.2% on MMLU in recent trials.",
    }
    b = {
        "url": "https://wire.example/b",
        "title": "Synthetic data benchmarks rise - wire",
        "snippet": "Researchers report synthetic data improves benchmark accuracy by 4.2% on MMLU in recent trials.",
    }
    assert is_near_duplicate(b, [a])


def test_select_urls_drops_duplicates_and_doi_identity():
    evidence = [
        {"url": "https://news.example/a", "title": "A", "snippet": "alpha beta gamma delta epsilon zeta eta theta"},
        {"url": "https://wire.example/b", "title": "B", "snippet": "alpha beta gamma delta epsilon zeta eta theta"},
        {"url": "https://arxiv.org/abs/2401.00001", "title": "Paper", "snippet": "accuracy 71% on SWE-bench"},
        {"url": "https://doi.org/10.1/x", "title": "DOI only", "snippet": "title"},
    ]
    urls = [e["url"] for e in evidence]
    kept, stats = select_urls_for_enrich(evidence, urls)
    assert stats["dropped_duplicate"] >= 1
    assert stats["dropped_access"] >= 1
    assert "https://arxiv.org/abs/2401.00001" in kept
    assert "https://doi.org/10.1/x" not in kept


def test_access_is_hard_gate_not_additive_boost():
    doi_ev = {"url": "https://doi.org/10.1/x", "snippet": "great paper"}
    arxiv_ev = {
        "url": "https://arxiv.org/abs/1",
        "search_snippet": "accuracy 62% on MMLU",
    }
    assert not access_allows_fetch(doi_ev["url"], doi_ev)
    assert expected_fetch_priority(doi_ev, doi_ev["url"]) < 0
    assert expected_fetch_priority(arxiv_ev, arxiv_ev["url"]) > 0


def test_gap_claims_are_specific_not_binary():
    coverage = score_must_answer(
        "How does synthetic data affect benchmark accuracy?",
        [],
    )
    claims = gap_claims_for_coverage("How does synthetic data affect benchmark accuracy?", coverage)
    assert claims
    first = claims[0]
    assert first.get("slot_id")
    assert first.get("targeted_query")
    assert "Need direct evidence" in first.get("missing", "")


def test_loop_health_circuit_breaker_after_stagnation():
    evidence = [{"id": "1", "url": "https://a/1", "title": "one", "snippet": "x"}]
    h1 = assess_loop_health(evidence, prev_unique_sources=1, stagnant_loops=1)
    assert h1["stagnant_loops"] == 2
    assert h1["circuit_break"] is True


def test_quant_reconcile_flags_conflict():
    evidence = [
        {
            "url": "https://arxiv.org/abs/1",
            "title": "A",
            "full_text": "On MMLU accuracy reached 62% in our benchmark evaluation.",
        },
        {
            "url": "https://arxiv.org/abs/2",
            "title": "B",
            "full_text": "On MMLU accuracy reached 81% in our benchmark evaluation.",
        },
    ]
    citations = [{"n": 1, "url": evidence[0]["url"]}, {"n": 2, "url": evidence[1]["url"]}]
    rows, notes = reconcile_quantitative_rows(evidence, citations)
    assert notes
    assert rows
