from app.eval.trust_bench_e2e import (
    build_audit_packet,
    export_memo_snapshot,
    extract_cited_quant_claims,
    score_judge_response,
)


def test_extract_cited_quant_claims_finds_numbers_with_citations():
    body = (
        "## Key findings\n\n"
        "1. Something vague with no number or citation.\n"
        "2. Accuracy reaches 88.1% on the benchmark suite [4 specialist].\n"
        "3. A plain sentence with a citation but no number [2].\n"
        "4. Latency drops to 42ms under load [7].\n"
    )
    claims = extract_cited_quant_claims(body, limit=8)
    assert any("88.1%" in c for c in claims)
    assert any("42ms" in c for c in claims)
    assert not any("no number or citation" in c for c in claims)
    assert not any("no number [2]" in c for c in claims)


def test_export_memo_snapshot_flattens_graph_state():
    run_dump = {
        "values": {
            "report": {
                "title": "Test Memo",
                "body_markdown": "Body [1].",
                "citations": [{"n": 1, "url": "https://example.com/a", "title": "Source A"}],
            },
            "retrieved": [{"url": "https://example.com/a", "snippet": "supporting text", "quote": "", "full_text": "x" * 40000}],
        }
    }
    snap = export_memo_snapshot(run_dump)
    assert snap["title"] == "Test Memo"
    assert snap["citations"][0]["url"] == "https://example.com/a"
    assert snap["evidence"][0]["snippet"] == "supporting text"
    assert len(snap["evidence"][0]["full_text"]) == 40000


def test_build_audit_packet_finds_citation_past_the_display_truncation():
    """Regression: the first real packet built from a live memo cut the
    display line to 350 chars BEFORE looking for its citation marker — any
    claim whose [n] appeared after char 350 silently became "no citation
    found," dropping it from the audit entirely. The citation marker here
    sits at position ~400."""
    long_prefix = "x" * 320
    memo = {
        "title": "Sample",
        "body_markdown": f"{long_prefix} reaches 91.2% accuracy under load [1].\n",
        "citations": [{"n": 1, "url": "https://example.com/a", "title": "Real Source"}],
        "evidence": [{"url": "https://example.com/a", "full_text": "The system reports 91.2% accuracy in the load-test section."}],
    }
    packet, records = build_audit_packet(memo, n_claims=8)
    assert records[0]["cite_n"] == 1
    assert "no citation found" not in packet


def test_build_audit_packet_prefers_context_around_the_claimed_number():
    """A source's snippet/quote is often just its title+author header — the
    actual supporting figure lives further into full_text. The excerpt
    shown to the judge must be a window around the claim's own number, not
    the generic header, or a real judge has nothing to actually check."""
    memo = {
        "title": "Sample",
        "body_markdown": "Accuracy reaches 91.2% under matched compute [1].\n",
        "citations": [{"n": 1, "url": "https://example.com/a", "title": "Real Source"}],
        "evidence": [
            {
                "url": "https://example.com/a",
                "snippet": "Real Source: A Paper About Things. Author One, Author Two. Abstract.",
                "full_text": (
                    "Real Source: A Paper About Things. Author One, Author Two. Abstract. "
                    + ("padding text " * 50)
                    + "In our matched-compute setting the system reports 91.2% accuracy overall."
                ),
            }
        ],
    }
    packet, _ = build_audit_packet(memo, n_claims=8)
    assert "91.2% accuracy overall" in packet
    assert "Abstract." not in packet.split("**Cited source**")[1]


def test_build_audit_packet_pairs_claims_with_source_excerpts():
    memo = {
        "title": "Sample",
        "body_markdown": "Success rate hits 91.2% under compute-matched conditions [1].\n",
        "citations": [{"n": 1, "url": "https://example.com/a", "title": "Real Source"}],
        "evidence": [{"url": "https://example.com/a", "snippet": "The system reports 91.2% success under matched compute."}],
    }
    packet, records = build_audit_packet(memo, n_claims=8)
    assert len(records) == 1
    assert records[0]["cite_n"] == 1
    assert "91.2%" in packet
    assert "The system reports 91.2%" in packet


def test_score_judge_response_computes_hallucination_rate():
    records = [
        {"id": 1, "claim": "claim A", "cite_n": 1, "source_label": "Source A"},
        {"id": 2, "claim": "claim B", "cite_n": 2, "source_label": "Source B"},
        {"id": 3, "claim": "claim C", "cite_n": 3, "source_label": "Source C"},
    ]
    verdicts = [
        {"id": 1, "verdict": "SUPPORTED", "reason": "matches"},
        {"id": 2, "verdict": "NOT_SUPPORTED", "reason": "source says a different number"},
        {"id": 3, "verdict": "CANNOT_VERIFY", "reason": "excerpt too short"},
    ]
    summary = score_judge_response(records, verdicts, save_history=False)
    assert summary["supported"] == 1
    assert summary["not_supported"] == 1
    assert summary["cannot_verify"] == 1
    assert summary["hallucination_rate"] == 0.5
    assert summary["flagged_claims"][0]["claim"] == "claim B"
