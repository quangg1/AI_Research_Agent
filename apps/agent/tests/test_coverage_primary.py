from app.domain.coverage import score_must_answer

Q = "How does Punica serve LoRA adapters on GPU?"


def test_primary_source_covers_with_one_topic_anchor():
    slots = [
        {
            "id": "mechanism",
            "label": "Mechanism",
            "critical": True,
            "patterns": [r"kernel", r"mechanism"],
            "topic_terms": ["lora", "punica", "gpu"],
            "followup": Q,
        }
    ]
    evidence = [
        {
            "id": "p1",
            "title": "LoRAX paper",
            "url": "https://arxiv.org/abs/2310.12345",
            "snippet": "Punica SGMV kernel batches LoRA adapters on GPU efficiently.",
            "tier": "peer_reviewed",
            "credibility": 0.9,
        }
    ]
    cov = score_must_answer(Q, evidence, slots)
    assert cov["slots"][0]["status"] == "covered"
    assert cov["primary_sources"] >= 1
