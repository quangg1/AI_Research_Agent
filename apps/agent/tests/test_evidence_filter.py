from app.domain.adversarial import extract_quantitative_rows, results_section_blob
from app.domain.quantitative_verify import number_in_source


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
