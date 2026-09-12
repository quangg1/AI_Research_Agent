from app.domain.coverage import (
    canonical_source_key,
    claims_from_must_answer,
    critic_should_pass,
    dedupe_evidence,
    entities_with_evidence,
    followups_for_gaps,
    is_official_implementation,
    score_must_answer,
    tag_evidence_roles,
)
from app.domain.decompose import derive_slots
from app.domain.schema import Budget
from app.graph.nodes.critic import critic_node

Q = (
    "How does Punica/LoRAX efficiently serve thousands of LoRA adapters in the same batch "
    "without blowing GPU memory, and what are the kernel-level bottlenecks?"
)

# Derived once without the LLM so the whole module sees the same deterministic
# dimensions (derive_slots caches per goal).
SLOTS = derive_slots(Q, use_llm=False)


def test_slots_follow_what_the_question_asks_for():
    by_id = {s["id"]: s for s in SLOTS}
    # The question asks how it works, names code-level concerns, and asks about limits.
    assert "mechanism" in by_id
    assert "implementation" in by_id
    assert "constraints" in by_id
    assert by_id["direct_answer"]["critical"] is True
    assert any(s["critical"] for s in SLOTS if s["id"] != "direct_answer")
    assert all(s["followup"] for s in SLOTS)


def test_slots_differ_for_an_unrelated_question():
    other = derive_slots("What did the Treaty of Westphalia settle?", use_llm=False)
    assert {s["id"] for s in other} != {s["id"] for s in SLOTS}
    blob = " ".join(s["followup"] for s in other).lower()
    assert "lora" not in blob and "gpu" not in blob


def test_github_issue_is_not_official_impl():
    ev = {
        "id": "noise",
        "title": "RuntimeError: CUDA error: no kernel image is available",
        "url": "https://github.com/someone/random/issues/123",
        "snippet": "CUDA error on decode loop",
        "tier": "specialist_research",
        "credibility": 0.8,
    }
    assert not is_official_implementation(ev)
    tagged = tag_evidence_roles([ev])
    assert tagged[0].get("off_topic")
    assert tagged[0]["source_role"] == "code_discussion"


def test_official_repo_root_counts_as_implementation():
    ev = {
        "id": "g1",
        "title": "punica",
        "url": "https://github.com/punica-ai/punica",
        "snippet": "SGMV CUDA kernel for multi-tenant LoRA",
        "tier": "specialist_research",
        "credibility": 0.8,
    }
    assert is_official_implementation(ev)
    assert is_official_implementation(ev, Q)


def test_repo_unrelated_to_the_question_is_not_implementation_evidence():
    ev = {
        "id": "g2",
        "title": "awesome-recipes",
        "url": "https://github.com/someone/awesome-recipes",
        "snippet": "A collection of dinner recipes",
    }
    assert not is_official_implementation(ev, Q)


def test_arxiv_dedupe():
    rows = dedupe_evidence(
        [
            {"id": "a", "url": "https://arxiv.org/abs/2310.18547", "title": "Punica"},
            {"id": "b", "url": "https://arxiv.org/pdf/2310.18547", "title": "Punica pdf"},
            {"id": "c", "url": "http://arxiv.org/abs/2310.18547v2", "title": "Punica v2"},
        ]
    )
    assert len(rows) == 1
    assert canonical_source_key("https://arxiv.org/html/2310.18547") == "arxiv:2310.18547"


def test_papers_alone_leave_the_implementation_dimension_open():
    evidence = [
        {
            "id": "p1",
            "title": "Punica: Multi-Tenant LoRA Serving",
            "url": "https://arxiv.org/abs/2310.18547",
            "snippet": "SGMV batches LoRA addon across adapters with segmented gather; shared base weight adapter id",
            "quote": "SGMV batches LoRA addon across adapters with segmented gather; shared base weight adapter id",
            "tier": "peer_reviewed",
            "credibility": 0.84,
        },
        {
            "id": "p2",
            "title": "S-LoRA",
            "url": "https://arxiv.org/abs/2311.03285",
            "snippet": "multi-tenant LoRA serving continuous batching adapter loading",
            "quote": "multi-tenant LoRA serving continuous batching adapter loading",
            "tier": "peer_reviewed",
            "credibility": 0.84,
        },
    ]
    cov = score_must_answer(Q, evidence, SLOTS)
    ok, reasons = critic_should_pass(Q, cov, evidence)
    assert not ok
    assert cov["depth_score"]["score"] <= 80
    assert cov["ratio"] == cov["covered"] / cov["total"]
    joined = " ".join(reasons).lower()
    assert "implementation" in joined or "critical dimension" in joined


def test_claims_are_question_dimensions_not_boilerplate():
    evidence = [
        {
            "id": "p1",
            "title": "Punica",
            "url": "https://arxiv.org/abs/2310.18547",
            "snippet": "In recent years the pretrain-then-finetune paradigm has become widely adopted.",
            "quote": "In recent years the pretrain-then-finetune paradigm has become widely adopted.",
            "tier": "peer_reviewed",
            "credibility": 0.84,
        },
        {
            "id": "g1",
            "title": "punica",
            "url": "https://github.com/punica-ai/punica",
            "snippet": "SGMV CUDA kernel implementation README for LoRA adapters",
            "quote": "SGMV CUDA kernel implementation README for LoRA adapters",
            "tier": "specialist_research",
            "credibility": 0.8,
        },
    ]
    cov = score_must_answer(Q, evidence, SLOTS)
    claims = claims_from_must_answer(cov, evidence)
    assert claims
    quotes = " ".join(c["quote"] for c in claims).lower()
    assert "pretrain-then-finetune" not in quotes
    assert all(c.get("slot_id") for c in claims)
    labels = {s["label"] for s in cov["slots"]}
    assert any(c["text"].split(" — ")[0] in labels for c in claims)


def test_uncovered_critical_dimension_blocks_the_critic():
    evidence = [
        {
            "id": "g1",
            "title": "punica",
            "url": "https://github.com/punica-ai/punica",
            "snippet": "SGMV CUDA kernel implementation for LoRA adapters",
            "quote": "SGMV CUDA kernel implementation for LoRA adapters",
            "tier": "specialist_research",
            "credibility": 0.8,
        }
    ]
    cov = score_must_answer(Q, evidence, SLOTS)
    ok, _ = critic_should_pass(Q, cov, evidence)
    uncovered = [s for s in cov["slots"] if s.get("critical") and s["status"] != "covered"]
    if uncovered:
        assert not ok
        assert cov["depth_score"]["score"] <= 80


def test_shared_topic_words_are_not_treated_as_compared_subjects():
    evidence = [
        {"id": "a", "title": "Punica", "snippet": "Punica batches LoRA adapters", "url": "https://a.dev/1"},
        {"id": "b", "title": "S-LoRA", "snippet": "S-LoRA pages LoRA adapters", "url": "https://b.dev/2"},
        {"id": "c", "title": "vLLM", "snippet": "vLLM serves LoRA adapters", "url": "https://c.dev/3"},
    ]
    entities = entities_with_evidence(
        "Compare Punica, S-LoRA, and vLLM for serving LoRA adapters", evidence
    )
    assert "LoRA" not in entities
    assert {"Punica", "S-LoRA", "vLLM"} <= set(entities)


def test_followups_for_gaps_covers_missing_primary_source_even_with_no_open_slots():
    # All slots covered, no critical_gaps -> the old code returned zero followups
    # here even when critic_should_pass() would still fail on missing primary_sources.
    coverage = {
        "critical_gaps": [],
        "slots": [{"id": "s1", "label": "x", "status": "covered"}],
        "primary_sources": False,
    }
    out = followups_for_gaps(Q, coverage, limit=2)
    assert out, "missing primary_sources must still produce a followup query"


def test_followups_for_gaps_covers_missing_named_entity_even_with_no_open_slots():
    query = "Compare Punica, S-LoRA, and vLLM for serving LoRA adapters"
    evidence = [
        {"id": "a", "title": "Punica", "snippet": "Punica batches LoRA adapters", "url": "https://a.dev/1"},
    ]
    coverage = {
        "critical_gaps": [],
        "slots": [{"id": "s1", "label": "x", "status": "covered"}],
        "primary_sources": True,
    }
    out = followups_for_gaps(query, coverage, limit=2, evidence=evidence)
    assert out, "a named subject (S-LoRA/vLLM) with no dedicated evidence must still produce a followup"
    joined = " ".join((sq.question or "").lower() for sq in out)
    assert "s-lora" in joined or "vllm" in joined


def test_followups_for_gaps_covers_missing_entity_even_with_an_unrelated_open_slot():
    """Regression: the entity/primary_sources fallback used to run only when
    `ordered` was completely empty -- one unrelated weak slot (e.g.
    "direct_answer") permanently crowded it out, so a named subject the
    question explicitly asked about (real run: "Claude-based") never got a
    followup query across 5 loop iterations, even though gap_limit left
    room for more than one followup per round."""
    query = "Compare Punica, S-LoRA, and vLLM for serving LoRA adapters"
    evidence = [
        {"id": "a", "title": "Punica", "snippet": "Punica batches LoRA adapters", "url": "https://a.dev/1"},
    ]
    coverage = {
        "critical_gaps": [],
        "slots": [{"id": "direct_answer", "label": "Direct answer to the question as asked", "status": "weak"}],
        "primary_sources": True,
    }
    out = followups_for_gaps(query, coverage, limit=3, evidence=evidence)
    gap_ids = {sq.gap_id for sq in out}
    assert "direct_answer" in gap_ids
    assert any(g.startswith("entity:") for g in gap_ids), gap_ids


def test_followups_for_gaps_excludes_gaps_already_proven_unproductive():
    coverage = {
        "critical_gaps": [
            {"id": "gap_a", "label": "Gap A"},
            {"id": "gap_b", "label": "Gap B"},
        ],
        "slots": [],
        "primary_sources": True,
    }
    out = followups_for_gaps(Q, coverage, limit=3, exclude_gap_ids={"gap_a"})
    gap_ids = {sq.gap_id for sq in out}
    assert "gap_a" not in gap_ids
    assert "gap_b" in gap_ids


def test_critic_node_loops_on_gaps():
    state = {
        "query": Q,
        "retrieved": [
            {
                "id": "p1",
                "title": "Punica",
                "url": "https://arxiv.org/abs/2310.18547",
                "snippet": "SGMV shared base weight adapter batching",
                "quote": "SGMV shared base weight adapter batching",
                "tier": "peer_reviewed",
                "credibility": 0.84,
            }
        ],
        "brief": {"must_answer": SLOTS, "depth": "deep"},
        "budget": Budget(max_iterations=3, iterations=1, max_tool_calls=12, used_tool_calls=3).model_dump(),
    }
    out = critic_node(state)
    assert out["critic"]["status"] == "insufficient"
    assert out["followups"]
    assert out["critic"]["depth_score"]["score"] <= 80
    fr = out["critic"]["coverage"].get("must_answer_fraction")
    assert fr and "/" in fr


def test_critic_node_stops_retrying_a_gap_that_produced_no_progress():
    """Regression: followups_for_gaps() is a pure function of coverage, so an
    unresolved gap gets the identical followup query every iteration with no
    signal it already ran and changed nothing -- real run: 3 straight
    iterations came back with byte-identical unique_sources/must_pct/
    depth_score, each one silently re-issuing the same search. The second
    critic_node call here simulates "last round's followups retrieved
    nothing new" and must stop asking for the same gap again."""
    base_state = {
        "query": Q,
        "retrieved": [
            {
                "id": "p1",
                "title": "Punica",
                "url": "https://arxiv.org/abs/2310.18547",
                "snippet": "SGMV shared base weight adapter batching",
                "quote": "SGMV shared base weight adapter batching",
                "tier": "peer_reviewed",
                "credibility": 0.84,
            }
        ],
        "brief": {"must_answer": SLOTS, "depth": "deep"},
        "budget": Budget(max_iterations=5, iterations=1, max_tool_calls=40, used_tool_calls=3).model_dump(),
    }
    first = critic_node(base_state)
    assert first["followups"], "first round must attempt at least one gap"
    tried_ids = {f["gap_id"] for f in first["followups"] if f.get("gap_id")}
    assert tried_ids

    # Same evidence -> same coverage -> the followup round changed nothing,
    # matching a real stagnant iteration.
    second_state = {
        **base_state,
        "budget": Budget(max_iterations=5, iterations=2, max_tool_calls=40, used_tool_calls=5).model_dump(),
        "_quality_history": first["_quality_history"],
        "_last_followup_gap_ids": first["_last_followup_gap_ids"],
        "_unproductive_gap_ids": first["_unproductive_gap_ids"],
    }
    second = critic_node(second_state)
    second_ids = {f["gap_id"] for f in second["followups"] if f.get("gap_id")}
    assert not (tried_ids & second_ids), (tried_ids, second_ids)
    assert set(second["_unproductive_gap_ids"]) >= tried_ids
