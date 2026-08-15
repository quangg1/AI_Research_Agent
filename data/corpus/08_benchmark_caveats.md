# Benchmark caveats: MMLU, SWE-bench, leaderboards

Source: Stanford HELM / LMSYS notes.
URL: https://crfm.stanford.edu/helm/lite/latest/
Published: 2024
Credibility: intergovernmental

MMLU contamination is well documented. A high MMLU score is weak evidence that a model will retrieve your internal runbooks correctly.

SWE-bench measures issue resolution on public GitHub with a specific harness. It is not a proxy for "this model is good at RAG over EUR-Lex" or "this agent loop is production-ready."

Public leaderboards mix prompt templates, sampling, and sometimes undisclosed scaffolding. Compare systems only when the harness is frozen.

Use a domain golden set (forbidden claims, required source tiers, contradiction flags) as the ship gate. Borrow public benches as secondary context.
