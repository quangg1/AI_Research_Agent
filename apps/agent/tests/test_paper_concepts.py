from app.domain.paper_concepts import extract_paper_concepts


def test_method_extraction_ignores_lowercase_prose_words():
    """Regression: re.I on [A-Z]-anchored patterns matched ordinary lowercase
    prose, turning "...difficult to obtain (e.g., weather data...)" and
    "...controlled variations (e.g., up-weighting...)" into fake dimension
    concepts "obtain" and "variations" (real memo output, 2026-09-05 run)."""
    evidence = [
        {
            "id": "ev1",
            "n": 1,
            "title": "Best Practices and Lessons Learned on Synthetic Data",
            "snippet": (
                "This is particularly valuable in domains where real-world data is "
                "scarce or difficult to obtain (e.g., weather data covering all "
                "conditions (Li et al., 2023a)). Second, synthetic data can be "
                "tailored to specific requirements, such as ensuring a balanced "
                "representation of different classes by introducing controlled "
                "variations (e.g., up-weighting low-resource languages)."
            ),
            "tier": "specialist_research",
            "credibility": 0.76,
        }
    ]
    concepts = extract_paper_concepts(evidence, limit=5)
    names = {c["concept_name"].lower() for c in concepts}
    assert "obtain" not in names
    assert "variations" not in names


def test_method_extraction_ignores_capitalized_sentence_prose():
    """Regression: same re.I bug matched "believe that the AgentInstruct
    approach" as a method name (should only ever match the real proper noun,
    if anything)."""
    evidence = [
        {
            "id": "ev2",
            "n": 2,
            "title": "AgentInstruct: Toward Generative Teaching with Agentic Flows",
            "snippet": "We believe that the AgentInstruct approach generalizes across tasks.",
            "tier": "specialist_research",
            "credibility": 0.76,
        }
    ]
    concepts = extract_paper_concepts(evidence, limit=5)
    names = [c["concept_name"] for c in concepts]
    assert "believe that the AgentInstruct" not in names


def test_method_extraction_still_finds_real_acronyms_and_titlecase_methods():
    """The case-sensitivity fix must not gut the feature — genuine proper-noun
    method/framework names should still be found."""
    evidence = [
        {
            "id": "ev3",
            "n": 3,
            "title": "Retrieval survey",
            "snippet": "We use RAG (Retrieval Augmented Generation) to reduce hallucination. Our Multi-Agent framework outperforms baselines.",
            "tier": "peer_reviewed",
            "credibility": 0.9,
        }
    ]
    concepts = extract_paper_concepts(evidence, limit=5)
    names = {c["concept_name"] for c in concepts}
    assert "RAG" in names
    assert "Multi-Agent" in names
