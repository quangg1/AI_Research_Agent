"""Tests for domain-balanced retrieval (Fix 1)."""

from app.graph.nodes.scholar import _balanced_evidence_pool, _classify_paper_domain
from app.graph.nodes.search import _classify_paper_domain as search_classify


def test_classify_paper_domain_code():
    """Code papers should be identified by GitHub/implementation keywords."""
    code_paper = {
        "url": "https://github.com/user/repo",
        "title": "Implementation of LangGraph",
        "snippet": "Source code for agentic framework"
    }
    assert _classify_paper_domain(code_paper) == "code"
    assert search_classify(code_paper) == "code"


def test_classify_paper_domain_theory():
    """Theory papers should be identified by arxiv/research keywords."""
    theory_paper = {
        "url": "https://arxiv.org/abs/2024.12345",
        "title": "Analysis of Memory Architectures in Agents",
        "snippet": "We propose a novel approach"
    }
    assert _classify_paper_domain(theory_paper) == "theory"


def test_classify_paper_domain_benchmark():
    """Benchmark papers should be identified by evaluation keywords."""
    benchmark_paper = {
        "url": "https://aclanthology.org/2024.acl-1.123",
        "title": "Benchmark Evaluation of LLM Agents",
        "snippet": "We evaluate performance metrics across multiple systems"
    }
    assert _classify_paper_domain(benchmark_paper) == "benchmark"


def test_classify_paper_domain_docs():
    """Documentation should be identified by docs keywords."""
    docs_paper = {
        "url": "https://docs.langchain.com/guides/agents",
        "title": "LangChain Agent Documentation",
        "snippet": "API reference and tutorial"
    }
    assert _classify_paper_domain(docs_paper) == "docs"


def test_balanced_evidence_pool_limits_code_papers():
    """Domain balancing should limit code papers to max 40%."""
    papers = [
        {"url": "https://github.com/a", "title": "Code A", "snippet": "impl"},  # code
        {"url": "https://github.com/b", "title": "Code B", "snippet": "impl"},  # code
        {"url": "https://github.com/c", "title": "Code C", "snippet": "impl"},  # code
        {"url": "https://github.com/d", "title": "Code D", "snippet": "impl"},  # code
        {"url": "https://github.com/e", "title": "Code E", "snippet": "impl"},  # code
        {"url": "https://github.com/f", "title": "Code F", "snippet": "impl"},  # code
        {"url": "https://github.com/g", "title": "Code G", "snippet": "impl"},  # code
        {"url": "https://github.com/h", "title": "Code H", "snippet": "impl"},  # code
        {"url": "https://arxiv.org/1", "title": "Theory 1", "snippet": "analysis"},  # theory
        {"url": "https://arxiv.org/2", "title": "Theory 2", "snippet": "analysis"},  # theory
    ]
    
    balanced = _balanced_evidence_pool(papers, max_code_ratio=0.40)
    
    # Count code papers in result
    code_count = sum(1 for p in balanced if "github" in p["url"])
    code_ratio = code_count / len(balanced)
    
    assert len(balanced) == 10, "Should return same total count"
    assert code_ratio <= 0.40, f"Code ratio {code_ratio:.2%} exceeds 40% limit"
    assert code_count == 4, f"Expected 4 code papers (40% of 10), got {code_count}"


def test_balanced_evidence_pool_preserves_ranking_within_domains():
    """Balancing should preserve relative ranking within each domain."""
    papers = [
        {"url": "https://github.com/1", "title": "Code 1", "snippet": ""},  # code, rank 1
        {"url": "https://github.com/2", "title": "Code 2", "snippet": ""},  # code, rank 2
        {"url": "https://github.com/3", "title": "Code 3", "snippet": ""},  # code, rank 3
        {"url": "https://arxiv.org/1", "title": "Theory 1", "snippet": ""},  # theory
        {"url": "https://arxiv.org/2", "title": "Theory 2", "snippet": ""},  # theory
    ]
    
    balanced = _balanced_evidence_pool(papers, max_code_ratio=0.40)
    
    # Check that first 2 code papers are Code 1 and Code 2 (preserved order)
    code_papers = [p for p in balanced if "github" in p["url"]]
    assert len(code_papers) == 2, "Should keep 2/5 = 40% code papers"
    assert code_papers[0]["title"] == "Code 1", "Should preserve ranking"
    assert code_papers[1]["title"] == "Code 2", "Should preserve ranking"


def test_balanced_evidence_pool_handles_no_code_bias():
    """If no code papers, balancing should not change anything."""
    papers = [
        {"url": "https://arxiv.org/1", "title": "Theory 1", "snippet": ""},
        {"url": "https://arxiv.org/2", "title": "Theory 2", "snippet": ""},
        {"url": "https://arxiv.org/3", "title": "Theory 3", "snippet": ""},
    ]
    
    balanced = _balanced_evidence_pool(papers, max_code_ratio=0.40)
    
    assert len(balanced) == 3
    assert balanced == papers, "Should return unchanged when no code papers"


def test_balanced_evidence_pool_handles_empty_input():
    """Empty input should return empty output."""
    assert _balanced_evidence_pool([]) == []


def test_balanced_evidence_pool_backfills_when_needed():
    """If not enough non-code papers, should backfill with code."""
    papers = [
        {"url": "https://github.com/1", "title": "Code 1", "snippet": ""},
        {"url": "https://github.com/2", "title": "Code 2", "snippet": ""},
        {"url": "https://github.com/3", "title": "Code 3", "snippet": ""},
        {"url": "https://github.com/4", "title": "Code 4", "snippet": ""},
        {"url": "https://github.com/5", "title": "Code 5", "snippet": ""},
        {"url": "https://arxiv.org/1", "title": "Theory 1", "snippet": ""},  # Only 1 non-code
    ]
    
    balanced = _balanced_evidence_pool(papers, max_code_ratio=0.40)
    
    # Should get: 2 code (40% of 6) + 1 theory + 3 backfilled code = 6 total
    assert len(balanced) == 6, "Should return full set with backfill"
    code_count = sum(1 for p in balanced if "github" in p["url"])
    assert code_count == 5, "Should backfill with remaining code papers"
