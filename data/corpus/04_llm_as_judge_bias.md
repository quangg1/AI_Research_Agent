# LLM-as-judge is not unbiased ground truth

Source: Evaluation methodology notes.
URL: https://arxiv.org/abs/2306.05685
Published: 2024
Credibility: intergovernmental

LLM-as-judge correlates with human preference labels and is cheap to scale. It is not ground truth. Judges inherit position bias, verbosity bias, and self-preference when the judge family matches the candidate.

HELM-style reporting separates scenario, metric, and contamination status. A single "arena score" hides task mixture and prompt sensitivity.

Golden sets with human labels remain the regression gate for product decisions. Use LLM judges as a noisy screen, then sample for human review.

If the judge prompt and the candidate share a provider, report that conflict. Do not claim the eval is unbiased.
