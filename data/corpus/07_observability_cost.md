# Traces, cost attribution, and token accounting

Source: LLM observability notes.
URL: https://docs.smith.langchain.com/observability
Published: 2025
Credibility: standard_body

A production research agent needs per-node traces: model, prompt version, token in/out, tool calls, and retrieval ids. LangSmith-style tracing is useful when every graph node emits structured events, not only the final answer.

Cost attribution should split planner / critic / report. A cheap routing model plus an expensive report model is a normal pattern.

Token estimates of len/4 are fine for budgets, not for invoices. Use provider usage metadata when present.

Without durable run events, you cannot debug why a critic looped or why HITL never fired after a restart.
