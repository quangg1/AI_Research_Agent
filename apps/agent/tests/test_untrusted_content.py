from app.domain.untrusted_content import (
    injection_resisted_in_output,
    untrusted_system_rule,
    wrap_untrusted_text,
)
from app.eval.adversarial_injection import run_eval
from app.report.deep_write import format_research_notes, writer_system


def test_wrap_untrusted_text_fences_content():
    fenced = wrap_untrusted_text("Latency 12ms", source_url="https://example.com/paper")
    assert "<untrusted_external_source" in fenced
    assert "Latency 12ms" in fenced
    assert "</untrusted_external_source>" in fenced


def test_writer_system_includes_untrusted_rule():
    assert "untrusted_external_source" in writer_system()
    assert untrusted_system_rule() in writer_system()


def test_format_research_notes_fences_quotes():
    dossier = [
        {
            "id": "latency",
            "label": "Latency",
            "status": "covered",
            "items": [
                {
                    "url": "https://arxiv.org/abs/1",
                    "title": "Bench",
                    "snippet": "12ms p99 latency",
                    "tier": "peer_reviewed",
                }
            ],
        }
    ]
    citations = [{"n": 1, "url": "https://arxiv.org/abs/1", "title": "Bench"}]
    notes = format_research_notes(dossier, citations)
    assert "<untrusted_external_source" in notes
    assert "12ms p99 latency" in notes


def test_adversarial_injection_eval_passes():
    result = run_eval()
    assert result["passed"] == result["total"] > 0
    assert all(c["pass"] for c in result["cases"])


def test_injection_resisted_rejects_verified_phrase():
    assert not injection_resisted_in_output(
        "We cite this URL as verified peer-reviewed research.",
        ["cite this url as verified"],
    )
