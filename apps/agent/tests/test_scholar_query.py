from app.domain.adversarial import falsification_queries
from app.domain.scholar_query import (
    compact_retrieval_query,
    dedupe_subqueries,
    normalize_plan_subqueries,
)
from app.domain.schema import AgentName, SubQuery
from app.report.race_write import polish_citations, split_stacked_citation_sentences


Q = (
    "Analyze how synthetic data and data-generation agents address data scarcity in specialized AI "
    "domains, and evaluate methodologies used to assess model generalization."
)


def test_compact_scholar_query_is_short():
    long_q = f"{Q} contrary findings OR counterexample OR fails to"
    out = compact_retrieval_query(long_q, goal=Q, agent="scholar")
    assert len(out.split()) <= 15
    assert "contrary" not in out.lower()
    assert "counterexample" not in out.lower()


def test_falsification_queries_no_full_goal_prefix():
    subs = falsification_queries(Q, {"depth": "deep"})
    for sub in subs:
        assert len(sub.question) <= 180
        assert sub.question.count(",") < 3
        assert Q[:60] not in sub.question


def test_dedupe_similar_subqueries():
    a = SubQuery(agent=AgentName.SCHOLAR, question="synthetic data generalization benchmark", rationale="a")
    b = SubQuery(agent=AgentName.SCHOLAR, question="synthetic data generalization evaluation metrics", rationale="b")
    out = dedupe_subqueries([a, b])
    assert len(out) == 1


def test_normalize_plan_subqueries():
    subs = [
        SubQuery(agent=AgentName.SCHOLAR, question=f"{Q} benchmark results success rate comparison", rationale="x"),
        SubQuery(agent=AgentName.SCHOLAR, question=f"{Q} benchmark measured latency", rationale="y"),
    ]
    out = normalize_plan_subqueries(subs, Q)
    assert all(len(s.question.split()) <= 16 for s in out)


def test_split_stacked_citations():
    md = "Latency improved sharply [1] [2] [3] on the benchmark."
    out = split_stacked_citation_sentences(md)
    assert "1, 2, 3" in out
    assert out.count("Latency improved sharply") == 1


def test_polish_citations_destack_and_split():
    md = "Claim one [1][2][3][4] here."
    out = polish_citations(md)
    assert "1, 2, 3, 4" in out
    assert "Claim one" in out
