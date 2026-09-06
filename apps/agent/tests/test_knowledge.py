from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.domain import knowledge
from app.domain.schema import Budget
from app.graph.builder import after_plan_gate, after_planner
from app.graph.nodes.planner import _knowledge_hit
from app.main import app


QUESTION = "How does CRISPR-Cas9 cut a target DNA sequence inside a living cell?"
NEAR_DUPLICATE = "How does CRISPR-Cas9 cut a target DNA sequence inside living cells?"
RELATED = "What are the off-target risks of CRISPR-Cas9 editing in human cells?"
UNRELATED = "Which accounting standard governs lease liabilities for retail tenants?"


def _report(title: str, urls: list[str], depth: int = 70) -> dict:
    return {
        "title": title,
        "executive_summary": f"{title} summary.",
        "body_markdown": f"# {title}\n\n## Analysis\n\nCas9 cleaves DNA. [1]\n",
        "decision_rule": "Act only on cited mechanism claims.",
        "open_questions": ["Off-target rate in primary cells"],
        "limitations": ["Two sources only"],
        "claims": [
            {"slot_id": "mechanism", "text": "Cas9 cleaves both strands", "confidence": 0.8, "url": urls[0]}
        ],
        "citations": [
            {"evidence_id": f"e{i}", "title": f"Source {i}", "url": u, "quote": "cleaves DNA", "tier": "peer_reviewed"}
            for i, u in enumerate(urls)
        ],
        "metrics": {"depth_score": depth},
    }


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    del tmp_path
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "memory")
    knowledge.reset_cache()
    monkeypatch.setattr(knowledge, "_embed", lambda text: [])
    yield
    knowledge.reset_cache()


def test_saves_and_reuses_a_near_duplicate_question():
    saved = knowledge.save_answer(QUESTION, _report("CRISPR mechanism", ["https://a.org/1"]))
    assert saved and saved["version"] == 1

    hit = knowledge.lookup(NEAR_DUPLICATE)
    assert hit is not None
    assert hit.mode == "cached"
    assert hit.record["id"] == saved["id"]


def test_unrelated_question_does_not_match():
    knowledge.save_answer(QUESTION, _report("CRISPR mechanism", ["https://a.org/1"]))
    assert knowledge.lookup(UNRELATED) is None


def test_related_question_only_augments():
    knowledge.save_answer(QUESTION, _report("CRISPR mechanism", ["https://a.org/1"]))
    hit = knowledge.lookup(RELATED)
    if hit is not None:
        assert hit.mode == "augment"


def test_high_embedding_similarity_but_low_keyword_overlap_does_not_reuse(monkeypatch):
    """Real run: 0.98 cosine similarity but 0.27 fingerprint overlap between a
    stored "single vs multi-agent" memo and a new question naming five agent
    frameworks the stored answer never covered. `score = max(cosine, jaccard)`
    let the embedding alone qualify it for "augment", which clamps the
    retrieval budget to ~6 calls for a question that's mostly new content —
    the run exhausted its whole budget re-issuing one deduped query and
    published a 5-source memo. Reuse must require real keyword overlap too,
    not just a similar embedding."""
    monkeypatch.setattr(knowledge, "_embed", lambda goal: [1.0, 0.0])
    saved = knowledge.save_answer(
        "Evaluate whether transitioning from single-agent to multi-agent LLM "
        "architectures improves reliability and efficiency in long-horizon "
        "research tasks.",
        _report("Single vs multi-agent", ["https://a.org/1"], depth=100),
    )
    assert saved
    hit = knowledge.lookup(
        "Compare OpenAI Agents, Anthropic Claude-based agents, LangGraph, "
        "AutoGen, and CrewAI on task decomposition, tool orchestration, "
        "inter-agent communication, and error propagation using benchmarks "
        "for web research, code generation, and multi-hop reasoning, "
        "measuring cost per successful task."
    )
    assert hit is None


def test_merging_keeps_every_source_and_bumps_version():
    first = knowledge.save_answer(QUESTION, _report("CRISPR mechanism", ["https://a.org/1"], depth=60))
    merged = knowledge.save_answer(
        NEAR_DUPLICATE,
        _report("CRISPR mechanism, revised", ["https://b.org/2", "https://c.org/3"], depth=85),
        prior_id=first["id"],
    )
    assert merged["version"] == 2
    urls = {c["url"] for c in merged["citations"]}
    assert urls == {"https://a.org/1", "https://b.org/2", "https://c.org/3"}
    assert merged["depth_score"] == 85
    assert merged["title"] == "CRISPR mechanism, revised"
    assert len(knowledge.load_records()) == 1


def test_a_weaker_rerun_does_not_overwrite_a_stronger_memo():
    first = knowledge.save_answer(QUESTION, _report("Strong memo", ["https://a.org/1"], depth=90))
    merged = knowledge.save_answer(
        NEAR_DUPLICATE, _report("Weak memo", ["https://b.org/2"], depth=30), prior_id=first["id"]
    )
    assert merged["title"] == "Strong memo"
    assert merged["depth_score"] == 90
    assert len(merged["citations"]) == 2


def test_concurrent_saves_preserve_valid_records():
    total = 20

    def save(index: int):
        return knowledge.save_answer(
            f"{QUESTION} Variant {index}",
            _report(f"Concurrent memo {index}", [f"https://example.org/{index}"]),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        saved = list(pool.map(save, range(total)))

    rows = knowledge.load_records()
    assert len(rows) == total
    assert len({row["id"] for row in rows}) == total
    assert all(saved)


def test_seeded_evidence_carries_prior_sources_forward():
    record = knowledge.save_answer(QUESTION, _report("CRISPR mechanism", ["https://a.org/1", "https://b.org/2"]))
    seeded = knowledge.seed_evidence(record)
    assert [e["url"] for e in seeded] == ["https://a.org/1", "https://b.org/2"]
    assert all(e["source_kind"] == "knowledge_reuse" for e in seeded)


def test_knowledge_endpoints_report_stats_and_matches():
    saved = knowledge.save_answer(QUESTION, _report("CRISPR mechanism", ["https://a.org/1"]))
    client = TestClient(app)

    stats = client.get("/v1/knowledge").json()
    assert stats["records"] == 1

    matched = client.get("/v1/knowledge/match", params={"query": NEAR_DUPLICATE}).json()
    assert matched["match"] is True
    assert matched["mode"] == "cached"
    assert matched["id"] == saved["id"]

    missed = client.get("/v1/knowledge/match", params={"query": UNRELATED}).json()
    assert missed == {"match": False}


def test_a_fresh_run_ignores_the_stored_answer():
    knowledge.save_answer(QUESTION, _report("CRISPR mechanism", ["https://a.org/1"]))
    budget = Budget(iterations=1)

    assert _knowledge_hit({"query": NEAR_DUPLICATE}, budget, []) is not None
    assert _knowledge_hit({"query": NEAR_DUPLICATE, "reuse_mode": "off"}, budget, []) is None


def test_cached_hit_routes_straight_to_report():
    state = {"reuse_mode": "cached", "prior_knowledge": {"id": "abc"}, "agents_to_run": []}
    assert after_planner(state) == "report"
    assert after_plan_gate({"reuse_mode": "augment", "agents_to_run": ["search"]}) == [
        "search",
        "scholar",
        "docs",
    ]
