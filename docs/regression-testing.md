# Regression Testing Setup

## Quick Start

### Install Git Hook (Recommended)

```bash
# From workspace root
chmod +x scripts/pre-commit.sh
cp scripts/pre-commit.sh .git/hooks/pre-commit
```

This runs fast regression check (3 test cases) before each commit.

### Manual Testing

```bash
# Fast check (3 cases, ~30 seconds)
cd apps/agent
python -m app.eval.regression_check --fast

# Full suite (10 cases, ~2 minutes)
python -m app.eval.regression_check --all

# Single case (for debugging)
python -m app.eval.regression_check --single
```

### CI Integration

GitHub Actions automatically runs full regression suite on:
- Pull requests to `main` or `develop`
- Pushes to `main` or `develop`
- Changes to `apps/agent/**`

See `.github/workflows/regression.yml`

## What Gets Tested

Each test case validates:

**Structural Requirements:**
- Required sections present (Executive summary, Key findings, etc.)
- Per-dimension subsections (for comparison questions)
- Composite worked example labeling
- Quantitative findings table (when required)

**Quality Metrics:**
- Coverage percentage (>= threshold)
- Citation stacking (< threshold)
- Source diversity (unique sources)
- Code ratio (< 45%)
- Word count (adaptive to evidence)

**Regression Detection:**
- Coverage drops > 5%
- New structural violations
- Increased citation stacking
- Threshold violations

## Test Cases

See `apps/agent/data/eval/golden_set.json` for all test cases:

1. **comparison_multi_dimension_rag**: Multi-dimension comparison
2. **implementation_specific_agentic_loop**: Specific system implementation
3. **theory_scaling_laws**: Theoretical question with quantitative requirements
4. **out_of_scope_medical**: Out-of-scope detection
5. **sparse_evidence_model_collapse**: Graceful degradation test
6. **benchmark_mmlu_vs_gpqa**: Benchmark comparison
7. **implementation_light_agent_framework**: Multi-system comparison
8. **edge_case_single_word_query**: Edge case handling
9. **folklore_detection_always_better**: Folklore detection
10. **fast_iteration_test**: Smart stopping test

## Adding New Test Cases

Edit `apps/agent/data/eval/golden_set.json`:

```json
{
  "id": "your_test_case",
  "category": "comparison|implementation|theory|benchmark|edge_case",
  "query": "Your test question",
  "rationale": "Why this test is important",
  "expected_structure": {
    "required_sections": [...],
    "min_subsections_detailed_analysis": 3
  },
  "expected_quality": {
    "min_coverage_pct": 65,
    "max_code_ratio": 0.45
  }
}
```

## Troubleshooting

### Hook not running

```bash
# Check if hook is executable
ls -la .git/hooks/pre-commit

# Make executable
chmod +x .git/hooks/pre-commit
```

### Regressions detected

1. Review the specific failures in terminal output
2. Fix the underlying issues
3. Re-run regression check
4. If intentional change, update golden_set.json expectations

### Bypass hook (not recommended)

```bash
git commit --no-verify
```

Only use for urgent hotfixes. CI will still catch regressions.

## Future Enhancements

- [ ] Live pipeline integration (currently uses mock data)
- [ ] Baseline/HEAD git comparison
- [ ] Hallucination rate from trust_bench_e2e
- [ ] Performance benchmarking (latency, cost)
- [ ] Auto-update golden_set from successful runs
