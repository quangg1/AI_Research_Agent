# Why Quantitative Findings Can't Be 100% Populated

## Extraction Pipeline

`apps/agent/app/domain/adversarial.py::extract_quantitative_rows()`

### Multi-Layer Filtering (Intentionally Conservative)

1. **Regex Extraction** (`QUANT_RE`, lines 64-73)
   - Matches: %, ms, FLOP, tokens/s, × speedup, etc.
   - Initial capture of candidate numbers

2. **Setup Parameter Filter** (`_is_setup_parameter`, lines 566-595)
   - **DROPS**: "N studies", "N runs", "N tokens" (config counts)
   - **KEEPS**: Numbers with outcome units (%, ms, FLOP, tok/s)
   - **Purpose**: Prevent padding table with experiment setup metadata

3. **Semantic Gate** (`_has_valid_metric_and_condition`, lines 621-679)
   - **REQUIRES BOTH**:
     - Metric/unit name (accuracy, latency, throughput)
     - Experimental condition (dataset, benchmark, baseline, vs.)
   - **DROPS**: Bare percentages like "16%" without context
   - **DROPS**: "In experiments, latency was 50ms" (generic condition)

4. **Final Condition Check** (line 541-542 in `extract_quantitative_rows`)
   - If condition is still "condition not stated in excerpt" → DROP
   - Even if passed semantic gate, must have explicit condition

5. **Results Section Bias** (`results_section_blob`, lines 86-93)
   - Preferentially extracts from Results/Findings/Evaluation sections
   - Numbers in abstract/intro might be missed
   - GitHub READMEs and blogs often lack formal Results sections

## Why Numbers Are Missed (By Design)

| Scenario | Example | Why Filtered | Correct? |
|----------|---------|--------------|----------|
| Bare percentage | "RAG improves accuracy by 25%" | No benchmark named | ✅ YES - unconditioned claim |
| Generic setup | "Evaluated on 100 tasks" | Setup parameter, not outcome | ✅ YES - config count |
| Qualitative only | "significantly faster" | No numeric value | ✅ YES - not quantitative |
| Missing condition | "Latency: 50ms" | No benchmark/dataset context | ✅ YES - can't verify |
| Abstract-only number | "Achieves 92% on MMLU" in intro | Outside Results section | ⚠️ MAYBE - could improve |
| Relative without baseline | "+20% improvement" | No baseline value stated | ✅ YES - incomplete comparison |

## Why This Is CORRECT

User's question specifically asked about:
- "factual accuracy, retrieval quality, latency, and overall system performance"

These ARE quantitative metrics that SHOULD have numbers. However:

### If Retrieved Evidence Has:
1. **No Results sections** (GitHub READMEs, blog posts, docs) → No quantitative data
2. **Qualitative descriptions** ("RAG reduces hallucinations significantly") → Not quantitative
3. **Unconditioned numbers** ("25% improvement" without benchmark) → Filtered correctly

Then **Quantitative findings SHOULD be empty/sparse**, because:
- Including unconditioned numbers would be WORSE than admitting we don't have data
- User previously complained about "filler rows" and "not reported" scaffolding
- Semantic gates prevent table pollution with setup parameters

## Why Can't It Be 100%?

### Structural Reasons:
1. **Source Diversity**: Web/GitHub sources often lack formal Results sections
2. **Conservative by Design**: Prefers empty table over incorrect/unconditioned numbers
3. **Semantic Rigor**: Requires metric+condition, filters bare percentages
4. **Coverage Priority**: Retrieval optimizes for conceptual coverage, not benchmark tables

### The Trade-off:
- **Relaxing filters** → More rows, but polluted with setup params and bare numbers
- **Current strict filters** → Sparse table, but every row is grounded and conditioned

## User's Memo Example

Question: "How do different retrieval strategies, embedding models, reranking methods affect factual accuracy, retrieval quality, latency, performance?"

Missing Quantitative findings likely because:
1. Retrieved sources discuss RAG mechanisms qualitatively
2. GitHub repos show implementation, not benchmark results
3. Numbers present but lack clear experimental conditions
4. Comparison questions often get conceptual papers, not empirical benchmarks

## Recommendation

**DO NOT change extraction logic.** Current conservatism is correct.

**Instead, improve UPSTREAM**:
1. Domain balancing (already fixed) reduces GitHub repo bias
2. Enhanced followup queries (already fixed) targets benchmark papers
3. Per-dimension subsection enforcement (already fixed) ensures comparison depth

If evidence still lacks quantitative data after these fixes, it's because:
- The question is conceptual (how does X work?) not empirical (what is X's performance?)
- OR field lacks published benchmarks for these specific comparisons
