# Test Plan: Verify Quality Improvements

## Original Query (From User)
"synthetic data + data-generation agents for specialized AI"

## Success Criteria

### 1. No Duplicate Quotes Across Sections
**Before**: Same quote appeared in multiple unrelated dimensions
**After**: Each dimension should have unique, relevant quotes

**Test**: Check if Latency section quotes different papers than Accuracy section

### 2. Numeric Data Has Context
**Before**: Bare "16%", "1970s" without explanation
**After**: Every number has metric + experimental condition

**Test**: Check quantitative table - every number should have benchmark/dataset

### 3. No Generic Filler
**Before**: "is carried by the collected sources"
**After**: Real synthesis from evidence

**Test**: Search memo for filler phrases

### 4. Quality Regeneration (Not Search)
**Before**: Quality issues → trigger new web search
**After**: Quality issues → rewrite from existing notes

**Test**: Check traces - should see "quality_regenerate" → report, not → search

## How to Run Integration Test

### Step 1: Set Up Environment
```bash
# In your local D:\AI_Research_Agent\
cp .env.example .env
# Fill in your API keys:
# - GOOGLE_API_KEY (from aistudio.google.com)
# - TAVILY_API_KEY (from tavily.com)
```

### Step 2: Start Services
```bash
# Start Postgres + Redis + Qdrant
docker-compose up -d

# Verify services are up
docker-compose ps
```

### Step 3: Run Research Query
```bash
cd apps/agent

# Run the original query
python -c "
from app.graph.builder import build_test_graph
from app.graph.state import ResearchState

graph = build_test_graph(enable_hitl=False)

query = 'synthetic data generation and data-generation agents for specialized AI model training'

result = graph.invoke(
    ResearchState(
        query=query,
        thread_id='test_quality_fix',
    )
)

# Save report
report = result.get('report')
if report:
    with open('test_memo_quality_fix.md', 'w') as f:
        f.write(report.get('body_markdown', ''))
    print('✅ Report saved to test_memo_quality_fix.md')
    print(f'Sources: {len(result.get(\"retrieved\", []))}')
    print(f'Dimensions: {len((result.get(\"critic\", {}).get(\"coverage\", {}).get(\"slots\", [])))}')
"
```

### Step 4: Manually Review Output
Check `test_memo_quality_fix.md` for:

1. **Duplicate Quotes**: 
   - Search for the same quote text appearing multiple times
   - Each section should cite different evidence

2. **Numeric Quality**:
   - Find the "## Quantitative findings" table
   - Every number should have benchmark/dataset/condition
   - No bare "X%" without context

3. **No Filler**:
   - Search for "is carried by"
   - Search for "TODO", "REVISIT IF", "[?]"

4. **Synthesis Quality**:
   - Each section should synthesize findings, not just list quotes
   - Should see mechanism explanations, not quote dumps

### Step 5: Compare with Baseline
If you have the OLD buggy memo (Doc 1), compare:
- Duplicate quote ratio: should be much lower
- Numeric table quality: should have more context
- Section synthesis: should be deeper

## Expected Improvements

| Metric | Before | After |
|--------|--------|-------|
| Duplicate quote ratio | ~60% | <40% |
| Numbers with context | ~30% | >80% |
| Filler phrases | Present | None |
| Quality regeneration | New search | Rewrite only |
| Per-dimension sources | Shared pool | Unique per dim |

## If Test Fails

### Problem 1: Still duplicate quotes
**Check**: Did retrieve_node actually run per-dimension?
```bash
# Look for trace
grep "per_dimension.*true" test_traces.json
```

### Problem 2: Numeric gate too strict
**Fix**: May need to loosen condition regex in `adversarial.py`

### Problem 3: Quality regeneration loops forever
**Check**: Is quality_gate_issues being cleared after rewrite?

### Problem 4: Embeddings failing
**Check**: Is text-embedding-004 available?
```python
from app.retrieval.embed import embed_texts
embed_texts(["test"])  # Should not error
```

## Next Steps After Validation

✅ If ALL criteria pass → Merge PR, deploy  
⚠️ If SOME fail → Iterate on specific issues  
❌ If MOST fail → Revisit approach  
