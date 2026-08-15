from app.domain.credibility import credibility_score, tier_for
from app.domain.schema import SourceTier


def test_primary_docs_host():
    tier, score = credibility_score("https://platform.openai.com/docs/guides/function-calling")
    assert tier is SourceTier.OFFICIAL_REGULATION
    assert score > 0.9


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
