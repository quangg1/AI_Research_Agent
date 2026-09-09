from app.domain.credibility import credibility_score, tier_for
from app.domain.schema import SourceTier


def test_primary_docs_host():
    tier, score = credibility_score("https://platform.openai.com/docs/guides/function-calling")
    assert tier is SourceTier.OFFICIAL_REGULATION
    assert score > 0.9


def test_agent_framework_official_docs_hosts_are_recognized():
    """Real run: a memo compared LangGraph/AutoGen/CrewAI/OpenAI Agents by
    name, but their actual official docs live on hosts this allowlist never
    had (docs.langchain.com, developers.openai.com, docs.crewai.com,
    microsoft.github.io for AutoGen) — every one of them fell through to
    UNKNOWN, so even when search found them they ranked no better than a
    random blog and the writer fell back on unreferenced training data."""
    assert tier_for("https://docs.langchain.com/oss/python/langchain/multi-agent") is SourceTier.STANDARD_BODY
    assert tier_for("https://developers.openai.com/api/docs/guides/agents") is SourceTier.OFFICIAL_REGULATION
    assert tier_for("https://docs.crewai.com/en/concepts/processes") is SourceTier.STANDARD_BODY
    assert (
        tier_for("https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html")
        is SourceTier.STANDARD_BODY
    )


def test_eval_lab():
    assert tier_for("https://helm.stanford.edu/intro").name == "INTERGOVERNMENTAL"


def test_unknown_blog_is_low():
    tier, score = credibility_score("https://random-consultant.example/rag-made-easy")
    assert tier is SourceTier.UNKNOWN
    assert score < 0.4


def test_old_year_penalised():
    _, fresh = credibility_score("https://arxiv.org/abs/2401.0001", "2025")
    _, old = credibility_score("https://arxiv.org/abs/2401.0001", "2012")
    assert old < fresh
