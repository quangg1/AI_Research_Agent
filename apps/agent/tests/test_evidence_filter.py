from app.domain.adversarial import extract_quantitative_rows, results_section_blob
from app.domain.evidence_filter import filter_memo_evidence, is_memo_excerpt_noise
from app.domain.quantitative_verify import number_in_source
from app.report.memo_artifacts import find_contrast_pairs

SYNTH_Q = (
    "Analyze synthetic data generation methodologies and evaluation frameworks for "
    "high-quality data scarcity in specialized AI domains."
)


def test_is_memo_excerpt_noise_rejects_dropout_layer():
    text = "This model incorporates a custom dropout layer with a dropout rate of 0.1."
    assert is_memo_excerpt_noise(text)


def test_filter_memo_evidence_drops_intrusion_detection_leak():
    evidence = [
        {
            "title": "Federated Learning-Based Intrusion Detection in IoT",
            "url": "https://example.com/fl-id",
            "snippet": "CNN achieves ~98% accuracy with low latency on network intrusion tasks.",
            "tier": "news_analysis",
        },
        {
            "title": "A Closer Look at Model Collapse",
            "url": "https://arxiv.org/html/2509.16499v2",
            "snippet": "Recursive training on synthetic data induces model collapse and tail variance loss.",
            "tier": "peer_reviewed",
            "source_agent": "scholar",
        },
    ]
    kept = filter_memo_evidence(SYNTH_Q, evidence)
    urls = [e["url"] for e in kept]
    assert "https://arxiv.org/html/2509.16499v2" in urls
    assert "https://example.com/fl-id" not in urls


def test_contrast_pairs_skip_dropout_and_require_distinct_sources():
    evidence = [
        {
            "title": "Agent-as-a-Judge",
            "url": "https://arxiv.org/html/2508.02994v1",
            "snippet": (
                "Multi-agent debate improved correlation with human judgments by roughly 10-16% "
                "on open-ended QA benchmarks for synthetic evaluation."
            ),
        },
        {
            "title": "Unfiltered synthetic baseline",
            "url": "https://arxiv.org/html/2509.99999v1",
            "snippet": (
                "Recursive training on unfiltered synthetic data decreased downstream accuracy "
                "by 11% on held-out real evaluation for domain specialization."
            ),
        },
        {
            "title": "Distribution shift survey",
            "url": "https://arxiv.org/html/2405.01978v1",
            "snippet": (
                "Custom Dropout Layer : This model incorporates a custom dropout layer with "
                "a dropout rate of 0.1, randomly setting 10% of input units to zero."
            ),
        },
        {
            "title": "Model collapse filtering",
            "url": "https://arxiv.org/html/2509.16499v2",
            "snippet": (
                "Greedy sample selection reduced collapse risk and improved downstream "
                "generalization by 8% on held-out real evaluation."
            ),
        },
    ]
    citations = [{"n": i + 1, "url": e["url"]} for i, e in enumerate(evidence)]
    pairs = find_contrast_pairs(SYNTH_Q, evidence, citations)
    assert pairs
    pos, neg = pairs[0]["positive"], pairs[0]["negative"]
    assert "dropout" not in neg.lower()
    assert "decreased" in neg.lower() or "11%" in neg
    assert pairs[0]["domain"] != "legal / regulatory"


def test_results_section_blob_prefers_experiments():
    text = (
        "Introduction about synthetic data.\n\n"
        "## Experiments\n"
        "Our method improves accuracy by 12.5% on the held-out benchmark.\n\n"
        "## Related work\n"
        "Prior work used dropout rate of 0.1.\n"
    )
    blob = results_section_blob(text)
    assert "12.5%" in blob
    assert "dropout rate" not in blob


def test_extract_quantitative_rows_skips_dropout_noise():
    evidence = [
        {
            "title": "Robustness ML",
            "url": "https://arxiv.org/abs/2405.01978",
            "full_text": (
                "Methods use a custom dropout layer with dropout rate of 0.1. "
                "## Experiments Accuracy improved by 14.2% on OOD slices."
            ),
        }
    ]
    rows = extract_quantitative_rows(evidence, [{"n": 1, "url": evidence[0]["url"]}])
    metrics = {r["metric"] for r in rows}
    assert "14.2%" in metrics or any("14.2" in m for m in metrics)
    assert not any("0.1" in m and "%" not in m for m in metrics)


def test_number_in_source_accepts_metric_context_percent():
    source = "Agent-as-a-Judge evaluation improved correlation with humans by 14% overall."
    assert number_in_source("14%", source)
