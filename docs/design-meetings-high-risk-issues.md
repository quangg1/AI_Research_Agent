# Design Meetings: High-Risk Architectural Issues

**Date**: 2026-09-07  
**Status**: Design Phase - Requires Team Discussion  
**Priority**: High (Security, Quality, User Experience)

This document outlines 4 high-risk architectural issues that require design meetings before implementation. Each issue involves significant architectural changes with potential side effects.

---

## Issue #1: Tier-B Dead-End Loop (Audit Feedback)

### Problem Statement
`trust_bench_e2e` runs POST-PUBLISH as optional audit, computing hallucination_rate and logging to `trust_bench_e2e_history.jsonl`. However:
- User receives memo BEFORE audit completes
- No action taken when `NOT_SUPPORTED` claims detected
- No feedback loop: audit results don't trigger memo updates, flags, or user warnings
- Essentially a dashboard metric, not a quality gate

### Current Flow
```
memo_gate (approve) → user receives memo → trust_bench_e2e (audit) → log only
                                                                    └── (dead end)
```

### Proposed Solutions

#### Option A: Async Notification + Memo Flagging
**Flow:**
```
memo_gate → user receives memo → trust_bench_e2e (audit)
                                        ↓ (if hallucination > threshold)
                                   update memo status (add warning banner)
                                   notify user (email/UI badge)
```

**Pros:**
- Non-blocking: User gets memo immediately
- Transparent: User warned if quality issues found post-hoc
- Minimal latency impact

**Cons:**
- User may act on low-quality memo before warning arrives
- Requires memo state updates after publish (DB schema change)
- Notification infrastructure needed

**Implementation:**
1. Add `quality_warning` field to memo schema
2. Update UI to show warning banner when present
3. trust_bench_e2e writes back to memo table on high hallucination
4. Email/notification service for critical warnings

**Estimated complexity**: MEDIUM (DB schema, notification service)

---

#### Option B: Pre-Publish Audit Gate (Sync)
**Flow:**
```
memo_gate → trust_bench_e2e (blocking audit) → if pass: publish to user
                                             → if fail: regenerate or flag
```

**Pros:**
- User never sees low-quality memo
- True "gate" behavior
- Simpler state model (no post-publish updates)

**Cons:**
- +10-30s latency per memo (audit is slow)
- May trigger frequent regenerations (audit is strict)
- Budget impact (each regeneration = full rewrite cost)

**Implementation:**
1. Move trust_bench_e2e before memo_gate approval
2. Add audit results to quality_check logic
3. Define hallucination threshold (suggest: 20% = fail)
4. Trigger regeneration on fail (max 2 attempts like quality gate)

**Estimated complexity**: MEDIUM-HIGH (pipeline reordering, latency optimization)

---

#### Option C: Tiered Audit (Fast + Slow)
**Flow:**
```
memo_gate → fast_audit (5 critical claims, 3s) → if suspicious: full_audit
         → if clean: publish → background full_audit → post-flag if needed
```

**Pros:**
- Best of both: fast path for clean memos, deep check when needed
- Minimal latency for majority case
- Still catches high-risk issues before publish

**Cons:**
- Most complex to implement
- Requires "critical claims" selection heuristic
- Two audit codepaths to maintain

**Implementation:**
1. Implement fast_audit: Sample 5 highest-confidence claims
2. Full audit only if fast_audit flags issues or random sample (10%)
3. Combine with Option A for background full audit

**Estimated complexity**: HIGH (two audit systems, sampling logic)

---

### Recommendation
**Start with Option A** (Async notification + flagging):
- Lowest implementation risk
- Preserves current latency
- Provides user value (transparency)
- Can upgrade to Option C later if audit speed improves

**Design Questions for Team:**
1. What hallucination_rate threshold should trigger warnings? (Suggest: >15% = warning, >25% = critical)
2. Should we block PRs/production deploy on high historical hallucination rate?
3. UI: How to show warning without alarming users unnecessarily?
4. Notification: Email? In-app? Dashboard only?

---

## Issue #4: Tier-C Syntactic vs Semantic Validation

### Problem Statement
Current Tier-C gates (`structure_validation.py`) check syntactic structure:
- Worked example cites 2 sources → check for `> **Composite**` label
- Detailed analysis → check for `###` headings
- Quantitative findings → check for table rows

But LLM can game these checks:
- Add `> **Composite**` to every worked example to pass check
- Create `### X` headings with empty/irrelevant content
- Insert meaningless numbers into quantitative table

**Goodhart's Law**: "When a measure becomes a target, it ceases to be a good measure."

### Current Validation (Syntactic)
```python
# check_worked_example_compositing
if citation_count >= 2 and not has_composite_label:
    violation = True

# check_per_dimension_subsections
if not any("retrieval strategies" in s.lower() for s in subsections):
    violation = True
```

LLM can trivially pass by adding markers without semantic correctness.

### Proposed Solutions

#### Option A: Semantic Similarity Checks (Embedding-Based)
**Approach:**
- For worked example: Verify all steps semantically cohere (same system/method)
- For per-dimension: Check subsection content actually discusses that dimension
- For quantitative: Verify numbers have associated metrics and conditions

**Implementation:**
```python
# Pseudo-code
def check_worked_example_semantic(memo_markdown, citations):
    example_section = extract_worked_example(memo_markdown)
    steps = split_into_steps(example_section)
    
    # Get embeddings for each step
    embeddings = [embed(step) for step in steps]
    
    # Check coherence (cosine similarity between consecutive steps)
    for i in range(len(embeddings) - 1):
        similarity = cosine_similarity(embeddings[i], embeddings[i+1])
        if similarity < 0.70:  # Low coherence = likely composited
            return {"violation": True, "reason": "Steps not coherent"}
    
    # Check against cited sources
    for citation in get_citations(example_section):
        source_embedding = embed(get_source_content(citation))
        if not any(cosine_similarity(step_emb, source_embedding) > 0.60 
                   for step_emb in embeddings):
            return {"violation": True, "reason": "Step not grounded in source"}
    
    return {"violation": False}
```

**Pros:**
- Harder to game (requires semantic coherence, not just markers)
- Catches actual quality issues (empty headings, unrelated content)
- ML-based: Improves as embeddings improve

**Cons:**
- Embedding API calls (cost: ~$0.001 per memo, latency: +500ms)
- Threshold tuning required (false positives if too strict)
- Harder to debug (why did similarity score fail?)

**Estimated complexity**: MEDIUM (embedding integration, threshold calibration)

---

#### Option D: LLM-as-Judge Verification
**Approach:**
- Use separate LLM to judge if writer followed rules
- "Does this worked example composite multiple sources without labeling?"
- "Does this ### subsection actually discuss retrieval strategies?"

**Implementation:**
```python
def llm_verify_compositing(worked_example, citations):
    prompt = f"""
    Worked example:
    {worked_example}
    
    Citations: {citations}
    
    Question: Does this worked example describe a unified workflow by combining
    steps from multiple different sources ([5], [6], [7]) without explicitly
    marking it as Composite?
    
    Answer with: YES (violation) or NO (compliant)
    Reasoning: <brief explanation>
    """
    
    response = llm_judge.complete(prompt, temperature=0)
    return parse_yes_no(response)
```

**Pros:**
- Can handle nuanced semantic checks
- No embedding tuning needed
- Can provide explanations (helps debugging)

**Cons:**
- LLM cost (~$0.005 per check)
- Latency (+1-2s per check)
- LLM-as-judge has own reliability issues (needs validation)
- Circular: Using LLM to check LLM

**Estimated complexity**: MEDIUM (LLM integration, prompt engineering)

---

#### Option C: Hybrid (Syntactic + Spot-Check Semantic)
**Approach:**
- Keep current fast syntactic checks for all memos
- Random sample (10-20%) gets full semantic validation
- Flag patterns: If source keeps gaming checks, escalate to semantic

**Flow:**
```
memo_gate → syntactic_check (fast, all memos)
         → if suspicious OR random_sample(20%): semantic_check
         → if semantic fail: regenerate
```

**Pros:**
- Fast path unchanged (no latency/cost for most memos)
- Catches gaming over time (spot checks deter bad behavior)
- Cost-effective ($0.001 per memo on average)

**Cons:**
- Doesn't catch all gaming immediately
- Requires tracking "suspicious patterns" (more state)
- Semantic check still needs embedding or LLM infra

**Estimated complexity**: MEDIUM-HIGH (hybrid logic, pattern tracking)

---

### Recommendation
**Start with monitoring current violations** (no immediate fix):
1. Log all structural violations to see gaming frequency
2. Manual review: Are violations legitimate or gaming?
3. If gaming rate < 5%, current syntactic checks sufficient
4. If gaming rate > 10%, implement **Option C** (hybrid with 20% spot-check)

**Design Questions for Team:**
1. What's acceptable false positive rate for semantic checks? (Suggest: <2%)
2. Embedding vs LLM-as-judge: Which fits our infra better?
3. Should we pre-announce semantic checks to LLM (in prompt) as deterrent?
4. Cost budget: $0.001/memo (embedding) vs $0.005/memo (LLM judge)?

---

## Issue #5: Planner Self-Grading Circularity

### Problem Statement
Current architecture:
1. **Planner** (LLM) writes `must_answer` slots: "To answer this question, I must find X, Y, Z"
2. **Retrieval** searches for evidence to fill slots
3. **Critic** (hard gate) checks: "Are slots X, Y, Z covered?"
4. If ≥65% coverage → "sufficient"

But **planner defines what "sufficient" means**. If planner writes easy/vague slots, critic will pass even if user question not deeply answered.

**Example:**
- User: "How do different RAG retrieval strategies, embedding models, reranking methods affect accuracy?"
- Planner (lazy): "Must answer: (1) What is RAG, (2) What are retrieval strategies"
- Retrieval: Finds 2 generic papers
- Critic: 100% coverage! (both slots covered)
- User: Receives surface-level memo that doesn't compare embedding models or reranking

**Root issue**: LLM defines its own success criteria.

### Current Flow
```
User query → Planner (LLM defines slots) → Critic (validates slots)
                    ↓                              ↓
            "circular dependency"          "passes easily"
```

### Proposed Solutions

#### Option A: Human-Calibrated Slot Templates
**Approach:**
- Define slot templates for common question types
- Planner selects template + fills parameters, not freeform

**Example:**
```python
COMPARISON_TEMPLATE = [
    {"id": "define_X", "label": "Define {dimension_1}"},
    {"id": "define_Y", "label": "Define {dimension_2}"},
    {"id": "compare_X_Y", "label": "Compare {dimension_1} vs {dimension_2} on {metric_1}"},
    {"id": "compare_X_Y_2", "label": "Compare {dimension_1} vs {dimension_2} on {metric_2}"},
    {"id": "quantitative", "label": "Find quantitative benchmarks"},
]

# Planner fills:
planner_output = {
    "template": "comparison",
    "dimension_1": "BERT embeddings",
    "dimension_2": "Ada-002 embeddings",
    "metric_1": "retrieval accuracy",
    "metric_2": "latency",
}

# Slots auto-generated from template (not LLM freeform)
```

**Pros:**
- Breaks circularity: Slots are human-designed, not LLM-designed
- Consistent quality: Same question type → same slot rigor
- Easier to calibrate thresholds (compare templates, not individual plans)

**Cons:**
- Reduces flexibility: Novel questions may not fit templates
- Template maintenance: Need to design for many question types
- Planner still chooses template (could pick easiest one)

**Estimated complexity**: MEDIUM (template design, fallback for novel questions)

---

#### Option B: External Ground Truth Validation
**Approach:**
- After critic passes, sample user queries
- Human reviewers rate: "Did memo actually answer the question?"
- If systematic mismatch (critic says 75%, human says 40%), recalibrate

**Flow:**
```
Critic: 72% coverage (passes) → Publish → Sample 10% for human review
                                        → If human disagrees: Adjust planner/critic logic
```

**Pros:**
- Ground truth: Humans define "sufficient", not LLM
- Calibration over time: Improves as we collect data
- Doesn't block pipeline (sampling is async)

**Cons:**
- Requires human review infrastructure ($cost + time)
- Slow feedback loop (weeks to collect statistically significant sample)
- Doesn't prevent individual bad memos (only calibrates long-term)

**Estimated complexity**: MEDIUM (human review tooling, sampling infra)

---

#### Option C: Adversarial Slot Validation (LLM Red Team)
**Approach:**
- After planner writes slots, second LLM (red team) tries to find gaps
- "Can this question be partially answered while still hitting these slots?"
- If red team finds easy path to 100% coverage, slots are too weak

**Flow:**
```
Planner writes slots → Red Team LLM: "Are these sufficient?"
                    → If Red Team says "No, missing X": Add slot for X
                    → Iterate until Red Team satisfied
```

**Pros:**
- Breaks circularity: Second LLM validates first LLM's plan
- Automated: No human review needed
- Catches weak plans before retrieval starts

**Cons:**
- LLM-judging-LLM (own reliability issues)
- Cost: 2x LLM calls for planning
- Latency: +2-3s for red team validation

**Estimated complexity**: MEDIUM-HIGH (red team prompt engineering, iteration logic)

---

### Recommendation
**Hybrid: Option A (templates) + Option B (sampling)**:
1. Implement slot templates for top 5 question types (comparison, implementation, theory, benchmark, debugging)
2. Planner uses template when match > 80% confidence, freeform otherwise
3. Sample 10% of published memos for human review (async)
4. Use human feedback to refine templates quarterly

**Design Questions for Team:**
1. What question types are most common? (Need template for each)
2. Who owns human review? (Research team? Customer success?)
3. How to handle novel questions that don't fit templates?
4. Should we show planner slots to user for transparency?

---

## Issue #9: HITL Timeout (Interrupt Hang Risk)

### Problem Statement
3 interrupt points (`plan_gate`, `hitl`, `memo_gate`) use LangGraph `interrupt()`. State saved to Postgres checkpointer. No TTL → run can hang indefinitely if user abandons.

**Resource leak scenario:**
1. User starts research, reaches `plan_gate` interrupt
2. User closes browser, never approves
3. Run state persists in DB indefinitely (thread_id never cleared)
4. Postgres checkpoint table grows unbounded
5. Dashboard shows "pending approval" forever

Not a cost issue (no compute running), but **UX + operational issue**:
- User confusion ("why is my old run still pending?")
- DB bloat (old checkpoints never cleaned)
- Metrics skew (active runs includes abandoned)

### Current Flow
```
plan_gate → interrupt() → wait forever...
                       → (no timeout, no auto-cancel)
```

### Proposed Solutions

#### Option A: Hard Timeout + Auto-Cancel
**Approach:**
- Set TTL on interrupts (e.g., 24 hours)
- Background job checks: if interrupt > TTL, auto-cancel run
- Notify user: "Your research expired, please restart"

**Implementation:**
```python
# In interrupt metadata
interrupt_time = datetime.now()
ttl_hours = 24

# Background job (cron every hour)
def cleanup_stale_interrupts():
    stale = db.query("""
        SELECT thread_id FROM checkpoints
        WHERE status = 'interrupted'
        AND interrupted_at < NOW() - INTERVAL '{ttl_hours} hours'
    """)
    
    for thread_id in stale:
        cancel_run(thread_id)
        notify_user(thread_id, "Research expired, please restart")
```

**Pros:**
- Prevents indefinite hangs
- Cleans up DB automatically
- Clear user expectation (24h window to approve)

**Cons:**
- User may lose work if they return after 24h
- Need notification system (email/dashboard)
- Arbitrary timeout (why 24h not 48h?)

**Estimated complexity**: LOW-MEDIUM (background job, notifications)

---

#### Option B: Soft Timeout + Reminder
**Approach:**
- No hard cancel, but send reminders
- After 2 hours: "You have pending research approval"
- After 24 hours: "Research will be archived soon"
- After 7 days: Move to "archived" tab (not deleted, but hidden)

**Implementation:**
```python
# Email reminders (non-blocking)
if interrupt_age > 2_hours and not reminded:
    send_email(user, "Pending approval: [query]")
    mark_reminded(thread_id)

# UI state changes (not deleted)
if interrupt_age > 7_days:
    update_status(thread_id, "archived")
    # User can manually "resume" from archived tab
```

**Pros:**
- No data loss (user can resume anytime)
- Gentle UX (reminders, not hard cancel)
- Archived runs out of main view but recoverable

**Cons:**
- DB still grows (archived runs kept forever)
- May need periodic hard cleanup (e.g., delete after 90 days)
- More complex state model (active/archived/deleted)

**Estimated complexity**: MEDIUM (reminder logic, archived state, UI changes)

---

#### Option C: Grace Period + Resume Token
**Approach:**
- After 24h, mark run as "expired"
- User gets "resume token" (saves plan + slots)
- Can restart with token → skips planning, goes straight to retrieval
- Original checkpoint deleted after expiry

**Flow:**
```
Interrupt > 24h → Generate resume token → Delete checkpoint
               → User returns with token → Restore plan/slots → Continue from retrieval
```

**Pros:**
- Cleans DB (checkpoints deleted)
- User can resume without losing plan
- Balances cleanup + UX

**Cons:**
- Resume token infra (token generation, storage, validation)
- Potential security issue (token hijacking)
- UX complexity ("what's a resume token?")

**Estimated complexity**: MEDIUM-HIGH (token system, security, UX)

---

### Recommendation
**Start with Option A** (hard timeout + auto-cancel, 48h window):
- 48h is generous (covers weekend + workday)
- Clear, predictable behavior
- Operational benefit (DB cleanup)
- Notification softens UX impact

**Design Questions for Team:**
1. What's reasonable timeout? (Suggest: 48h weekday, 72h weekend)
2. Should we allow users to extend timeout? ("Keep alive" button)
3. Email vs in-app notification? Both?
4. After auto-cancel, can user "resume" with same query? (Start fresh vs recover state)

---

## Implementation Priority

### Immediate (Next Sprint)
- **Issue #9**: HITL timeout (LOW-MEDIUM complexity, operational priority)

### Short-term (Next Quarter)
- **Issue #1**: Tier-B feedback (MEDIUM complexity, user value)
- **Issue #5**: Planner slot templates (MEDIUM complexity, quality improvement)

### Long-term (Backlog)
- **Issue #4**: Semantic validation (wait for gaming evidence first)

---

## Design Meeting Agenda

1. **Review each issue** (15 min per issue = 60 min total)
   - Confirm problem statement
   - Discuss proposed solutions
   - Answer design questions

2. **Prioritize** (15 min)
   - Which issue impacts users most?
   - Which has best ROI (impact / complexity)?
   - Dependencies between issues?

3. **Assign owners** (10 min)
   - Who leads design for each?
   - Who implements?
   - Timeline expectations?

4. **Next steps** (5 min)
   - When to reconvene?
   - What prototypes needed?
   - User research required?

---

## Appendix: Risk Matrix

| Issue | Impact | Complexity | User Visible | Breaking Change |
|-------|---------|-----------|--------------|-----------------|
| #1 (Audit) | HIGH (quality) | MEDIUM | YES (warnings) | NO |
| #4 (Semantic) | MEDIUM (gaming) | MEDIUM-HIGH | NO | NO |
| #5 (Planner) | HIGH (quality) | MEDIUM | NO | MAYBE (slots) |
| #9 (Timeout) | LOW (UX) | LOW-MEDIUM | YES (expiry msg) | MAYBE (cancel) |

**Legend:**
- Impact: How much this improves the system
- Complexity: Engineering effort
- User Visible: Does user see changes directly?
- Breaking Change: Requires migration or changes existing behavior?
