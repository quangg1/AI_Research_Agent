from app.domain.credibility import credibility_score, tier_for
from app.domain.decompose import derive_slots
from app.domain.research_intent import (
    claim_confidence,
    decision_rule_for,
    decompose_subquestions,
    flip_condition_for,
    is_comparison_query,
    is_mechanism_query,
    is_secondary_host,
    named_systems,
    user_goal,
)
from app.domain.routing_policy import heuristic_plan
from app.domain.schema import AgentName, SourceTier

MECHANISM_Q = "How does Punica/LoRAX batch multiple LoRA adapters in one forward pass?"
BIOLOGY_Q = "How does CRISPR-Cas9 cut a target DNA sequence inside a living cell?"
POLICY_Q = "Compare the GDPR and the CCPA on how they define a data subject request."


def test_user_goal_strips_brief_metadata():
    blob = (
        f"{MECHANISM_Q}\n"
        "Sector: LLM systems\n"
        "Constraints: Stay inside applied AI / LLM systems (serving, RAG, agents, eval)"
    )
    goal = user_goal(blob)
    assert "Punica" in goal
    assert "Constraints" not in goal
    assert is_mechanism_query(blob)


def test_mechanism_detection_ignores_effect_questions():
    assert is_mechanism_query(BIOLOGY_Q)
    assert not is_mechanism_query(
        "What is continuous batching in vLLM and how does it affect time-to-first-token?"
    )


def test_named_subjects_come_from_the_question_not_a_builtin_list():
    assert "Punica" in named_systems(MECHANISM_Q)
    assert "CRISPR-Cas9" in named_systems(BIOLOGY_Q)
    assert {"GDPR", "CCPA"} <= set(named_systems(POLICY_Q))
    assert is_comparison_query(POLICY_Q)


def test_slots_track_the_subject_of_any_question():
    for query in (MECHANISM_Q, BIOLOGY_Q, POLICY_Q):
        slots = derive_slots(query, use_llm=False)
        assert slots, query
        assert any(s["critical"] for s in slots)
        followups = " ".join(s["followup"] for s in slots).lower()
        assert not any(token in followups for token in ("sgmv", "punica ai", "bm25")) or "punica" in query.lower()


def test_slots_for_a_non_technical_question_carry_no_technical_vocabulary():
    slots = derive_slots(POLICY_Q, use_llm=False)
    blob = " ".join(f"{s['label']} {s['followup']}" for s in slots).lower()
    for leaked in ("sgmv", "cuda", "lora", "kernel", "bm25", "vector database"):
        assert leaked not in blob


def test_scalability_is_its_own_slot_when_asked():
    from app.domain.decompose import reset_slot_cache

    reset_slot_cache()
    q = (
        "Compare o1 vs DeepSeek-R1 on latency, FLOP cost, and scalability "
        "(KV-cache and multi-node MCTS)."
    )
    slots = derive_slots(q, use_llm=False)
    ids = {s["id"] for s in slots}
    assert "scalability" in ids
    assert "quantitative" in ids


def test_decision_rule_reflects_coverage_not_a_template():
    critic = {
        "status": "insufficient",
        "coverage": {
            "slots": [
                {"id": "direct_answer", "label": "Direct answer to the question", "status": "covered"},
                {"id": "mechanism", "label": "How CRISPR-Cas9 cleaves DNA", "status": "weak"},
                {"id": "constraints", "label": "Off-target effects", "status": "open"},
            ]
        },
    }
    rule = decision_rule_for(BIOLOGY_Q, [], critic)
    assert "How CRISPR-Cas9 cleaves DNA" in rule
    assert "Off-target effects" in rule
    assert "Verify:" in rule or "Do not assume:" in rule
    assert "BM25" not in rule
    assert "vector database" not in rule.lower()


def test_decision_rule_falls_back_without_coverage():
    rule = decision_rule_for(POLICY_Q, [], {"status": "insufficient"})
    assert "Empirical cutoffs" in rule
    assert "Engineering heuristics" in rule
    assert "Evidence-backed threshold: none" in rule
    assert "BM25" not in rule


def test_decision_rule_separates_empirical_from_heuristics():
    critic = {
        "coverage": {
            "slots": [
                {"id": "a", "label": "How CRISPR-Cas9 cleaves DNA", "status": "covered"},
                {"id": "b", "label": "Off-target effects", "status": "open"},
            ]
        }
    }
    rule = decision_rule_for(BIOLOGY_Q, [{"n": 1}], critic)
    assert "### Empirical cutoffs" in rule
    assert "### Engineering heuristics" in rule
    assert "How CRISPR-Cas9 cleaves DNA" in rule
    assert "Off-target effects" in rule


def test_decision_rule_never_leaks_the_authoring_instruction():
    """Real memo output showed the literal instruction sentence ("Only
    thresholds measured in a cited experiment belong here. If none were
    measured, write: Evidence-backed threshold: none.") printed as reader-
    facing body text, verbatim, in both Decision rule and the derived At a
    glance box — it described what decision_rule_for already does below it,
    so it should never be emitted at all."""
    rule = decision_rule_for(POLICY_Q, [], {"status": "insufficient"})
    assert "Only thresholds measured in a cited experiment belong here" not in rule


def test_decision_rule_no_orphaned_act_on_these_header():
    """supported can be non-empty while every label is filtered out by
    _is_slot_label_leak (bare slot ids / poison labels) — the header used to
    print unconditionally before that filtering ran, leaving "Act on these
    — the sources support them directly:" printed with no bullets under it."""
    critic = {
        "coverage": {
            "slots": [
                {"id": "constraints_and_limitations", "label": "constraints_and_limitations", "status": "covered"},
            ]
        }
    }
    rule = decision_rule_for(POLICY_Q, [{"n": 1}], critic)
    assert "Act on these" not in rule
    assert "Evidence-backed threshold: none" in rule


def test_named_systems_drops_capitalized_verbs_from_brief_metadata():
    """goal_with_named_subjects reorders the query (Must cover / Constraints
    content first), which moved the goal's own first word out of sentence-
    initial position and let plain capitalized verbs through as "named
    subjects" — a real run flagged "Named subjects with no dedicated
    evidence: Anthropic, Assess, Isolate" and sent the followup loop hunting
    for evidence about "Assess"."""
    query = (
        "Conduct a systematic evaluation comparing single-agent and multi-agent architectures.\n"
        "Must cover: OpenAI Agents; Anthropic Claude-based agents; LangGraph; AutoGen; CrewAI\n"
        "Constraints: Assess reliability. Isolate architecture from compute scaling."
    )
    found = named_systems(query)
    assert "Anthropic" in found
    assert {"LangGraph", "AutoGen", "CrewAI"} <= set(found)
    assert "Assess" not in found
    assert "Isolate" not in found
    assert "Conduct" not in found


def test_named_systems_keeps_a_name_that_opens_the_question():
    """The filter must not punish a name for starting the sentence when the
    name carries its own signal (inner caps / acronym)."""
    assert {"LangGraph", "AutoGen"} <= set(
        named_systems("LangGraph vs AutoGen: which handles long-horizon state better?")
    )
    assert {"GDPR", "CCPA"} <= set(named_systems(POLICY_Q))


def test_decision_rule_no_orphaned_verify_header():
    """Same bug as the "Act on these" header, for the weak/partial branch:
    a slot can be "weak" while its label is filtered out entirely by
    _is_slot_label_leak, leaving "Verify before acting — evidence is
    indirect or single-sourced:" printed with no bullets under it (real
    memo output)."""
    critic = {
        "coverage": {
            "slots": [
                {"id": "constraints_and_limitations", "label": "constraints_and_limitations", "status": "weak"},
            ]
        }
    }
    rule = decision_rule_for(POLICY_Q, [{"n": 1}], critic)
    assert "Verify before acting" not in rule


def test_decision_rule_no_orphaned_do_not_assume_header():
    critic = {
        "coverage": {
            "slots": [
                {"id": "constraints_and_limitations", "label": "constraints_and_limitations", "status": "open"},
            ]
        }
    }
    rule = decision_rule_for(POLICY_Q, [{"n": 1}], critic)
    assert "Do not assume" not in rule


def test_flip_condition_truncates_at_a_word_boundary():
    long_goal = "Compare full-parameter fine-tuning, LoRA, and QLoRA across performance metrics, computational cost, memory footprint, and domain generalization"
    text = flip_condition_for(long_goal, {})
    assert "footprint, an”" not in text
    assert "…”" in text


def test_secondary_host_demoted():
    assert is_secondary_host("https://pub.towardsai.net/foo")
    tier, score = credibility_score("https://medium.com/@x/serving-lora")
    assert tier == SourceTier.NEWS_ANALYSIS
    assert score <= 0.40
    assert tier_for("https://github.com/predibase/lorax") == SourceTier.SPECIALIST_RESEARCH
    assert (
        claim_confidence({"url": "https://medium.com/x", "tier": "news_analysis", "credibility": 0.4, "quote": "lora"})
        < 0.7
    )


def test_plan_decomposes_any_mechanism_question():
    plan = heuristic_plan(BIOLOGY_Q, 12, live_first=True)
    assert AgentName.DOCS not in plan.agents_to_run
    assert len(plan.sub_queries) >= 2
    questions = " ".join(s.question for s in plan.sub_queries).lower()
    assert "crispr" in questions
    assert "sgmv" not in questions
    assert decompose_subquestions(POLICY_Q)
