# Agent loops vs linear pipelines

Source: LangGraph / agent orchestration notes.
URL: https://langchain-ai.github.io/langgraph/concepts/low_level/
Published: 2025
Credibility: standard_body

A research agent that always runs search → scholar → docs → report is a pipeline, even if it is drawn as a graph. Real agent loops need conditional edges, critic loop-back, budget, interrupts, and durable checkpoints.

LangGraph is useful when state must survive HITL pauses and when the critic can send the planner new sub-queries. If those edges never fire, the framework is ornamental.

Single-agent-with-tools often beats a swarm of specialists on the same tool budget. Extra agents add coordination cost and duplicated context.

HITL interrupts belong before expensive search and before a user-facing memo, not after the model has already spent the token budget.
