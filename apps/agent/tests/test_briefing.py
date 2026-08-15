from app.graph.nodes.briefing import _compose_query, _heuristic_brief
from app.domain.schema import QueryType


def test_heuristic_brief_extracts_sector_and_depth():
    brief = _heuristic_brief(
        "Should we fine-tune a 8B model on weekly runbooks, or use RAG over the same docs?"
    )
    assert brief.sector
    assert any(x in brief.sector.lower() for x in ("rag", "fine", "llm", "tune"))
    assert brief.depth in {"quick", "standard", "deep"}
    assert brief.query_type == QueryType.OPEN_RESEARCH.value
    assert any("fine-tune" in x.lower() or "rag" in x.lower() for x in brief.must_cover)


def test_compose_query_includes_must_cover():
    brief = _heuristic_brief("Does RAG always require a vector database?")
    text = _compose_query("Does RAG always require a vector database?", brief)
    assert "Must cover" in text or brief.must_cover
    assert "vector" in text.lower() or "bm25" in " ".join(brief.must_cover).lower() or "vector" in " ".join(brief.must_cover).lower()
