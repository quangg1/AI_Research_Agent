# Reference Memo Analysis: Dimension Pattern Extraction

## Reference Memo Structure (Vietnamese Synthetic Data Report)

### Main Dimensions (## headings):

1. **Tóm tắt điều hành** (Executive Summary)
2. **Nền tảng khái niệm và các cơ chế giải quyết khan hiếm dữ liệu**
   - NOT: "Conceptual Foundation" (generic)
   - YES: "Mechanisms by which synthetic data solves scarcity" (specific!)
   
3. **Kiến trúc tác nhân, pipeline và hệ sinh thái công cụ**
   - NOT: "Methodologies and Frameworks" (textbook)
   - YES: "Agent architectures + tool ecosystem" (paper-specific stack)
   
4. **Đánh giá generalization, robustness, bias, alignment và lỗi tự khuếch đại**
   - NOT: "Evaluation Techniques" (generic)
   - YES: "Generalization, robustness, bias, alignment AND self-amplification errors" (bundled paper concerns!)
   
5. **Bằng chứng thực nghiệm: khi dữ liệu tổng hợp giúp và khi nó gây hại**
   - NOT: "Empirical Results" (academic)
   - YES: "When synthetic helps AND when it harms" (dialectical!)
   
6. **Chiến lược giảm bias, distribution shift và feedback loop**
   - NOT: "Bias Mitigation" (generic ML)
   - YES: "Strategies for bias + distribution shift + feedback loop" (paper-specific triad)
   
7. **Hướng dẫn triển khai và checklist cho miền chuyên biệt**
   - NOT: "Implementation" (generic)
   - YES: "Deployment guidance + checklists for specialized domains" (actionable)
   
8. **Khoảng trống nghiên cứu và các thí nghiệm nên ưu tiên**
   - NOT: "Future Work" (academic filler)
   - YES: "Research gaps AND experiments to prioritize" (prescriptive)

## KEY PATTERN DIFFERENCES vs Current Output

### ❌ Current (Generic Templates):
```
## Detailed analysis
### Measured numbers, benchmarks, latency, or cost
### Scalability: KV-cache, GPU memory bandwidth
### Limits, bottlenecks, and failure modes
```

### ✅ Reference (Paper-Concept-Driven):
```
## [Mechanism Analysis]
### Augmentation có mục tiêu (Targeted augmentation)
   - Van Breugel et al. minority classes + low-density regions
   - Coverage, not just dataset size

### Domain transfer/domain adaptation
   - German law study: grounded QA + difficulty stratification
   - 43.0→55.4 vs simple QA 43.0→35.4

### Rare-event synthesis
   - Tail coverage must be measured separately
   - Model collapse mechanism from Nature 2024
```

## CRITICAL INSIGHTS:

### 1. **Dimensions are MECHANISMS, not TOPICS**
   - NOT: "Performance metrics"
   - YES: "Mechanisms by which X solves Y"

### 2. **Every dimension references SPECIFIC PAPERS**
   - Self-Instruct (Wang et al.)
   - phi-1 "Textbooks Are All You Need"
   - Gowal et al. DDPM adversarial robustness
   - Van Breugel et al. DGE
   - Nature 2024 model collapse
   - German Law 2026 domain adaptation
   - EACL 2026 multilingual classification

### 3. **Quantitative EVERYWHERE with CONDITIONS**
   - "33 điểm tuyệt đối trên Super-NaturalInstructions" [Self-Instruct]
   - "50,6% HumanEval dù chỉ có 1,3B params" [phi-1]
   - "66,10% robust accuracy CIFAR-10 với 100M DDPM samples" [Gowal]
   - "LLaMA LegalMC4 QA 43,0→55,4 WITH difficulty-graded + filtering" [German Law]
   - "LLaMA LegalMC4 QA 43,0→35,4 WITH simple instruction" [German Law negative]

### 4. **Structured Evidence Tables**
   Reference has 3 major tables:
   - **Architecture comparison**: 7 columns × 5 architectures (LLM agents, Simulators, GAN, Diffusion, Programmatic)
   - **Evaluation framework**: Questions × Metrics × Statistical tests × Decision criteria
   - **Empirical evidence**: Study × Domain × Results × Interpretation

### 5. **Dialectical Structure: Helps AND Harms**
   Every major claim has:
   - Positive evidence ("Self-Instruct +33 points")
   - Negative evidence ("Simple QA -7.6 points") 
   - Conditional synthesis ("Helps when grounded/verified")

### 6. **Checklists = Actionable Decision Rules**
   Four separate checklists:
   - Before generating data
   - During generation
   - Before training
   - Evaluation
   - Deployment

## IMPLEMENTATION STRATEGY

### Phase 1: Paper Concept Extraction (NEW MODULE)

Create: `apps/agent/app/domain/paper_concepts.py`

```python
def extract_paper_concepts(papers: list[dict]) -> list[dict]:
    """Extract key technical concepts from top papers.
    
    Scans:
    - Abstract + introduction
    - Section headings (h2/h3)
    - Method names
    - Benchmark/dataset names
    - Metric names with numbers
    
    Returns list of concepts with:
    - concept_name: "FreeAL active verifier"
    - paper_id: citation ID
    - context: surrounding text
    - metrics: extracted numbers + conditions
    - section_type: method/result/limitation
    """
    pass
```

### Phase 2: Evidence-First Dimension Synthesis

Replace `derive_slots` heuristics with:

```python
def synthesize_dimensions_from_evidence(
    query: str,
    scholar_results: list[dict],
    search_results: list[dict]
) -> list[dict]:
    """
    1. Extract concepts from top 5 papers
    2. Cluster into themes
    3. Generate dimension candidates
    4. Filter out generic taxonomy
    5. Validate each has specific papers/metrics
    
    Returns dimensions like:
    - "Multi-Stage Agent Pipelines with Role-Based Generation [2]"
    - "FreeAL Active Curation Mechanisms [1]"
    - "Model Collapse Detection and Prevention [Nature 2024]"
    
    NOT:
    - "Methodologies"
    - "Performance"
    - "Challenges"
    """
    pass
```

### Phase 3: Subsection Structure Template

Each ### subsection should follow:

```markdown
### [Paper-Specific Concept Name]

**Technical definition:** [1 sentence from paper]

**Measured findings:** [Numbers + conditions + benchmark]
- Self-Instruct: +33 points on SuperNI with filtering [8]
- Negative control: -7.6 points without filtering [1]

**Mechanism:** [How/why it works - 2-3 sentences]

**Operational constraints:** [When it applies, when it fails]
```

### Phase 4: Numeric Extraction Enhancement

Strengthen `adversarial.py` to require:

```python
def validate_quantitative_claim(text: str) -> dict:
    has_metric = NUMBER_WITH_UNIT_RE.search(text)  # "50.6%", "33 points"
    has_benchmark = BENCHMARK_RE.search(text)      # "HumanEval", "CIFAR-10"
    has_condition = CONDITION_RE.search(text)      # "with filtering", "1.3B params"
    has_baseline = BASELINE_RE.search(text)        # "vs 78% baseline", "→55.4"
    
    return {
        "valid": all([has_metric, has_benchmark, has_condition]),
        "confidence": "empirical" if has_baseline else "reported"
    }
```

## SUCCESS METRICS

### Before (Current Output Quality):
- Generic dimensions: 85%
- Paper-specific concepts: 15%
- Grounded numbers: 30%
- Metric + condition + baseline: 10%

### After (Target Quality):
- Generic dimensions: 20%
- Paper-specific concepts: 80%
- Grounded numbers: 70%
- Metric + condition + baseline: 60%

## NEXT STEPS

1. ✅ Implement `paper_concepts.py` module
2. ✅ Replace `derive_slots` with evidence-first synthesis
3. ✅ Add paper-specific dimension template to writer prompt
4. ✅ Strengthen numeric validation
5. ✅ Test on same query → Compare dimensions

**Key Insight:** The reference memo is NOT following generic templates. It's extracting concepts FROM papers FIRST, then organizing them into dimensions that reflect what the papers actually discuss!
