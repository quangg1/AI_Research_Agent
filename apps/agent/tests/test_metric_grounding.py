from app.domain.metric_grounding import assess_causal_delta, audit_memo_causal_deltas, extract_causal_deltas
from app.domain.report_audit import audit_memo
from app.domain.verify_citations import verify_against_sources


ORAGENT_SOURCE = (
    "Experiments with fourteen frontier agent stacks show a clear but limited frontier: "
    "the best agent passes only 35.51% of all tasks and 20.59% of hard tasks. "
    "This pattern reveals a central feasibility–quality gap in ORAgentBench."
)


def test_oragent_style_hard_vs_all_is_not_a_causal_gain():
    claim = (
        "Enhancing the orchestration framework on ORAgentBench increased end-to-end task "
        "success from 20.59% to 35.51% without altering the underlying model weights."
    )
    result = assess_causal_delta(claim, ORAGENT_SOURCE)
    assert result is not None
    assert result["status"] == "wrong_causal"
    assert "subset" in result["note"].lower() or "hard" in result["note"].lower()


def test_verifier_flags_wrong_causal_even_when_both_numbers_exist():
    evidence = [
        {
            "id": "e11",
            "url": "https://arxiv.org/abs/2606.19787",
            "title": "ORAgentBench",
            "full_text": ORAGENT_SOURCE,
            "tier": "peer_reviewed",
        }
    ]
    claims = [
        {
            "id": "C1",
            "text": (
                "Enhancing the orchestration framework on ORAgentBench increased end-to-end "
                "task success from 20.59% to 35.51%."
            ),
            "quote": "the best agent passes only 35.51% of all tasks and 20.59% of hard tasks",
            "url": "https://arxiv.org/abs/2606.19787",
            "kind": "direct",
            "confidence": 0.9,
        }
    ]
    graph = verify_against_sources(
        claims, evidence, [{"n": 11, "url": "https://arxiv.org/abs/2606.19787"}], refetch=False
    )
    assert graph["claims"][0]["verification_status"] == "wrong_causal"
    assert graph["claims"][0]["confidence"] <= 0.25


def test_honest_conditioned_metrics_are_not_flagged_as_causal():
    claim = (
        "On ORAgentBench the best stack reaches 35.51% pass rate on all tasks and "
        "20.59% on hard tasks [11]."
    )
    assert extract_causal_deltas(claim) == []
    assert assess_causal_delta(claim, ORAGENT_SOURCE) is None


def test_memo_audit_warns_on_causal_percentage_stories():
    notes = audit_memo(
        "## Executive summary\n\n"
        "Enhancing orchestration increased success from 20.59% to 35.51% (+14.92 pp) [11].\n\n"
        "## Quantitative findings\n\n| M | V |\n|---|---|\n| x | 20.59% |\n| y | 35.51% |\n\n"
        "## What we don't know yet\n\n- gap\n"
    )
    blob = " ".join(notes).lower()
    assert "causal" in blob or "comparison" in blob
    assert audit_memo_causal_deltas(
        "increased from 20.59% to 35.51% by adding orchestration"
    )
