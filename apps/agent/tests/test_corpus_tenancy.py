from app.domain import knowledge
from app.retrieval import store


def test_knowledge_isolated_by_org(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "memory")
    knowledge.reset_cache()
    monkeypatch.setattr(knowledge, "_embed", lambda text: [])

    knowledge.save_answer(
        "How does CRISPR work?",
        {
            "title": "CRISPR",
            "executive_summary": "Cas9 cuts DNA.",
            "body_markdown": "# CRISPR\n\nCas9. [1]\n",
            "citations": [{"url": "https://a.org/1", "title": "A"}],
            "metrics": {"depth_score": 70},
        },
        org_id="org_a",
    )
    knowledge.save_answer(
        "How does CRISPR work?",
        {
            "title": "CRISPR B",
            "executive_summary": "Other org memo.",
            "body_markdown": "# CRISPR\n\nOther. [1]\n",
            "citations": [{"url": "https://b.org/1", "title": "B"}],
            "metrics": {"depth_score": 70},
        },
        org_id="org_b",
    )

    hit_a = knowledge.lookup("How does CRISPR work?", org_id="org_a")
    hit_b = knowledge.lookup("How does CRISPR work?", org_id="org_b")
    assert hit_a and hit_a.record.get("org_id") == "org_a"
    assert hit_b and hit_b.record.get("org_id") == "org_b"
    assert hit_a.record["id"] != hit_b.record["id"]


def test_memory_corpus_filters_by_org(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "memory")
    store._MEMORY = [
        {"id": "g1", "snippet": "global", "org_id": None, "path": "a.md", "title": "G"},
        {"id": "o1", "snippet": "org", "org_id": "org_x", "path": "b.md", "title": "O"},
    ]
    scoped = store.get_store_documents("org_x")
    assert len(scoped) == 2
    assert any(d["id"] == "g1" for d in scoped)
    assert any(d["id"] == "o1" for d in scoped)

    store._MEMORY = [
        {"id": "g1", "snippet": "global", "org_id": None, "path": "a.md", "title": "G"},
    ]
    assert store.corpus_available("org_x")
