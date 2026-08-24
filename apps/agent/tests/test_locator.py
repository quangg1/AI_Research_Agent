from app.domain.locator import locate_quote
from app.domain.verify_citations import load_bearing_numbers, verify_against_sources


def test_locate_page_marker_and_heading():
    source = (
        "[[page 1]] Intro fluff. "
        "[[page 2]] ## Evaluation\n"
        "CODA-BENCH contains 1,009 tasks with a success rate of only 61.1%."
    )
    loc = locate_quote("success rate of only 61.1%", source)
    assert loc.found
    assert loc.page == 2
    assert "p.2" in loc.label or "Evaluation" in loc.label


def test_unfound_quote_has_no_locator():
    loc = locate_quote("quantum teleportation of embeddings", "A paper about RAG hybrid search.")
    assert loc.found is False
    assert loc.label == ""


def test_table_locator_from_quote():
    source = "Results are in Table 3. The 8B model reaches 48% pass@1 on SWE-bench Verified."
    loc = locate_quote("Table 3. The 8B model reaches 48% pass@1", source)
    assert loc.found
    assert loc.kind == "table"
    assert "3" in loc.label


def test_verifier_marks_verified_and_wrong_number():
    evidence = [
        {
            "id": "e1",
            "url": "https://arxiv.org/abs/2601.00001",
            "title": "CODA-BENCH",
            "full_text": "The benchmark has 1009 tasks and a success rate of 61.1% across agents.",
            "tier": "peer_reviewed",
        }
    ]
    claims = [
        {
            "id": "C1",
            "text": "CODA-BENCH has a 61.1% success rate.",
            "quote": "success rate of 61.1%",
            "url": "https://arxiv.org/abs/2601.00001",
            "kind": "paper_says",
        },
        {
            "id": "C2",
            "text": "CODA-BENCH has a 40% success rate.",
            "quote": "success rate of 61.1%",
            "url": "https://arxiv.org/abs/2601.00001",
            "kind": "paper_says",
        },
    ]
    graph = verify_against_sources(claims, evidence, [{"n": 11, "url": "https://arxiv.org/abs/2601.00001"}], refetch=False)
    by_id = {c["id"]: c for c in graph["claims"]}
    assert by_id["C1"]["verification_status"] == "verified"
    assert by_id["C1"]["locator"]
    assert by_id["C2"]["verification_status"] == "wrong_number"


def test_verifier_does_not_call_llm_and_can_refetch(monkeypatch):
    calls = []

    def fake_fetch(url: str) -> str:
        calls.append(url)
        return (
            "Advanced agents struggle to integrate data discovery with code execution. "
            "The suite reports N = 1009 tasks in the official release notes."
        )

    claims = [
        {
            "id": "C1",
            "text": "The bench has 1009 tasks.",
            "quote": "N = 1009 tasks",
            "url": "https://arxiv.org/abs/2401.12345",
            "kind": "paper_says",
        }
    ]
    graph = verify_against_sources(claims, [], refetch=True, fetch_fn=fake_fetch)
    assert calls
    assert graph["claims"][0]["verification_status"] == "verified"


def test_load_bearing_numbers_skip_years():
    nums = load_bearing_numbers("In 2023 the success rate was 61.1% on 1009 tasks.")
    assert "61.1" in nums or "61.1%" in nums
    assert "1009" in nums
    assert "2023" not in nums
